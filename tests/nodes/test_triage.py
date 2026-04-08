import asyncio
import uuid

from app.graph_state import GraphState
from app.nodes.input_shield import input_shield_node
from app.nodes.triage import triage_node
from app.schemas import SupportTicket


async def main() -> None:
    state = GraphState(
        request_id=str(uuid.uuid4()),
        initial_ticket=SupportTicket(
            customer_message="I was charged twice for my order and I want a refund.",
            customer_metadata={"customer_id": "cust_123"},
            order_account_metadata={"order_id": "ord_456"},
        ),
    )

    state = await input_shield_node(state)
    state = await triage_node(state)

    print("REQUEST ID:", state.request_id)
    print("WORKFLOW OUTCOME:", state.workflow_outcome)
    print("SHIELD RESULT:", state.shield_result.model_dump() if state.shield_result else None)
    print("TRIAGE RESULT:", state.triage_result.model_dump() if state.triage_result else None)
    print("METADATA:", state.additional_metadata)


if __name__ == "__main__":
    asyncio.run(main())