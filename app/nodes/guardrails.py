from app.schemas import ShieldOutput
from app.graph_state import GraphState

def guardrails_node(user_input:str, graph_state: GraphState) -> GraphState:
    # check the user input through the input shield
    #user_message = "### " + user_input + " ###"

    #shield_result = input_shield(user_input)
    #graph_state.shield_result = shield_result
    return graph_state