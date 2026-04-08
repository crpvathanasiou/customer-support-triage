from app.schemas import ShieldOutput, SupportTicket


def build_triage_system_prompt() -> str:
    return """
    You are the Triage Analyzer for a customer support AI workflow.

    Your job is to analyze the incoming support request and return a structured triage result.

    You must determine:
    - issue_category
    - intent
    - urgency
    - customer_tone
    - requires_escalation
    - requires_human_approval
    - reasoning_summary

    Rules:
    - Do not solve the ticket.
    - Do not retrieve knowledge.
    - Do not invent customer/account facts that were not provided.
    - Be conservative with escalation and human approval for refund, billing disputes, and account security issues.
    - reasoning_summary must be short and decision-focused.
    """.strip()


def build_triage_user_prompt(
    ticket: SupportTicket,
    shield_result: ShieldOutput,
) -> str:
    return f"""

    Sanitized customer message:
    <<<SANITIZED_MESSAGE>>>
    {shield_result.sanitized_message}
    <<<END_SANITIZED_MESSAGE>>>

    Shield decision:
    - decision: {shield_result.decision}
    - risk_level: {shield_result.risk_level}
    - categories: {shield_result.categories}
    - should_route_to_human: {shield_result.should_route_to_human}

    Customer metadata:
    {ticket.customer_metadata or {}}

    Order/account metadata:
    {ticket.order_account_metadata or {}}
    """.strip()
