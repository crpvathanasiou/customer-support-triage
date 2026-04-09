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


# This helper creates a fresh copy of a step and marks it as completed.
#
# We do this instead of mutating the original step directly because:
# - it keeps updates explicit
# - it makes debugging easier
# - it fits well with state-based workflow thinking
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


# This helper marks a step as failed and stores the error message on the step itself.
#
# Why store the error on the step?
# Because later, when we inspect the graph state, we want to know:
# - which step failed
# - why it failed
# - whether we should route to human review
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


# This helper keeps a step in pending state.
#
# In our workflow, this is mainly used for human-owned steps.
# execute_plan_node does not perform human review itself.
# It only prepares the workflow so that the later human_review node can handle it.
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


# The planner decides THAT retrieval is needed.
# The executor decides HOW to turn that decision into a query.
#
# For tutorial v1, we build a simple query using:
# - the customer message
# - the planner step title
# - the planner step description
# - a few useful triage fields
#
# This is intentionally simple and explainable.

# ticket = "I was charged twice and want a refund"
# step.title = "Retrieve refund policy"
# step.description = "Retrieve refund and billing policy relevant to the ticket"
# triage.issue_category = "refund"
# triage.intent = "complaint"
# Result: I was charged twice and want a refund Retrieve refund policy 
# Retrieve refund and billing policy relevant to the ticket refund complaint ...
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


# After execution, we want to know what the next actionable step is.
#
# Example:
# - retrieval step completed
# - drafting step completed
# - human review step still pending
#
# In that case, the next pending step should be the human review step.
def _get_next_pending_step_id(plan: list[PlanStep]) -> str | None:
    for step in plan:
        if step.status == "pending":
            return step.step_id
    return None


# Execute a retrieval step.
#
# What happens here:
# 1. Build a retrieval query from current workflow context
# 2. Fetch relevant KB documents
# 3. Save them into state.retrieved_documents
# 4. Mark the step as completed
#
# Important:
# The planner does NOT retrieve docs itself.
# It only says retrieval should happen.
# This function is where that plan becomes real execution.
async def _execute_retrieval_step(state: GraphState, step: PlanStep) -> tuple[PlanStep, int]:
    query = _build_retrieval_query(state, step)
    documents = retrieve_relevant_documents(query=query, max_documents=3)

    # Save retrieved documents in graph state so downstream steps can use them.
    # The response drafting step depends on this context.
    state.retrieved_documents = documents

    result_summary = f"Retrieved {len(documents)} document(s)."
    updated_step = _mark_step_completed(step, result=result_summary)

    return updated_step, len(documents)


# Execute a response drafting step.
#
# What happens here:
# 1. Read triage + retrieved documents from state
# 2. Build a drafting prompt
# 3. Ask the LLM for a structured ResponseDrafting object
# 4. Save it into state.response_draft
# 5. Mark the step as completed
#
# This is the point where the workflow creates the customer-facing draft.
# The guardrails node will validate this artifact next.
async def _execute_response_step(state: GraphState, step: PlanStep) -> PlanStep:
    # We require triage before drafting because the draft should reflect
    # issue type, urgency, tone, and human-review sensitivity.
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

    # Save the draft into state.
    # This becomes the main input for the guardrails node.
    state.response_draft = parsed

    # Save useful execution metadata for tracing/debugging.
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
# This node is the "Act" part of the pattern:
# Plan -> Act -> Validate
#
# Upstream:
# - planner created the plan
#
# This node:
# - reads the plan
# - executes supported steps
# - updates state artifacts
#
# Downstream:
# - guardrails validates the drafted response
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

    # Safety check:
    # if there is no plan, this node has nothing meaningful to execute.
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

    # Walk through the plan in order.
    # For v1, we support:
    # - retrieval_agent steps
    # - response_agent steps
    # - human steps (kept pending)
    for step in state.agent_state.plan:
        # Retrieval steps fetch supporting evidence from the knowledge base.
        if step.owner == "retrieval_agent" and step.status == "pending":
            try:
                updated_step, docs_count = await _execute_retrieval_step(state, step)
                retrieval_count += docs_count
                updated_plan.append(updated_step)
            except Exception as exc:
                # Important production-minded behavior:
                # one step failure should not crash the whole workflow.
                updated_plan.append(_mark_step_failed(step, str(exc)))

        # Response steps create the customer-facing draft.
        elif step.owner == "response_agent" and step.status == "pending":
            try:
                updated_step = await _execute_response_step(state, step)
                updated_plan.append(updated_step)
            except Exception as exc:
                updated_plan.append(_mark_step_failed(step, str(exc)))

        # Human steps are intentionally not executed here.
        # They remain pending so that the human_review node can handle them later.
        elif step.owner == "human":
            updated_plan.append(_mark_step_pending(step))

        # Any unsupported or already-resolved steps are carried forward unchanged.
        else:
            updated_plan.append(step)

    # Save the updated plan back into the graph state.
    state.agent_state.plan = updated_plan
    state.agent_state.current_step_id = _get_next_pending_step_id(updated_plan)

    failed_steps = [step for step in updated_plan if step.status == "failed"]
    human_steps = [step for step in updated_plan if step.owner == "human"]

    # Decide the current workflow outcome after execution.
    #
    # If something failed, we prefer a safe path toward human review.
    # Otherwise the workflow stays "running" so downstream nodes can continue.
    if failed_steps:
        state.workflow_outcome = "needs_human_review"
    elif human_steps:
        state.workflow_outcome = "running"
    else:
        state.workflow_outcome = "running"

    latency_ms = round((time.perf_counter() - started) * 1000, 2)

    # Store execution metadata for observability.
    # This helps later with:
    # - debugging
    # - tracing
    # - latency inspection
    # - future metrics collection
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
