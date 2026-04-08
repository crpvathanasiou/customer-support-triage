import time

from langsmith import traceable

from app.core.exceptions import (
    GuardrailBlockedError,
    ModelOutputParsingError,
    UpstreamServiceError,
)
from app.core.logging import bind_log_context, get_logger
from app.core.settings import get_settings
from app.graph_state import GraphState
from app.llm.openai_wrapper import AsyncOpenAIWrapper
from app.prompts.triage_prompts import (
    build_triage_system_prompt,
    build_triage_user_prompt,
)
from app.schemas import TriageOutput

logger = get_logger(__name__)


# Trace this node in LangSmith as a chain step.
@traceable(
    run_type="chain",
    name="triage_node",
)
async def triage_node(state: GraphState) -> GraphState:
    # Start timing for latency / observability.
    started = time.perf_counter()
    request_id = state.request_id

    # Structured log: triage node execution started.
    logger.info(
        "triage.started",
        extra=bind_log_context(
            request_id=request_id,
            node_name="triage",
        ),
    )

    # Safety check:
    # Triage depends on the shield result. If it is missing, stop the workflow.
    if state.shield_result is None:
        state.workflow_outcome = "blocked"
        state.additional_metadata["triage_error"] = {
            "request_id": request_id,
            "error_type": "MissingShieldResult",
            "message": "triage_node called without shield_result",
        }
        return state

    # If the shield already decided to block or request clarification,
    # triage should not run.
    if state.shield_result.decision in {"block", "needs_clarification"}:
        state.workflow_outcome = "blocked"
        state.additional_metadata["triage"] = {
            "request_id": request_id,
            "skipped": True,
            "reason": f"shield_decision={state.shield_result.decision}",
        }
        return state

    # Load runtime settings and initialize the async LLM wrapper.
    settings = get_settings()
    llm = AsyncOpenAIWrapper(
        default_model=settings.openai_model_input_shield,
        default_temperature=0.0,
    )

    # Build the prompts for the triage classifier.
    system_prompt = build_triage_system_prompt()
    user_prompt = build_triage_user_prompt(
        ticket=state.initial_ticket,
        shield_result=state.shield_result,
    )

    try:
        # Call the model using strict structured output parsing.
        result = await llm.generate_structured(
            system_prompt=system_prompt,
            prompt=user_prompt,
            response_schema=TriageOutput,
        )

        parsed = result.parsed

        # Defensive checks:
        # Even with structured parsing, keep explicit validation for robustness.
        if parsed is None:
            raise ModelOutputParsingError("Parsed triage output is None.")

        if not isinstance(parsed, TriageOutput):
            raise ModelOutputParsingError("Parsed triage output is not of type TriageOutput.")

        # Persist triage result into graph state.
        state.triage_result = parsed

        # Update workflow outcome based on business routing signals.
        if parsed.requires_human_approval or parsed.requires_escalation:
            state.workflow_outcome = "needs_human_review"
        else:
            state.workflow_outcome = "running"

        # Store structured execution metadata for debugging and observability.
        state.additional_metadata["triage"] = {
            "request_id": request_id,
            "model_name": result.model_name,
            "latency_ms": result.latency_ms,
            "attempts": result.attempts,
            "issue_category": parsed.issue_category,
            "intent": parsed.intent,
            "urgency": parsed.urgency,
            "requires_escalation": parsed.requires_escalation,
            "requires_human_approval": parsed.requires_human_approval,
        }

        # Structured log: triage completed successfully.
        logger.info(
            "triage.completed",
            extra=bind_log_context(
                request_id=request_id,
                node_name="triage",
                model_name=result.model_name,
                latency_ms=result.latency_ms,
                attempts=result.attempts,
                issue_category=parsed.issue_category,
                intent=parsed.intent,
                urgency=parsed.urgency,
                requires_escalation=parsed.requires_escalation,
                requires_human_approval=parsed.requires_human_approval,
            ),
        )
        return state

    except GuardrailBlockedError as exc:
        # If a guardrail blocks the triage call/output,
        # send the case to human review instead of failing hard.
        state.workflow_outcome = "needs_human_review"
        state.additional_metadata["triage_error"] = {
            "request_id": request_id,
            "error_type": "GuardrailBlockedError",
            "message": str(exc),
        }

        logger.warning(
            "triage.guardrail_blocked",
            extra=bind_log_context(
                request_id=request_id,
                node_name="triage",
                error_type="GuardrailBlockedError",
            ),
        )
        return state

    except (ModelOutputParsingError, UpstreamServiceError) as exc:
        # Recoverable failure path:
        # If parsing fails or the upstream model provider fails,
        # route safely to human review and preserve observability metadata.
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        state.workflow_outcome = "needs_human_review"
        state.additional_metadata["triage_error"] = {
            "request_id": request_id,
            "error_type": exc.__class__.__name__,
            "message": str(exc),
            "latency_ms": latency_ms,
        }

        logger.exception(
            "triage.recovered_error",
            extra=bind_log_context(
                request_id=request_id,
                node_name="triage",
                error_type=exc.__class__.__name__,
                latency_ms=latency_ms,
            ),
        )
        return state