from __future__ import annotations

from typing import List

from app.graph_state import GraphState


RISKY_REFUND_PATTERNS = [
    "full refund guaranteed",
    "we will refund you immediately",
    "refund has been approved",
    "your refund is confirmed",
]

RISKY_SECURITY_PATTERNS = [
    "we have reset your account",
    "your account has been restored",
    "we verified your identity",
    "we changed your account settings",
]

OVERCONFIDENT_PATTERNS = [
    "definitely",
    "certainly",
    "guaranteed",
    "for sure",
]


def _contains_any(text: str, patterns: list[str]) -> list[str]:
    lowered = text.lower()
    return [pattern for pattern in patterns if pattern in lowered]


def validate_response_draft(state: GraphState) -> list[str]:
    """
    Returns a list of safety issues.
    Empty list means the draft passed v1 guardrails.
    """
    issues: List[str] = []

    if state.response_draft is None:
        issues.append("Missing response draft.")
        return issues

    draft = state.response_draft
    response_text = draft.ticket_response.strip()

    if not response_text:
        issues.append("Response draft is empty.")

    if not draft.related_documents:
        issues.append("Response draft is not grounded in retrieved documents.")

    if draft.unsupported_promises is True:
        issues.append("Response draft contains unsupported promises.")

    triage = state.triage_result
    if triage is not None:
        if triage.issue_category == "refund":
            matched = _contains_any(response_text, RISKY_REFUND_PATTERNS)
            if matched:
                issues.append(
                    f"Refund-related draft contains risky commitment language: {matched}."
                )

        if triage.issue_category == "account_security":
            matched = _contains_any(response_text, RISKY_SECURITY_PATTERNS)
            if matched:
                issues.append(
                    f"Security-related draft contains risky account-action language: {matched}."
                )

    matched_confidence = _contains_any(response_text, OVERCONFIDENT_PATTERNS)
    if matched_confidence and (triage and triage.requires_human_approval):
        issues.append(
            f"Draft uses overconfident language for a case requiring human approval: {matched_confidence}."
        )

    return issues


def summarize_guardrail_issues(issues: list[str]) -> str:
    if not issues:
        return "Response draft passed v1 guardrails."
    return " | ".join(issues)
