import asyncio
from pprint import pprint

from app.graph import build_graph
from app.graph_state import GraphState
from app.schemas import SupportTicket

# python scripts\run_graph_once.py

async def main():
    graph = build_graph()

    state = GraphState(
        request_id="req-manual-001",
        initial_ticket=SupportTicket(
            customer_message="I have paid for the order but I have not received it, I need someone to communicate with me.",
            customer_metadata={"customer_id": "cust-123"},
            order_account_metadata={"order_id": "ORD-123"},
        ),
    )

    result = await graph.ainvoke(state)

    print("\n=== WORKFLOW OUTCOME ===")
    print(result["workflow_outcome"] if isinstance(result, dict) else result.workflow_outcome)

    final_state = result if isinstance(result, GraphState) else GraphState.model_validate(result)

    print("\n=== TRIAGE RESULT ===")
    pprint(final_state.triage_result.model_dump() if final_state.triage_result else None)

    print("\n=== PLAN ===")
    if final_state.agent_state and final_state.agent_state.plan:
        for step in final_state.agent_state.plan:
            print(
                f"- {step.step_id} | {step.title} | owner={step.owner} | "
                f"status={step.status} | requires_human_approval={step.requires_human_approval}"
            )
    else:
        print("No plan produced.")

    print("\n=== CURRENT STEP ID ===")
    print(final_state.agent_state.current_step_id if final_state.agent_state else None)

    print("\n=== RETRIEVED DOCUMENTS ===")
    pprint([doc.model_dump() for doc in final_state.retrieved_documents or []])

    print("\n=== RESPONSE DRAFT ===")
    pprint(final_state.response_draft.model_dump() if final_state.response_draft else None)

    print("\n=== SAFETY ===")
    print("is_safe:", final_state.is_safe)
    print("safety_feedback:", final_state.safety_feedback)


if __name__ == "__main__":
    asyncio.run(main())


