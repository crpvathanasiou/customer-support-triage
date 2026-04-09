from typing import Literal
import time
from langsmith import traceable

from app.core.logging import bind_log_context, get_logger
from app.graph_state import GraphState

logger = get_logger(__name__)

# The final workflow can end only in one of these states.
# We keep the type explicit so the code stays aligned with GraphState
# and static type checkers can validate assignments safely.
WorkflowOutcome = Literal["running", "blocked", "needs_human_review", "completed"]


def _resolve_final_workflow_outcome(state: GraphState) -> WorkflowOutcome:
    """
    Decide the final terminal outcome of the workflow.

    Important idea:
    finalize_node should NOT invent new business logic.
    It only consolidates the decisions already made upstream
    by shield, triage, guardrails, and human review.

    Priority order matters:
    - blocked stays blocked
    - rejected human review blocks the case
    - pending human review stays pending
    - approved human review completes the case
    - safe drafted response completes the case
    - ambiguous terminal states fall back to blocked for safety
    """

    # If the workflow was already explicitly blocked upstream,
    # preserve that decision.
    if state.workflow_outcome == "blocked":
        return "blocked"

    # If a human explicitly rejected the case, that overrides
    # any earlier "running" status and blocks the workflow.
    if state.human_approved is False:
        return "blocked"

    # If the workflow is still waiting for a human decision,
    # do not collapse it into completed or blocked.
    if state.workflow_outcome == "needs_human_review":
        return "needs_human_review"

    # If a human explicitly approved the case, the workflow can end successfully.
    if state.human_approved is True:
        return "completed"

    # If the draft is safe and exists, then the workflow can complete
    # even without human approval.
    if state.is_safe and state.response_draft is not None:
        return "completed"

    # If we reached finalize with a "running" or "completed" signal,
    # we still want to be conservative:
    # - if there is a response draft, complete
    # - if not, block, because the workflow did not produce a real output
    if state.workflow_outcome in {"running", "completed"}:
        if state.response_draft is not None:
            return "completed"
        return "blocked"

    # Safe fallback:
    # if the state is ambiguous or incomplete, prefer blocked over
    # accidentally treating the workflow as completed.
    return "blocked"


@traceable(run_type="chain", name="finalize_node")
async def finalize_node(state: GraphState) -> GraphState:
    """
    Final graph node.

    Responsibilities:
    - resolve the final workflow outcome
    - write final metadata for tracing/debugging
    - log terminal state information

    This node is intentionally simple.
    It is the "closer" of the workflow, not a new decision-maker.
    """
    started = time.perf_counter()
    request_id = state.request_id

    logger.info(
        "finalize.started",
        extra=bind_log_context(
            request_id=request_id,
            node_name="finalize",
        ),
    )

    # Compute the final terminal state from the accumulated graph state.
    final_outcome = _resolve_final_workflow_outcome(state)
    state.workflow_outcome = final_outcome

    latency_ms = round((time.perf_counter() - started) * 1000, 2)

    # Store final execution metadata.
    # This helps later with observability, audits, debugging, and E2E tracing.
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