from typing import Literal
import time
from langsmith import traceable

from app.core.logging import bind_log_context, get_logger
from app.graph_state import GraphState

logger = get_logger(__name__)

WorkflowOutcome = Literal["running", "blocked", "needs_human_review", "completed"]


def _resolve_final_workflow_outcome(state: GraphState) -> WorkflowOutcome:
    """
    Resolve the terminal workflow outcome based on the final graph state.
    """
    if state.workflow_outcome == "blocked":
        return "blocked"

    if state.human_approved is False:
        return "blocked"

    if state.workflow_outcome == "needs_human_review":
        return "needs_human_review"

    if state.human_approved is True:
        return "completed"

    if state.is_safe and state.response_draft is not None:
        return "completed"

    if state.workflow_outcome in {"running", "completed"}:
        if state.response_draft is not None:
            return "completed"
        return "blocked"

    return "blocked"


@traceable(run_type="chain", name="finalize_node")
async def finalize_node(state: GraphState) -> GraphState:
    started = time.perf_counter()
    request_id = state.request_id

    logger.info(
        "finalize.started",
        extra=bind_log_context(
            request_id=request_id,
            node_name="finalize",
        ),
    )

    final_outcome = _resolve_final_workflow_outcome(state)
    state.workflow_outcome = final_outcome

    latency_ms = round((time.perf_counter() - started) * 1000, 2)

    state.additional_metadata["finalize"] = {
        "request_id": request_id,
        "latency_ms": latency_ms,
        "final_workflow_outcome": final_outcome,
        "has_response_draft": state.response_draft is not None,
        "is_safe": state.is_safe,
        "human_approved": state.human_approved,
        "current_step_id": state.agent_state.current_step_id if state.agent_state else None,
    }

    logger.info(
        "finalize.completed",
        extra=bind_log_context(
            request_id=request_id,
            node_name="finalize",
            latency_ms=latency_ms,
            final_workflow_outcome=final_outcome,
            has_response_draft=state.response_draft is not None,
            is_safe=state.is_safe,
            human_approved=state.human_approved,
        ),
    )

    return state
    