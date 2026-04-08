from typing import Optional, Dict, Any, Literal
from pydantic import BaseModel, Field, ConfigDict

from app.schemas import (
    SupportTicket,
    TriageOutput,
    RetrievedDocument,
    ResponseDrafting,
    SupportAgentState,
    ShieldOutput,
)


class GraphState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(..., description="Request correlation id for logs, traces, and graph execution.")

    initial_ticket: SupportTicket = Field(
        ...,
        description="The initial ticket from the customer",
    )

    shield_result: Optional[ShieldOutput] = Field(
        default=None,
        description="The result of the input shield",
    )

    triage_result: Optional[TriageOutput] = Field(
        default=None,
        description="The result of the triage",
    )

    retrieved_documents: list[RetrievedDocument] = Field(
        default_factory=list,
        description="The documents retrieved from the knowledge base",
    )

    agent_state: Optional[SupportAgentState] = Field(
        default=None,
        description="The state of the agents",
    )

    response_draft: Optional[ResponseDrafting] = Field(
        default=None,
        description="The response draft to the customer's ticket",
    )

    is_safe: bool = Field(
        default=True,
        description="Flag if all semantic guardrails are passed.",
    )

    safety_feedback: Optional[str] = Field(
        default=None,
        description="Comments from the validator if the response is not safe.",
    )

    human_approved: Optional[bool] = Field(
        default=None,
        description="Whether the human approved the response",
    )

    human_comments: Optional[str] = Field(
        default=None,
        description="The comments from the human",
    )

    workflow_outcome: Optional[Literal["running", "blocked", "needs_human_review", "completed"]] = Field(
        default=None,
        description="The outcome of the workflow",
    )

    additional_metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional metadata about the response",
    )
