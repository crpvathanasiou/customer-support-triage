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
from app.prompts.planner_prompts import (
    build_planner_system_prompt,
    build_planner_user_prompt,
)
from app.schemas import PlanStep, SupportAgentState

logger = get_logger(__name__)


def _normalize_planner_output(agent_state: SupportAgentState) -> SupportAgentState:
    """
    Final defensive normalization for planner output.
    Ensures:
    - every step starts as pending
    - current_step_id points to the first step if missing
    """
    normalized_steps: list[PlanStep] = []

    for step in agent_state.plan:
        normalized_steps.append(
            PlanStep(
                step_id=step.step_id,
                title=step.title,
                description=step.description,
                owner=step.owner,
                status="pending",
                requires_human_approval=step.requires_human_approval,
                result=None,
                error=None,
            )
        )

    current_step_id = agent_state.current_step_id
    if normalized_steps and not current_step_id:
        current_step_id = normalized_steps[0].step_id

    return SupportAgentState(
        plan=normalized_steps,
        current_step_id=current_step_id,
    )


def _build_fallback_plan(state: GraphState) -> SupportAgentState:
    """
    Safe fallback plan if planner generation fails.
    Keeps the workflow recoverable and reviewable.
    """
    requires_human = False
    if state.triage_result:
        requires_human = (
            state.triage_result.requires_human_approval
            or state.triage_result.requires_escalation
        )

    steps = [
        PlanStep(
            step_id="step_retrieve_context",
            title="Retrieve relevant support context",
            description="Retrieve the most relevant policy, FAQ, or SOP context for this ticket.",
            owner="retrieval_agent",
            status="pending",
        ),
        PlanStep(
            step_id="step_draft_response",
            title="Draft grounded response",
            description="Draft a customer response grounded in the retrieved support context.",
            owner="response_agent",
            status="pending",
        ),
    ]

    if requires_human:
        steps.append(
            PlanStep(
                step_id="step_human_review",
                title="Human review",
                description="Review the case before any final customer-facing response.",
                owner="human",
                status="pending",
                requires_human_approval=True,
            )
        )

    return SupportAgentState(
        plan=steps,
        current_step_id=steps[0].step_id if steps else None,
    )


@traceable(run_type="chain", name="planner_node")
async def planner_node(state: GraphState) -> GraphState:
    started = time.perf_counter()
    request_id = state.request_id

    logger.info(
        "planner.started",
        extra=bind_log_context(
            request_id=request_id,
            node_name="planner",
        ),
    )

    if state.shield_result is None:
        state.workflow_outcome = "blocked"
        state.additional_metadata["planner_error"] = {
            "request_id": request_id,
            "error_type": "MissingShieldResult",
            "message": "planner_node called without shield_result",
        }
        return state

    if state.triage_result is None:
        state.workflow_outcome = "blocked"
        state.additional_metadata["planner_error"] = {
            "request_id": request_id,
            "error_type": "MissingTriageResult",
            "message": "planner_node called without triage_result",
        }
        return state

    settings = get_settings()
    planner_model = getattr(
        settings,
        "openai_model_planner",
        settings.openai_model_input_shield,
    )

    llm = AsyncOpenAIWrapper(
        default_model=planner_model,
        default_temperature=0.0,
    )

    system_prompt = build_planner_system_prompt()
    user_prompt = build_planner_user_prompt(
        ticket=state.initial_ticket,
        shield_result=state.shield_result,
        triage_result=state.triage_result,
    )

    try:
        result = await llm.generate_structured(
            system_prompt=system_prompt,
            prompt=user_prompt,
            response_schema=SupportAgentState,
        )

        parsed = result.parsed

        if parsed is None:
            raise ModelOutputParsingError("Parsed planner output is None.")

        if not isinstance(parsed, SupportAgentState):
            raise ModelOutputParsingError(
                "Parsed planner output is not of type SupportAgentState."
            )

        normalized = _normalize_planner_output(parsed)

        if not normalized.plan:
            raise ModelOutputParsingError("Planner returned an empty plan.")

        state.agent_state = normalized
        state.workflow_outcome = "running"

        state.additional_metadata["planner"] = {
            "request_id": request_id,
            "model_name": result.model_name,
            "latency_ms": result.latency_ms,
            "attempts": result.attempts,
            "plan_length": len(normalized.plan),
            "current_step_id": normalized.current_step_id,
            "step_titles": [step.title for step in normalized.plan],
        }

        logger.info(
            "planner.completed",
            extra=bind_log_context(
                request_id=request_id,
                node_name="planner",
                model_name=result.model_name,
                latency_ms=result.latency_ms,
                attempts=result.attempts,
                plan_length=len(normalized.plan),
                current_step_id=normalized.current_step_id,
            ),
        )

        return state

    except GuardrailBlockedError as exc:
        fallback = _build_fallback_plan(state)
        state.agent_state = fallback
        state.workflow_outcome = "needs_human_review"
        state.additional_metadata["planner_error"] = {
            "request_id": request_id,
            "error_type": "GuardrailBlockedError",
            "message": str(exc),
            "fallback_plan_used": True,
        }

        logger.warning(
            "planner.guardrail_blocked",
            extra=bind_log_context(
                request_id=request_id,
                node_name="planner",
                error_type="GuardrailBlockedError",
            ),
        )
        return state

    except (ModelOutputParsingError, UpstreamServiceError) as exc:
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        fallback = _build_fallback_plan(state)
        state.agent_state = fallback
        state.workflow_outcome = "needs_human_review"
        state.additional_metadata["planner_error"] = {
            "request_id": request_id,
            "error_type": exc.__class__.__name__,
            "message": str(exc),
            "latency_ms": latency_ms,
            "fallback_plan_used": True,
        }

        logger.exception(
            "planner.recovered_error",
            extra=bind_log_context(
                request_id=request_id,
                node_name="planner",
                error_type=exc.__class__.__name__,
                latency_ms=latency_ms,
            ),
        )
        return state

    except Exception as exc:
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        fallback = _build_fallback_plan(state)
        state.agent_state = fallback
        state.workflow_outcome = "needs_human_review"
        state.additional_metadata["planner_error"] = {
            "request_id": request_id,
            "error_type": exc.__class__.__name__,
            "message": str(exc),
            "latency_ms": latency_ms,
            "fallback_plan_used": True,
        }

        logger.exception(
            "planner.unexpected_error",
            extra=bind_log_context(
                request_id=request_id,
                node_name="planner",
                error_type=exc.__class__.__name__,
                latency_ms=latency_ms,
            ),
        )
        return state

