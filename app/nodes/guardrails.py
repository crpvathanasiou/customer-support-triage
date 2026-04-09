import time
from langsmith import traceable

from app.core.logging import bind_log_context, get_logger
from app.graph_state import GraphState
from app.guardrails.response_guardrails import (
    summarize_guardrail_issues,
    validate_response_draft,
)

logger = get_logger(__name__)


@traceable(run_type="chain", name="guardrails_node")
async def guardrails_node(state: GraphState) -> GraphState:
    started = time.perf_counter()
    request_id = state.request_id

    logger.info(
        "guardrails.started",
        extra=bind_log_context(
            request_id=request_id,
            node_name="guardrails",
        ),
    )

    issues = validate_response_draft(state)
    latency_ms = round((time.perf_counter() - started) * 1000, 2)

    if issues:
        state.is_safe = False
        state.safety_feedback = summarize_guardrail_issues(issues)
        state.workflow_outcome = "needs_human_review"
    else:
        state.is_safe = True
        state.safety_feedback = "Response draft passed v1 guardrails."
        if state.workflow_outcome != "needs_human_review":
            state.workflow_outcome = "running"

    state.additional_metadata["guardrails"] = {
        "request_id": request_id,
        "latency_ms": latency_ms,
        "issues_count": len(issues),
        "issues": issues,
        "is_safe": state.is_safe,
    }

    logger.info(
        "guardrails.completed",
        extra=bind_log_context(
            request_id=request_id,
            node_name="guardrails",
            latency_ms=latency_ms,
            issues_count=len(issues),
            is_safe=state.is_safe,
        ),
    )

    return state
