from typing import Optional, Dict, Any, Literal, List, TypeAlias
from pydantic import BaseModel, Field, ConfigDict

ShieldCategory: TypeAlias = Literal[
    "valid_support_request",
    "out_of_scope",
    "non_actionable",
    "prompt_injection",
    "privacy_risk",
    "policy_bypass_attempt",
    "abusive_language",
    "suspicious_input",
]

class SupportTicket(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_message: str = Field(
        ...,
        description="The message from the customer",
        min_length=5,
        max_length=500,
    )

    customer_metadata: Optional[Dict[str, Any]] = Field(
        description="Metadata about the customer",
        default_factory=dict,
    )

    order_account_metadata: Optional[Dict[str, Any]] = Field(
        description="Metadata about the order and account",
        default_factory=dict,
    )


class ShieldOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal[
        "allow",
        "allow_with_flag",
        "needs_clarification",
        "block",
    ] = Field(..., description="The input shield's decision on whether and how the graph will proceed.")

    risk_level: Literal["low", "medium", "high", "critical"] = Field(
        ...,
        description="Assessment of the total risk of the incoming message.",
    )

    categories: List[ShieldCategory] = Field(
        default_factory=list,
        description="Categories/signals detected within the input.",
    )

    sanitized_message: str = Field(
        ...,
        description="A sanitized version of the message safe for subsequent nodes.",
    )

    should_route_to_human: bool = Field(
        default=False,
        description="Indicates if the input needs to be routed to a human for review.",
    )

    clarification_question: Optional[str] = Field(
        default=None,
        description="Proposed question to ask if the input is insufficient or non-actionable.",
    )

    reasoning: str = Field(
        ...,
        description="A brief explanation of the shield's decision logic.",
    )


class TriageOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue_category: Literal[
        "technical",
        "billing",
        "refund",
        "account_security",
        "other",
    ] = Field(..., description="The business/domain category of the issue.")

    intent: Literal[
        "information_request",
        "problem_report",
        "complaint",
        "suggestion",
        "other",
    ] = Field(..., description="The intention of the customer")

    urgency: Literal["low", "medium", "high", "critical"] = Field(
        default="medium",
        description="The urgency level of the issue.",
    )

    customer_tone: Literal["calm", "frustrated", "angry", "neutral"] = Field(
        ...,
        description="The customer's overall tone.",
    )

    requires_escalation: bool = Field(
        ...,
        description="Whether the issue should be routed to an escalation path.",
    )

    requires_human_approval: bool = Field(
        ...,
        description="Whether a human must review before sending a final response.",
    )

    reasoning_summary: str = Field(
        ...,
        description="Brief decision-focused summary explaining the triage result in 1-2 sentences.",
    )


class RetrievedDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(..., description="Source document name or source of the information.")
    content: str = Field(..., description="Related excerpt of text.")


class ResponseDrafting(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticket_response: str = Field(..., description="The response to the customer's ticket.")
    related_documents: list[RetrievedDocument] = Field(
        ..., 
        description="The related documents to the customer's ticket."
        )
    unsupported_promises: Optional[bool] = Field(
        default=None, 
        description="Whether the response is unsupported."
        )


class PlanStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: str = Field(..., description="Unique id of the step.")
    title: str = Field(..., description="Short title of the step.")
    description: str = Field(..., description="What needs to be done in this step.")
    owner: Literal["planner", "triage_agent", "retrieval_agent", "response_agent", "guardrail_agent", "human"] = Field(
        ...,
        description="Who is responsible for executing the step.",
    )
    status: Literal["pending", "in_progress", "completed", "failed", "blocked"] = Field(
        default="pending",
        description="Status of the step.",
    )
    requires_human_approval: bool = Field(
        default=False,
        description="Whether human approval is required before continuing.",
    )
    result: Optional[str] = Field(default=None, description="Short result or output summary of the step.")
    error: Optional[str] = Field(default=None, description="Description of the error if the step fails.")


class SupportAgentState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan: List[PlanStep] = Field(
        default_factory=list,
        description="Structured plan that the planner creates.",
    )
    current_step_id: Optional[str] = Field(
        default=None,
        description="The current step that is being executed.",
    )
