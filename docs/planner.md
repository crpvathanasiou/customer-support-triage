## Για ticket τύπου refund/high risk, ένα λογικό output θα είναι περίπου:
SupportAgentState(
    plan=[
        PlanStep(
            step_id="step_retrieve_refund_policy",
            title="Retrieve refund policy context",
            description="Retrieve refund and billing policy relevant to the ticket.",
            owner="retrieval_agent",
            status="pending",
        ),
        PlanStep(
            step_id="step_draft_refund_response",
            title="Draft grounded refund response",
            description="Draft a response grounded in the retrieved refund policy.",
            owner="response_agent",
            status="pending",
        ),
        PlanStep(
            step_id="step_human_review",
            title="Human review",
            description="Review the case before any final customer-facing response.",
            owner="human",
            status="pending",
            requires_human_approval=True,
        ),
    ],
    current_step_id="step_retrieve_refund_policy",
)