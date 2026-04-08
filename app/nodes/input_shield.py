import time
from typing import List

from app.core.exceptions import (
    GuardrailBlockedError,
    ModelOutputParsingError,
    UpstreamServiceError,
)
from app.core.logging import bind_log_context, get_logger
from app.core.settings import get_settings
from app.graph_state import GraphState
from app.guardrails.input_guardrails import (
    build_fail_fast_shield_output,
    sanitize_message,
)
from app.llm.openai_wrapper import (
    AsyncOpenAIWrapper,
    BaseGuardrail,
    GuardrailResult,
    MaxPromptLengthGuardrail,
)
from app.prompts.input_shield_prompts import (
    build_input_shield_system_prompt,
    build_input_shield_user_prompt,
)
from app.schemas import ShieldOutput, SupportTicket


logger = get_logger(__name__)


class ShieldOutputNotEmptyGuardrail(BaseGuardrail):
    name = "shield_output_not_empty"

    def check_output(
        self,
        *,
        prompt: str,
        output_text: str,
        model_name: str,
        temperature: float,
    ) -> GuardrailResult:
        return GuardrailResult(passed=True)


def _build_default_guardrails() -> List[BaseGuardrail]:
    settings = get_settings()
    return [
        MaxPromptLengthGuardrail(max_chars=settings.input_shield_max_prompt_chars),
        ShieldOutputNotEmptyGuardrail(),
    ]


def _normalize_llm_shield_output(output: ShieldOutput, ticket: SupportTicket) -> ShieldOutput:
    sanitized = sanitize_message(output.sanitized_message or ticket.customer_message)

    if not sanitized:
        sanitized = sanitize_message(ticket.customer_message)

    categories = output.categories or []
    decision = output.decision
    risk_level = output.risk_level
    should_route_to_human = output.should_route_to_human

    if "privacy_risk" in categories and decision != "block":
        decision = "block"
        risk_level = "high"
        should_route_to_human = True

    if "prompt_injection" in categories and decision == "allow":
        decision = "allow_with_flag"
        if risk_level in {"low", "medium"}:
            risk_level = "high"

    if "non_actionable" in categories and decision == "allow":
        decision = "needs_clarification"

    return ShieldOutput(
        decision=decision,
        risk_level=risk_level,
        categories=categories,
        sanitized_message=sanitized,
        should_route_to_human=should_route_to_human,
        clarification_question=output.clarification_question,
        reasoning=output.reasoning,
    )


async def input_shield_node(state: GraphState) -> GraphState:
    started = time.perf_counter()
    request_id = state.request_id
    ticket = state.initial_ticket

    logger.info(
        "input_shield.started",
        extra=bind_log_context(
            request_id=request_id,
            node_name="input_shield",
        ),
    )

    fail_fast_result = build_fail_fast_shield_output(ticket)
    if fail_fast_result is not None:
        state.shield_result = fail_fast_result

        if fail_fast_result.decision == "block":
            state.workflow_outcome = "blocked"
        elif fail_fast_result.decision == "needs_clarification":
            state.workflow_outcome = "blocked"
        elif fail_fast_result.should_route_to_human:
            state.workflow_outcome = "needs_human_review"
        else:
            state.workflow_outcome = "running"

        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        state.additional_metadata["input_shield"] = {
            "request_id": request_id,
            "source": "heuristic_fail_fast",
            "latency_ms": latency_ms,
            "decision": fail_fast_result.decision,
            "risk_level": fail_fast_result.risk_level,
        }

        logger.info(
            "input_shield.completed_fail_fast",
            extra=bind_log_context(
                request_id=request_id,
                node_name="input_shield",
                decision=fail_fast_result.decision,
                risk_level=fail_fast_result.risk_level,
                latency_ms=latency_ms,
            ),
        )
        return state

    settings = get_settings()
    system_prompt = build_input_shield_system_prompt()
    user_prompt = build_input_shield_user_prompt(ticket)

    llm = AsyncOpenAIWrapper(
        default_model=settings.openai_model_input_shield,
        default_temperature=settings.input_shield_temperature,
    )

    try:
        result = await llm.generate_structured(
            system_prompt=system_prompt,
            prompt=user_prompt,
            response_schema=ShieldOutput,
            enforced_guardrails=_build_default_guardrails(),
        )

        parsed = result.parsed
        if parsed is None:
            raise ModelOutputParsingError("Parsed shield output is None.")

        normalized = _normalize_llm_shield_output(parsed, ticket)

        state.shield_result = normalized

        if normalized.decision == "block":
            state.workflow_outcome = "blocked"
        elif normalized.decision == "needs_clarification":
            state.workflow_outcome = "blocked"
        elif normalized.should_route_to_human:
            state.workflow_outcome = "needs_human_review"
        else:
            state.workflow_outcome = "running"

        state.additional_metadata["input_shield"] = {
            "request_id": request_id,
            "model_name": result.model_name,
            "guardrail_notes": result.guardrail_notes,
            "latency_ms": result.latency_ms,
            "attempts": result.attempts,
            "decision": normalized.decision,
            "risk_level": normalized.risk_level,
        }

        logger.info(
            "input_shield.completed",
            extra=bind_log_context(
                request_id=request_id,
                node_name="input_shield",
                model_name=result.model_name,
                latency_ms=result.latency_ms,
                attempts=result.attempts,
                decision=normalized.decision,
                risk_level=normalized.risk_level,
            ),
        )
        return state

    except GuardrailBlockedError as exc:
        state.shield_result = ShieldOutput(
            decision="block",
            risk_level="high",
            categories=["suspicious_input"],
            sanitized_message=sanitize_message(ticket.customer_message),
            should_route_to_human=True,
            clarification_question=None,
            reasoning=f"Shield guardrail blocked the input: {str(exc)}",
        )
        state.workflow_outcome = "blocked"

        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        state.additional_metadata["input_shield_error"] = {
            "request_id": request_id,
            "error_type": "GuardrailBlockedError",
            "message": str(exc),
            "latency_ms": latency_ms,
        }

        logger.warning(
            "input_shield.guardrail_blocked",
            extra=bind_log_context(
                request_id=request_id,
                node_name="input_shield",
                error_type="GuardrailBlockedError",
                latency_ms=latency_ms,
            ),
        )
        return state

    except (ModelOutputParsingError, UpstreamServiceError) as exc:
        state.shield_result = ShieldOutput(
            decision="allow_with_flag",
            risk_level="medium",
            categories=["suspicious_input"],
            sanitized_message=sanitize_message(ticket.customer_message),
            should_route_to_human=True,
            clarification_question=None,
            reasoning="Shield model classification failed, so the message is being forwarded with caution.",
        )
        state.workflow_outcome = "needs_human_review"

        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        state.additional_metadata["input_shield_error"] = {
            "request_id": request_id,
            "error_type": exc.__class__.__name__,
            "message": str(exc),
            "latency_ms": latency_ms,
        }

        logger.exception(
            "input_shield.recovered_error",
            extra=bind_log_context(
                request_id=request_id,
                node_name="input_shield",
                error_type=exc.__class__.__name__,
                latency_ms=latency_ms,
            ),
        )
        return state