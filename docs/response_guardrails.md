Για **v1 `guardrails_node`** θα πρότεινα να το κρατήσουμε **deterministic και explainable**, όχι LLM-as-judge ακόμα.
Αυτό είναι καλύτερο πρώτο βήμα γιατί:

* είναι πιο αξιόπιστο για tutorial
* είναι εύκολο να τεσταριστεί
* δείχνει καθαρά structural + semantic checks
* δεν προσθέτει άλλο ένα ασταθές LLM hop χωρίς λόγο

Η λογική του node θα είναι:

1. ελέγχει ότι υπάρχει `response_draft`
2. ελέγχει ότι υπάρχουν `related_documents`
3. ελέγχει το `unsupported_promises`
4. κάνει μερικούς **policy-risk keyword checks**
5. ενημερώνει:

   * `is_safe`
   * `safety_feedback`
   * `workflow_outcome`
   * metadata/logging

---

# 1. `app/guardrails/response_guardrails.py`

```python
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
```

---

# 2. `app/nodes/guardrails.py`

```python
import time
from langsmith import traceable

from app.core.logging import bind_log_context, get_logger
from app.graph_state import GraphState
from app.guardrails.response_guardrails import (
    summarize_guardrail_issues,
    validate_response_draft,
)

logger = get_logger(__name__)


@traceable(run_type="chain", name="guardrails_node")
async def guardrails_node(state: GraphState) -> GraphState:
    started = time.perf_counter()
    request_id = state.request_id

    logger.info(
        "guardrails.started",
        extra=bind_log_context(
            request_id=request_id,
            node_name="guardrails",
        ),
    )

    issues = validate_response_draft(state)
    latency_ms = round((time.perf_counter() - started) * 1000, 2)

    if issues:
        state.is_safe = False
        state.safety_feedback = summarize_guardrail_issues(issues)
        state.workflow_outcome = "needs_human_review"
    else:
        state.is_safe = True
        state.safety_feedback = "Response draft passed v1 guardrails."
        if state.workflow_outcome != "needs_human_review":
            state.workflow_outcome = "running"

    state.additional_metadata["guardrails"] = {
        "request_id": request_id,
        "latency_ms": latency_ms,
        "issues_count": len(issues),
        "issues": issues,
        "is_safe": state.is_safe,
    }

    logger.info(
        "guardrails.completed",
        extra=bind_log_context(
            request_id=request_id,
            node_name="guardrails",
            latency_ms=latency_ms,
            issues_count=len(issues),
            is_safe=state.is_safe,
        ),
    )

    return state
```

---

# 3. Τι κάνει σωστά αυτό το v1

Αυτό το `guardrails_node` ελέγχει:

* υπάρχει draft ή όχι
* είναι grounded ή όχι
* δηλώθηκε unsupported promise ή όχι
* έχει επικίνδυνα commitments για refund/security cases ή όχι
* χρησιμοποιεί υπερβολικά βέβαιη γλώσσα σε cases που θέλουν human approval ή όχι

Αυτό είναι αρκετά καλό για πρώτη έκδοση και στέκει σαν **semantic guardrails v1**.

---

# 4. Πώς δένει με το graph

Το current graph σου ήδη κάνει:

* μετά το `execute_plan`
* πάει `guardrails`
* και από εκεί:

  * αν `not state.is_safe` → `human_review`
  * αλλιώς `finalize`

Άρα αυτό το node κουμπώνει κατευθείαν με τη ροή που έχεις.

---

# 5. Τι **δεν** κάνει ακόμα

Επίτηδες δεν βάζω ακόμα:

* LLM semantic judge
* citation span checking
* contradiction detection ανάμεσα σε draft και retrieved docs
* policy rule engine

Αυτά είναι v2.

---

# 6. Tests που πρέπει να γράψουμε μετά

Θα σου πρότεινα αμέσως μετά 3 tests:

1. **pass case**
   grounded response, no unsupported promises → `is_safe=True`

2. **missing grounding case**
   no related docs → `is_safe=False`, `needs_human_review`

3. **risky refund/security wording case**
   risky language → `is_safe=False`

---

# 7. Μικρή σύσταση

Για να παραμείνει καθαρό το tutorial, αυτός ο deterministic guardrails layer είναι πολύ καλή βάση.
Αργότερα, αν θέλεις, μπορούμε να προσθέσουμε **δεύτερο semantic validation layer με LLM** πάνω από αυτόν, όχι αντί για αυτόν.

Αν θέλεις, στο επόμενο μήνυμα σου δίνω κατευθείαν το **`tests/nodes/test_guardrails.py`**.
