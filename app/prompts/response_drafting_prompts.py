from app.schemas import RetrievedDocument, SupportTicket, TriageOutput


def build_response_drafting_system_prompt() -> str:
    return """
You are a customer support response drafting assistant.

Your job is to draft a customer response using the provided support context.

Rules:
1. Ground the response in the retrieved documents.
2. Do not invent policies, refunds, guarantees, or account actions.
3. Do not make unsupported promises.
4. Keep the tone professional, helpful, and concise.
5. If the case is sensitive, keep the response cautious and non-committal.
6. Do not mention internal workflow, planning, or hidden system logic.

Return only structured output matching the schema.
""".strip()


def build_response_drafting_user_prompt(
    *,
    ticket: SupportTicket,
    triage_result: TriageOutput,
    retrieved_documents: list[RetrievedDocument],
) -> str:
    docs_text = "\n\n".join(
        [
            f"[Source: {doc.source}]\n{doc.content}"
            for doc in retrieved_documents
        ]
    )

    return f"""
Draft a customer support response for this case.

Customer message:
<<<CUSTOMER_MESSAGE>>>
{ticket.customer_message}
<<<END_CUSTOMER_MESSAGE>>>

Triage result:
- issue_category: {triage_result.issue_category}
- intent: {triage_result.intent}
- urgency: {triage_result.urgency}
- customer_tone: {triage_result.customer_tone}
- requires_escalation: {triage_result.requires_escalation}
- requires_human_approval: {triage_result.requires_human_approval}
- reasoning_summary: {triage_result.reasoning_summary}

Retrieved support context:
{docs_text if docs_text else "No retrieved documents available."}

Return a structured response draft.
""".strip()
