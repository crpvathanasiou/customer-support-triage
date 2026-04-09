import pytest

from app.graph_state import GraphState
from app.nodes.finalize import finalize_node
from app.schemas import ResponseDrafting, RetrievedDocument, SupportTicket


@pytest.mark.asyncio
async def test_finalize_node_marks_completed_for_safe_response():
    state = GraphState(
        request_id="req-finalize-001",
        initial_ticket=SupportTicket(
            customer_message="How long does shipping take?",
            customer_metadata={},
            order_account_metadata={},
        ),
        response_draft=ResponseDrafting(
            ticket_response="Shipping usually takes 3-5 business days.",
            related_documents=[
                RetrievedDocument(
                    source="faq.md",
                    content="Shipping usually takes 3-5 business days.",
                )
            ],
            unsupported_promises=False,
        ),
        is_safe=True,
        workflow_outcome="running",
    )

    updated_state = await finalize_node(state)

    assert updated_state.workflow_outcome == "completed"
    assert "finalize" in updated_state.additional_metadata
    assert updated_state.additional_metadata["finalize"]["final_workflow_outcome"] == "completed"


@pytest.mark.asyncio
async def test_finalize_node_keeps_needs_human_review_when_pending():
    state = GraphState(
        request_id="req-finalize-002",
        initial_ticket=SupportTicket(
            customer_message="I want a refund.",
            customer_metadata={},
            order_account_metadata={"order_id": "ORD-002"},
        ),
        is_safe=False,
        workflow_outcome="needs_human_review",
        human_approved=None,
    )

    updated_state = await finalize_node(state)

    assert updated_state.workflow_outcome == "needs_human_review"
    assert "finalize" in updated_state.additional_metadata
    assert updated_state.additional_metadata["finalize"]["final_workflow_outcome"] == "needs_human_review"


@pytest.mark.asyncio
async def test_finalize_node_marks_blocked_when_human_rejects():
    state = GraphState(
        request_id="req-finalize-003",
        initial_ticket=SupportTicket(
            customer_message="My account was hacked.",
            customer_metadata={},
            order_account_metadata={"account_id": "ACC-003"},
        ),
        workflow_outcome="running",
        human_approved=False,
        human_comments="Do not send draft. Needs manual handling.",
    )

    updated_state = await finalize_node(state)

    assert updated_state.workflow_outcome == "blocked"
    assert "finalize" in updated_state.additional_metadata
    assert updated_state.additional_metadata["finalize"]["final_workflow_outcome"] == "blocked"
