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


def _get_next_pending_step_id(plan: list[PlanStep]) -> str | None:
    for step in plan:
        if step.status == "pending":
            return step.step_id
    return None


async def _execute_retrieval_step(state: GraphState, step: PlanStep) -> tuple[PlanStep, int]:
    query = _build_retrieval_query(state, step)
    documents = retrieve_relevant_documents(query=query, max_documents=3)

    state.retrieved_documents = documents

    result_summary = f"Retrieved {len(documents)} document(s)."
    updated_step = _mark_step_completed(step, result=result_summary)

    return updated_step, len(documents)


async def _execute_response_step(state: GraphState, step: PlanStep) -> PlanStep:
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

    state.response_draft = parsed
    state.additional_metadata["response_drafting"] = {
        "request_id": state.request_id,
        "model_name": result.model_name,
        "latency_ms": result.latency_ms,
        "attempts": result.attempts,
        "used_documents": len(parsed.related_documents),
    }

    return _mark_step_completed(step, result="Drafted grounded customer response.")


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

    for step in state.agent_state.plan:
        if step.owner == "retrieval_agent" and step.status == "pending":
            try:
                updated_step, docs_count = await _execute_retrieval_step(state, step)
                retrieval_count += docs_count
                updated_plan.append(updated_step)
            except Exception as exc:
                updated_plan.append(_mark_step_failed(step, str(exc)))

        elif step.owner == "response_agent" and step.status == "pending":
            try:
                updated_step = await _execute_response_step(state, step)
                updated_plan.append(updated_step)
            except Exception as exc:
                updated_plan.append(_mark_step_failed(step, str(exc)))

        elif step.owner == "human":
            updated_plan.append(_mark_step_pending(step))

        else:
            updated_plan.append(step)

    state.agent_state.plan = updated_plan
    state.agent_state.current_step_id = _get_next_pending_step_id(updated_plan)

    failed_steps = [step for step in updated_plan if step.status == "failed"]
    human_steps = [step for step in updated_plan if step.owner == "human"]

    if failed_steps:
        state.workflow_outcome = "needs_human_review"
    elif human_steps:
        state.workflow_outcome = "running"
    else:
        state.workflow_outcome = "running"

    latency_ms = round((time.perf_counter() - started) * 1000, 2)
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
