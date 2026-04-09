import time
from langsmith import traceable

from app.core.logging import bind_log_context, get_logger
from app.graph_state import GraphState

logger = get_logger(__name__)


def _human_review_required(state: GraphState) -> bool:
    """
    Determine whether this case should be considered human-review-gated.

    A case may require human review because:
    - shield flagged it
    - triage requires human approval
    - guardrails failed
    """
    if state.shield_result and state.shield_result.should_route_to_human:
        return True

    if state.triage_result and state.triage_result.requires_human_approval:
        return True

    if state.is_safe is False:
        return True

    return False


@traceable(run_type="chain", name="human_review_node")
async def human_review_node(state: GraphState) -> GraphState:
    started = time.perf_counter()
    request_id = state.request_id

    logger.info(
        "human_review.started",
        extra=bind_log_context(
            request_id=request_id,
            node_name="human_review",
        ),
    )

    review_required = _human_review_required(state)

    # If this node was reached but no human review is actually required,
    # keep the workflow moving toward completion.
    if not review_required:
        state.workflow_outcome = "running"
        state.additional_metadata["human_review"] = {
            "request_id": request_id,
            "review_required": False,
            "review_status": "not_required",
        }

        logger.info(
            "human_review.skipped",
            extra=bind_log_context(
                request_id=request_id,
                node_name="human_review",
                review_required=False,
                review_status="not_required",
            ),
        )
        return state

    # If a human decision has not yet been provided, keep the workflow
    # in a waiting/review-needed state.
    if state.human_approved is None:
        state.workflow_outcome = "needs_human_review"
        state.additional_metadata["human_review"] = {
            "request_id": request_id,
            "review_required": True,
            "review_status": "pending",
            "human_comments": state.human_comments,
        }

        logger.info(
            "human_review.pending",
            extra=bind_log_context(
                request_id=request_id,
                node_name="human_review",
                review_required=True,
                review_status="pending",
            ),
        )
        return state

    # Human explicitly approved the case.
    if state.human_approved is True:
        state.workflow_outcome = "completed"
        state.additional_metadata["human_review"] = {
            "request_id": request_id,
            "review_required": True,
            "review_status": "approved",
            "human_comments": state.human_comments,
        }

        logger.info(
            "human_review.approved",
            extra=bind_log_context(
                request_id=request_id,
                node_name="human_review",
                review_required=True,
                review_status="approved",
            ),
        )
        return state

    # Human explicitly rejected or did not approve the case.
    state.workflow_outcome = "blocked"
    state.additional_metadata["human_review"] = {
        "request_id": request_id,
        "review_required": True,
        "review_status": "rejected",
        "human_comments": state.human_comments,
    }

    logger.info(
        "human_review.rejected",
        extra=bind_log_context(
            request_id=request_id,
            node_name="human_review",
            review_required=True,
            review_status="rejected",
        ),
    )

    return state