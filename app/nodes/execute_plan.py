import time
from langsmith import traceable

from app.core.logging import bind_log_context, get_logger
from app.core.settings import get_settings
from app.graph_state import GraphState
from app.llm.openai_wrapper import AsyncOpenAIWrapper
from app.prompts.response_drafting_prompts import (
    build_response_drafting_system_prompt,
    build_response_drafting_user_prompt,
)
from app.schemas import PlanStep, ResponseDrafting
from app.services.retrieval_service import retrieve_relevant_documents

logger = get_logger(__name__)


# Helper: return a new step marked as completed.
# We keep step updates immutable-like by creating a fresh PlanStep
# instead of mutating the existing object in place.
def _mark_step_completed(step: PlanStep, result: str | None = None) -> PlanStep:
    return PlanStep(
        step_id=step.step_id,
        title=step.title,
        description=step.description,
        owner=step.owner,
        status="completed",
        requires_human_approval=step.requires_human_approval,
        result=result,
        error=None,
    )


# Helper: return a new step marked as failed.
# The error message is stored on the step so the graph can later inspect
# exactly which execution step failed and why.
def _mark_step_failed(step: PlanStep, error: str) -> PlanStep:
    return PlanStep(
        step_id=step.step_id,
        title=step.title,
        description=step.description,
        owner=step.owner,
        status="failed",
        requires_human_approval=step.requires_human_approval,
        result=None,
        error=error,
    )


# Helper: keep a step pending.
# This is especially useful for "human" steps, because execute_plan_node
# should not execute human review itself — it only leaves that step ready
# for the downstream human_review node.
def _mark_step_pending(step: PlanStep) -> PlanStep:
    return PlanStep(
        step_id=step.step_id,
        title=step.title,
        description=step.description,
        owner=step.owner,
        status="pending",
        requires_human_approval=step.requires_human_approval,
        result=step.result,
        error=step.error,
    )


# Build a simple retrieval query from:
# - the original customer message
# - the planner's step title/description
# - key triage signals
#
# This is the bridge between planning and retrieval:
# the planner decides that retrieval is needed,
# and the executor turns that decision into an actual retrieval query.
def _build_retrieval_query(state: GraphState, step: PlanStep) -> str:
    triage = state.triage_result
    ticket = state.initial_ticket.customer_message

    parts = [ticket, step.title, step.description]

    if triage:
        parts.extend(
            [
                triage.issue_category,
                triage.intent,
                triage.reasoning_summary,
            ]
        )

    return " ".join(part for part in parts if part)


# After executing the plan, determine which step should be considered
# the next actionable step in the workflow.
# Usually this will be the human review step if one remains pending.
def _get_next_pending_step_id(plan: list[PlanStep]) -> str | None:
    for step in plan:
        if step.status == "pending":
            return step.step_id
    return None


# Execute one retrieval step.
#
# v1 approach:
# - build a simple keyword-style query
# - retrieve local KB documents
# - store them in state.retrieved_documents
# - mark the step as completed
#
# The planner does not retrieve documents itself.
# It only expresses that retrieval is needed.
# This function is where that plan becomes action.
async def _execute_retrieval_step(state: GraphState, step: PlanStep) -> tuple[PlanStep, int]:
    query = _build_retrieval_query(state, step)
    documents = retrieve_relevant_documents(query=query, max_documents=3)

    # Persist retrieved evidence into graph state for downstream nodes:
    # response drafting and guardrails.
    state.retrieved_documents = documents

    result_summary = f"Retrieved {len(documents)} document(s)."
    updated_step = _mark_step_completed(step, result=result_summary)

    return updated_step, len(documents)


# Execute one response drafting step.
#
# Requirements:
# - triage_result must already exist
# - retrieval may or may not have produced documents
#
# This function uses the retrieved context plus triage information
# to ask the LLM for a structured ResponseDrafting output.
async def _execute_response_step(state: GraphState, step: PlanStep) -> PlanStep:
    # Drafting without triage would mean missing important business signals
    # such as urgency, tone, escalation need, and risk level.
    if state.triage_result is None:
        return _mark_step_failed(step, "Missing triage_result for response drafting.")

    settings = get_settings()
    drafting_model = getattr(
        settings,
        "openai_model_response_drafting",
        settings.openai_model_planner,
    )

    llm = AsyncOpenAIWrapper(
        default_model=drafting_model,
        default_temperature=0.0,
    )

    system_prompt = build_response_drafting_system_prompt()
    user_prompt = build_response_drafting_user_prompt(
        ticket=state.initial_ticket,
        triage_result=state.triage_result,
        retrieved_documents=state.retrieved_documents or [],
    )

    result = await llm.generate_structured(
        system_prompt=system_prompt,
        prompt=user_prompt,
        response_schema=ResponseDrafting,
    )

    parsed = result.parsed
    if parsed is None or not isinstance(parsed, ResponseDrafting):
        return _mark_step_failed(step, "Response drafting returned invalid structured output.")

    # Persist the drafted response into state.
    # This is the main artifact that the guardrails node will validate next.
    state.response_draft = parsed

    # Store execution metadata for observability/debugging.
    state.additional_metadata["response_drafting"] = {
        "request_id": state.request_id,
        "model_name": result.model_name,
        "latency_ms": result.latency_ms,
        "attempts": result.attempts,
        "used_documents": len(parsed.related_documents),
    }

    return _mark_step_completed(step, result="Drafted grounded customer response.")


# Main executor node.
#
# Responsibilities:
# - read the planner-produced plan
# - execute supported step types (retrieval / response drafting)
# - leave human steps pending
# - update step statuses
# - write execution artifacts into state
# - record observability metadata
#
# In other words:
# planner -> decides WHAT should happen
# execute_plan -> actually DOES it
@traceable(run_type="chain", name="execute_plan_node")
async def execute_plan_node(state: GraphState) -> GraphState:
    started = time.perf_counter()
    request_id = state.request_id

    logger.info(
        "execute_plan.started",
        extra=bind_log_context(
            request_id=request_id,
            node_name="execute_plan",
        ),
    )

    # Fail fast if the planner did not produce a usable plan.
    # This protects the workflow from executing on incomplete state.
    if state.agent_state is None or not state.agent_state.plan:
        state.workflow_outcome = "blocked"
        state.additional_metadata["execute_plan_error"] = {
            "request_id": request_id,
            "error_type": "MissingPlan",
            "message": "execute_plan_node called without an executable plan.",
        }
        return state

    updated_plan: list[PlanStep] = []
    retrieval_count = 0

    # Walk through the plan in order and execute the steps the executor owns.
    for step in state.agent_state.plan:
        # Retrieval step: fetch local KB context and mark the step completed.
        if step.owner == "retrieval_agent" and step.status == "pending":
            try:
                updated_step, docs_count = await _execute_retrieval_step(state, step)
                retrieval_count += docs_count
                updated_plan.append(updated_step)
            except Exception as exc:
                # Execution should be resilient:
                # one failed step should not crash the whole workflow.
                updated_plan.append(_mark_step_failed(step, str(exc)))

        # Response drafting step: generate a structured draft using
        # triage signals and retrieved documents.
        elif step.owner == "response_agent" and step.status == "pending":
            try:
                updated_step = await _execute_response_step(state, step)
                updated_plan.append(updated_step)
            except Exception as exc:
                updated_plan.append(_mark_step_failed(step, str(exc)))

        # Human step is not executed here.
        # We simply leave it pending so that the dedicated human_review node
        # can handle it later in the graph.
        elif step.owner == "human":
            updated_plan.append(_mark_step_pending(step))

        # Any other step is passed through unchanged.
        # This keeps v1 flexible without overengineering execution branching.
        else:
            updated_plan.append(step)

    # Persist the updated plan back into state.
    state.agent_state.plan = updated_plan
    state.agent_state.current_step_id = _get_next_pending_step_id(updated_plan)

    failed_steps = [step for step in updated_plan if step.status == "failed"]
    human_steps = [step for step in updated_plan if step.owner == "human"]

    # Outcome logic:
    # - if execution failed anywhere, route toward human review
    # - otherwise keep the workflow running
    #
    # Even if a human step exists, execute_plan itself does not finish the case;
    # it only prepares the state for downstream nodes.
    if failed_steps:
        state.workflow_outcome = "needs_human_review"
    elif human_steps:
        state.workflow_outcome = "running"
    else:
        state.workflow_outcome = "running"

    latency_ms = round((time.perf_counter() - started) * 1000, 2)

    # Store execution-level metadata for tracing, debugging, and later metrics.
    state.additional_metadata["execute_plan"] = {
        "request_id": request_id,
        "latency_ms": latency_ms,
        "retrieved_documents_count": len(state.retrieved_documents or []),
        "failed_steps": [step.step_id for step in failed_steps],
        "next_step_id": state.agent_state.current_step_id,
    }

    logger.info(
        "execute_plan.completed",
        extra=bind_log_context(
            request_id=request_id,
            node_name="execute_plan",
            latency_ms=latency_ms,
            retrieved_documents_count=len(state.retrieved_documents or []),
            failed_steps=len(failed_steps),
            next_step_id=state.agent_state.current_step_id,
        ),
    )

    return state
