import asyncio
import uuid

from app.graph_state import GraphState
from app.nodes.input_shield import input_shield_node
from app.schemas import SupportTicket


async def main() -> None:
    state = GraphState(
        request_id=str(uuid.uuid4()),
        initial_ticket=SupportTicket(
            customer_message="I was charged twice for my order and I need help.",
            customer_metadata={"customer_id": "cust_123"},
            order_account_metadata={"order_id": "ord_456"},
        ),
    )

    updated_state = await input_shield_node(state)

    print("REQUEST ID:", updated_state.request_id)
    print("WORKFLOW OUTCOME:", updated_state.workflow_outcome)
    print("SHIELD RESULT:", updated_state.shield_result.model_dump() if updated_state.shield_result else None)
    print("METADATA:", updated_state.additional_metadata)


if __name__ == "__main__":
    asyncio.run(main())