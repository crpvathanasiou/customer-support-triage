from langgraph.graph import StateGraph, START, END

from app.graph_state import GraphState

from app.nodes.input_shield import input_shield_node
from app.nodes.triage import triage_node
from app.nodes.planner import planner_node
from app.nodes.execute_plan import execute_plan_node
from app.nodes.guardrails import guardrails_node
from app.nodes.human_review import human_review_node
from app.nodes.finalize import finalize_node


def route_after_input_shield(state: GraphState) -> str:
    """
    Decide what to do after the input shield.
    """
    if state.shield_result is None:
        return "finalize"

    decision = state.shield_result.decision

    if decision == "block":
        return "finalize"

    if decision == "needs_clarification":
        return "finalize"

    return "triage"

def route_after_planner(state: GraphState) -> str:
    """
    If planner produced no plan, stop gracefully.
    """
    if state.agent_state is None:
        return "finalize"

    if not state.agent_state.plan:
        return "finalize"

    return "execute_plan"

def route_after_guardrails(state: GraphState) -> str:
    """
    Decide whether we need human review or can finalize.
    """
    if not state.is_safe:
        return "human_review"

    if state.triage_result and state.triage_result.requires_human_approval:
        return "human_review"

    if state.shield_result and state.shield_result.should_route_to_human:
        return "human_review"

    return "finalize"


def build_graph():
    graph = StateGraph(GraphState)

    # Nodes
    graph.add_node("input_shield", input_shield_node)
    graph.add_node("triage", triage_node)
    graph.add_node("planner", planner_node)
    graph.add_node("execute_plan", execute_plan_node)
    graph.add_node("guardrails", guardrails_node)
    graph.add_node("human_review", human_review_node)
    graph.add_node("finalize", finalize_node)
    

    # Edges
    graph.add_edge(START, "input_shield")
    graph.add_conditional_edges(
        "input_shield",
        route_after_input_shield,
        {
            "triage": "triage",
            "finalize": "finalize",
        }
        )

    graph.add_edge("triage", "planner")

    graph.add_conditional_edges(
        "planner",
        route_after_planner,
        {
            "execute_plan": "execute_plan",
            "finalize": "finalize",
        }
        )

    graph.add_edge("execute_plan", "guardrails")

    graph.add_conditional_edges(
        "guardrails",
        route_after_guardrails,
        {
            "human_review": "human_review",
            "finalize": "finalize",
        }
        )

    graph.add_edge("human_review", "finalize")

    graph.add_edge("finalize", END)

    return graph.compile()
























