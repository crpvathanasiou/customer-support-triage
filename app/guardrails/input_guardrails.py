import re
from typing import List, Optional

from app.schemas import ShieldCategory, ShieldOutput, SupportTicket


PROMPT_INJECTION_PATTERNS = [
    r"ignore\s+previous\s+instructions",
    r"ignore\s+all\s+previous\s+instructions",
    r"reveal\s+your\s+system\s+prompt",
    r"show\s+me\s+the\s+system\s+prompt",
    r"you\s+are\s+now",
    r"new\s+rules",
    r"developer\s+message",
    r"system\s+prompt",
    r"disregard\s+the\s+above",
]

OUT_OF_SCOPE_PATTERNS = [
    r"\bwrite me a poem\b",
    r"\bweather\b",
    r"\btell me a joke\b",
    r"\bwrite python code\b",
    r"\bdo my homework\b",
]

ABUSIVE_PATTERNS = [
    r"\bstupid\b",
    r"\bidiot\b",
    r"\btrash\b",
    r"\bdamn\b",
    r"\bshit\b",
    r"\bfuck\b",
]

THIRD_PARTY_DATA_PATTERNS = [
    r"show me .* order",
    r"give me .* customer data",
    r"tell me .* email address",
    r"show me .* account details",
    r"what is .* phone number",
]

POLICY_BYPASS_PATTERNS = [
    r"refund me without",
    r"bypass policy",
    r"make an exception",
    r"skip verification",
    r"don't follow policy",
]


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def sanitize_message(text: str) -> str:
    return normalize_whitespace(text)


def matches_any_pattern(text: str, patterns: List[str]) -> bool:
    lowered = text.lower()
    return any(re.search(pattern, lowered) for pattern in patterns)


def collect_categories(text: str) -> List[ShieldCategory]:
    categories: List[ShieldCategory] = []

    if matches_any_pattern(text, PROMPT_INJECTION_PATTERNS):
        categories.append("prompt_injection")

    if matches_any_pattern(text, THIRD_PARTY_DATA_PATTERNS):
        categories.append("privacy_risk")

    if matches_any_pattern(text, POLICY_BYPASS_PATTERNS):
        categories.append("policy_bypass_attempt")

    if matches_any_pattern(text, ABUSIVE_PATTERNS):
        categories.append("abusive_language")

    if matches_any_pattern(text, OUT_OF_SCOPE_PATTERNS):
        categories.append("out_of_scope")

    return categories


def is_non_actionable(text: str) -> bool:
    normalized = normalize_whitespace(text).lower()
    weak_messages = {
        "help",
        "problem",
        "issue",
        "refund",
        "support",
        "it doesn't work",
        "not working",
    }
    return normalized in weak_messages or len(normalized) < 8


def build_fail_fast_shield_output(ticket: SupportTicket) -> Optional[ShieldOutput]:
    text = sanitize_message(ticket.customer_message)

    if not text:
        return ShieldOutput(
            decision="block",
            risk_level="medium",
            categories=["non_actionable"],
            sanitized_message="",
            should_route_to_human=False,
            clarification_question="Please describe your issue in one or two sentences.",
            reasoning="The message is empty after normalization, so the workflow should not continue.",
        )

    if is_non_actionable(text):
        return ShieldOutput(
            decision="needs_clarification",
            risk_level="low",
            categories=["non_actionable"],
            sanitized_message=text,
            should_route_to_human=False,
            clarification_question="Please describe your issue with a bit more detail, for example what happened and what you need help with.",
            reasoning="The message appears support-related but too vague to continue reliably.",
        )

    categories = collect_categories(text)

    if "privacy_risk" in categories:
        return ShieldOutput(
            decision="block",
            risk_level="high",
            categories=categories,
            sanitized_message=text,
            should_route_to_human=True,
            clarification_question=None,
            reasoning="The message appears to request unauthorized third-party information.",
        )

    if "prompt_injection" in categories:
        return ShieldOutput(
            decision="allow_with_flag",
            risk_level="high",
            categories=categories,
            sanitized_message=text,
            should_route_to_human=False,
            clarification_question=None,
            reasoning="The message contains instruction-override patterns, so it should continue only with caution.",
        )

    return None
