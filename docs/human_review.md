Πάμε να φτιάξουμε ένα **ρεαλιστικό `human_review_node` v1**.

Για το tutorial σας, το `human_review_node` δεν πρέπει να είναι “μαγικό”.
Πρέπει να εκφράζει καθαρά το εξής:

* το σύστημα έφτασε σε σημείο που χρειάζεται άνθρωπο
* ο άνθρωπος είτε:

  * εγκρίνει
  * απορρίπτει
  * ζητά αλλαγές / σχόλια

Για v1, επειδή δεν έχουμε UI ή real review system, το πιο σωστό είναι να το κάνουμε **state-driven/manual-input friendly**.

---

# Τι θα κάνει το `human_review_node` v1

Θα διαβάζει:

* `state.shield_result`
* `state.triage_result`
* `state.response_draft`
* `state.safety_feedback`
* `state.additional_metadata`

Και θα βασίζεται σε **manual review input** που θα υπάρχει στο state, π.χ.:

* `state.human_approved`
* `state.human_comments`

Αν αυτά δεν έχουν δοθεί ακόμα, ο node:

* δεν “μαντεύει”
* δεν αυτοεγκρίνει
* απλώς βάζει το workflow σε `needs_human_review`

Αυτό είναι πολύ πιο σωστό production-wise.

---

# 1. Μικρή προσθήκη στο `GraphState`

Για να είναι το node λίγο πιο καθαρό, το current state σου ήδη έχει:

* `human_approved`
* `human_comments`

Αυτό αρκεί.
Δεν χρειάζεται νέο schema για v1.

Άρα δεν αλλάζουμε κάτι υποχρεωτικά.

---

# 2. `app/nodes/human_review.py`

```python
import time
from langsmith import traceable

from app.core.logging import bind_log_context, get_logger
from app.graph_state import GraphState

logger = get_logger(__name__)


def _human_review_required(state: GraphState) -> bool:
    """
    Determine whether this case should be considered human-review-gated.

    A case may require human review because:
    - shield flagged it
    - triage requires human approval
    - guardrails failed
    """
    if state.shield_result and state.shield_result.should_route_to_human:
        return True

    if state.triage_result and state.triage_result.requires_human_approval:
        return True

    if state.is_safe is False:
        return True

    return False


@traceable(run_type="chain", name="human_review_node")
async def human_review_node(state: GraphState) -> GraphState:
    started = time.perf_counter()
    request_id = state.request_id

    logger.info(
        "human_review.started",
        extra=bind_log_context(
            request_id=request_id,
            node_name="human_review",
        ),
    )

    review_required = _human_review_required(state)

    # If this node was reached but no human review is actually required,
    # keep the workflow moving toward completion.
    if not review_required:
        state.workflow_outcome = "running"
        state.additional_metadata["human_review"] = {
            "request_id": request_id,
            "review_required": False,
            "review_status": "not_required",
        }

        logger.info(
            "human_review.skipped",
            extra=bind_log_context(
                request_id=request_id,
                node_name="human_review",
                review_required=False,
                review_status="not_required",
            ),
        )
        return state

    # If a human decision has not yet been provided, keep the workflow
    # in a waiting/review-needed state.
    if state.human_approved is None:
        state.workflow_outcome = "needs_human_review"
        state.additional_metadata["human_review"] = {
            "request_id": request_id,
            "review_required": True,
            "review_status": "pending",
            "human_comments": state.human_comments,
        }

        logger.info(
            "human_review.pending",
            extra=bind_log_context(
                request_id=request_id,
                node_name="human_review",
                review_required=True,
                review_status="pending",
            ),
        )
        return state

    # Human explicitly approved the case.
    if state.human_approved is True:
        state.workflow_outcome = "completed"
        state.additional_metadata["human_review"] = {
            "request_id": request_id,
            "review_required": True,
            "review_status": "approved",
            "human_comments": state.human_comments,
        }

        logger.info(
            "human_review.approved",
            extra=bind_log_context(
                request_id=request_id,
                node_name="human_review",
                review_required=True,
                review_status="approved",
            ),
        )
        return state

    # Human explicitly rejected or did not approve the case.
    state.workflow_outcome = "blocked"
    state.additional_metadata["human_review"] = {
        "request_id": request_id,
        "review_required": True,
        "review_status": "rejected",
        "human_comments": state.human_comments,
    }

    logger.info(
        "human_review.rejected",
        extra=bind_log_context(
            request_id=request_id,
            node_name="human_review",
            review_required=True,
            review_status="rejected",
        ),
    )

    return state
```

---

# 3. Τι κάνει σωστά αυτό το v1

## Αν δεν χρειάζεται review

Δεν μπλοκάρει άσκοπα το workflow.

## Αν χρειάζεται review αλλά δεν έχει δοθεί ακόμα απόφαση

Κρατάει το state σε:

```python
workflow_outcome = "needs_human_review"
```

Αυτό είναι το πιο σωστό behavior.

## Αν ο άνθρωπος εγκρίνει

Σημαίνει:

```python
human_approved = True
workflow_outcome = "completed"
```

## Αν ο άνθρωπος απορρίψει

Σημαίνει:

```python
human_approved = False
workflow_outcome = "blocked"
```

---

# 4. Πώς δένει με το graph

Το current graph σου κάνει:

* `guardrails`
* αν χρειάζεται → `human_review`
* μετά → `finalize`

Άρα το `human_review_node` λειτουργεί ως **decision gate πριν το finalize**.

---

# 5. Important design note

Αυτός ο node **δεν κάνει drafting revisions**.
Δεν ξαναστέλνει το draft πίσω για αλλαγές.
Για v1 αυτό είναι σωστό.

Αργότερα, αν θες, μπορείς να κάνεις:

* human rejects with comments
* graph loops back to planner or drafting

Αλλά όχι τώρα.

---

# 6. `tests/nodes/test_human_review.py`

```python
import pytest

from app.graph_state import GraphState
from app.nodes.human_review import human_review_node
from app.schemas import ShieldOutput, SupportTicket, TriageOutput


@pytest.mark.asyncio
async def test_human_review_node_stays_pending_when_decision_not_provided():
    state = GraphState(
        request_id="req-human-001",
        initial_ticket=SupportTicket(
            customer_message="I want a refund for a double charge.",
            customer_metadata={},
            order_account_metadata={"order_id": "ORD-001"},
        ),
        shield_result=ShieldOutput(
            decision="allow_with_flag",
            risk_level="medium",
            categories=["valid_support_request"],
            sanitized_message="I want a refund for a double charge.",
            should_route_to_human=False,
            clarification_question=None,
            reasoning="Valid request with some risk.",
        ),
        triage_result=TriageOutput(
            issue_category="refund",
            intent="complaint",
            urgency="high",
            customer_tone="frustrated",
            requires_escalation=False,
            requires_human_approval=True,
            reasoning_summary="Refund case requires human approval.",
        ),
        is_safe=True,
        human_approved=None,
        human_comments=None,
    )

    updated_state = await human_review_node(state)

    assert updated_state.workflow_outcome == "needs_human_review"
    assert "human_review" in updated_state.additional_metadata
    assert updated_state.additional_metadata["human_review"]["review_status"] == "pending"


@pytest.mark.asyncio
async def test_human_review_node_marks_completed_when_approved():
    state = GraphState(
        request_id="req-human-002",
        initial_ticket=SupportTicket(
            customer_message="I was charged twice.",
            customer_metadata={},
            order_account_metadata={"order_id": "ORD-002"},
        ),
        triage_result=TriageOutput(
            issue_category="refund",
            intent="complaint",
            urgency="high",
            customer_tone="frustrated",
            requires_escalation=False,
            requires_human_approval=True,
            reasoning_summary="Refund case requires review.",
        ),
        is_safe=True,
        human_approved=True,
        human_comments="Approved after reviewing billing context.",
    )

    updated_state = await human_review_node(state)

    assert updated_state.workflow_outcome == "completed"
    assert "human_review" in updated_state.additional_metadata
    assert updated_state.additional_metadata["human_review"]["review_status"] == "approved"
    assert updated_state.additional_metadata["human_review"]["human_comments"] == "Approved after reviewing billing context."


@pytest.mark.asyncio
async def test_human_review_node_blocks_when_rejected():
    state = GraphState(
        request_id="req-human-003",
        initial_ticket=SupportTicket(
            customer_message="My account was hacked.",
            customer_metadata={},
            order_account_metadata={"account_id": "ACC-003"},
        ),
        triage_result=TriageOutput(
            issue_category="account_security",
            intent="problem_report",
            urgency="high",
            customer_tone="frustrated",
            requires_escalation=True,
            requires_human_approval=True,
            reasoning_summary="Security case requires human decision.",
        ),
        is_safe=False,
        safety_feedback="Security-related draft contains risky account-action language.",
        human_approved=False,
        human_comments="Do not send this draft. Needs manual handling.",
    )

    updated_state = await human_review_node(state)

    assert updated_state.workflow_outcome == "blocked"
    assert "human_review" in updated_state.additional_metadata
    assert updated_state.additional_metadata["human_review"]["review_status"] == "rejected"
    assert updated_state.additional_metadata["human_review"]["human_comments"] == "Do not send this draft. Needs manual handling."
```

---

# 7. Run

```bash
pytest tests/nodes/test_human_review.py -q
```

---

# 8. Τι κερδίζεις με αυτό το node

Με αυτό το `human_review_node`, το graph σου πλέον έχει πλήρες v1 cycle:

* shield
* triage
* planner
* execute
* guardrails
* human gate
* finalize

Άρα το tutorial έχει πλέον **όλο το skeleton ενός σοβαρού agentic workflow**.

Το αμέσως επόμενο μετά από αυτό είναι σχεδόν σίγουρα:
**`finalize_node`**
και μετά ένα μικρό end-to-end pass.
