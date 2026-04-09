import pytest

from app.graph_state import GraphState
from app.nodes.guardrails import guardrails_node
from app.schemas import (
    ResponseDrafting,
    RetrievedDocument,
    SupportTicket,
    TriageOutput,
)


@pytest.mark.asyncio
async def test_guardrails_node_passes_grounded_response():
    state = GraphState(
        request_id="req-guardrails-001",
        initial_ticket=SupportTicket(
            customer_message="How long does shipping take?",
            customer_metadata={},
            order_account_metadata={},
        ),
        triage_result=TriageOutput(
            issue_category="other",
            intent="information_request",
            urgency="low",
            customer_tone="calm",
            requires_escalation=False,
            requires_human_approval=False,
            reasoning_summary="Customer asks for shipping information.",
        ),
        response_draft=ResponseDrafting(
            ticket_response="Thanks for reaching out. Shipping usually takes 3-5 business days.",
            related_documents=[
                RetrievedDocument(
                    source="faq.md",
                    content="Shipping usually takes 3-5 business days.",
                )
            ],
            unsupported_promises=False,
        ),
    )

    updated_state = await guardrails_node(state)

    assert updated_state.is_safe is True
    assert updated_state.safety_feedback == "Response draft passed v1 guardrails."
    assert updated_state.workflow_outcome == "running"
    assert "guardrails" in updated_state.additional_metadata
    assert updated_state.additional_metadata["guardrails"]["issues_count"] == 0


@pytest.mark.asyncio
async def test_guardrails_node_fails_when_response_is_not_grounded():
    state = GraphState(
        request_id="req-guardrails-002",
        initial_ticket=SupportTicket(
            customer_message="Can I get a refund?",
            customer_metadata={},
            order_account_metadata={"order_id": "ORD-100"},
        ),
        triage_result=TriageOutput(
            issue_category="refund",
            intent="complaint",
            urgency="medium",
            customer_tone="neutral",
            requires_escalation=False,
            requires_human_approval=True,
            reasoning_summary="Refund-related case requiring careful review.",
        ),
        response_draft=ResponseDrafting(
            ticket_response="We can help review your refund request.",
            related_documents=[],
            unsupported_promises=False,
        ),
    )

    updated_state = await guardrails_node(state)

    assert updated_state.is_safe is False
    assert updated_state.safety_feedback is not None
    assert "not grounded in retrieved documents" in updated_state.safety_feedback
    assert updated_state.workflow_outcome == "needs_human_review"
    assert "guardrails" in updated_state.additional_metadata
    assert updated_state.additional_metadata["guardrails"]["issues_count"] >= 1


@pytest.mark.asyncio
async def test_guardrails_node_fails_on_risky_refund_wording():
    state = GraphState(
        request_id="req-guardrails-003",
        initial_ticket=SupportTicket(
            customer_message="I was charged twice and want a refund.",
            customer_metadata={},
            order_account_metadata={"order_id": "ORD-200"},
        ),
        triage_result=TriageOutput(
            issue_category="refund",
            intent="complaint",
            urgency="high",
            customer_tone="frustrated",
            requires_escalation=False,
            requires_human_approval=True,
            reasoning_summary="Refund complaint that requires careful handling.",
        ),
        response_draft=ResponseDrafting(
            ticket_response="Your refund is confirmed and we will refund you immediately.",
            related_documents=[
                RetrievedDocument(
                    source="refund_policy.md",
                    content="Refund requests are reviewed according to billing policy.",
                )
            ],
            unsupported_promises=False,
        ),
    )

    updated_state = await guardrails_node(state)

    assert updated_state.is_safe is False
    assert updated_state.safety_feedback is not None
    assert "risky commitment language" in updated_state.safety_feedback
    assert updated_state.workflow_outcome == "needs_human_review"
    assert "guardrails" in updated_state.additional_metadata
    assert updated_state.additional_metadata["guardrails"]["issues_count"] >= 1
