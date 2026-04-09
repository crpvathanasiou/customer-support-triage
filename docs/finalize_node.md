Τέλεια. Άρα και το `human_review_node` έκλεισε σωστά.

Μέχρι εδώ έχεις πλέον περασμένα tests για:

* `planner_node` ✅
* `execute_plan_node` ✅
* `guardrails_node` ✅
* `human_review_node` ✅

Αυτό σημαίνει ότι το core workflow είναι πλέον ουσιαστικά στημένο.

## Πού είμαστε τώρα

Έχεις πρακτικά ένα πλήρες v1 pipeline:

* input shield
* triage
* planner
* execute plan
* guardrails
* human review

Το μόνο βασικό node που λείπει για να “κλείσει” η ροή είναι το:

**`finalize_node`**

## Άρα το επόμενο σωστό βήμα

Προτείνω:

1. να φτιάξουμε το `finalize_node`
2. να γράψουμε 2-3 end-to-end graph tests
3. μετά να κάνουμε ένα μικρό architecture review για:

   * concurrency
   * production discipline
   * τι θα έλεγες σε interview / presentation

## Τι πρέπει να κάνει το `finalize_node`

Για v1, αρκεί να:

* συνθέτει το terminal αποτέλεσμα
* σταθεροποιεί το `workflow_outcome`
* αφήνει το state σε καθαρή τελική μορφή
* ίσως γράφει final metadata/logs

Δεν χρειάζεται να κάνει βαριά λογική.

Η πιο φυσική συνέχεια είναι να πάμε τώρα σε **`finalize_node`**.

# Finalize Node
Πάμε να το κλείσουμε σωστά.

Το `finalize_node` σε αυτό το project δεν πρέπει να κάνει “έξυπνη” δουλειά.
Πρέπει να λειτουργεί σαν **τερματικός consolidator**:

* να κοιτάζει το state όπως έχει διαμορφωθεί από τα προηγούμενα nodes
* να σταθεροποιεί το τελικό `workflow_outcome`
* να γράφει final metadata / logging
* να μην αλλάζει business αποφάσεις που έχουν ήδη παρθεί upstream

Με άλλα λόγια:
**δεν αποφασίζει**, απλώς **συνοψίζει και κλείνει**.

---

# `app/nodes/finalize.py`

```python
import time
from langsmith import traceable

from app.core.logging import bind_log_context, get_logger
from app.graph_state import GraphState

logger = get_logger(__name__)


def _resolve_final_workflow_outcome(state: GraphState) -> str:
    """
    Resolve the terminal workflow outcome based on the final graph state.

    Priority order matters:
    1. Explicit blocked cases stay blocked
    2. Pending human review stays pending
    3. Approved human review becomes completed
    4. Rejected human review becomes blocked
    5. Safe completed drafting path becomes completed
    6. Fallback to the existing workflow_outcome if already present
    7. Otherwise default to blocked for safety
    """
    if state.workflow_outcome == "blocked":
        return "blocked"

    if state.human_approved is False:
        return "blocked"

    if state.workflow_outcome == "needs_human_review":
        return "needs_human_review"

    if state.human_approved is True:
        return "completed"

    if state.is_safe and state.response_draft is not None:
        return "completed"

    if state.workflow_outcome in {"running", "completed"}:
        # At finalize time, "running" should collapse into a terminal state.
        # If we reached finalize without a draft, block conservatively.
        if state.response_draft is not None:
            return "completed"
        return "blocked"

    return "blocked"


@traceable(run_type="chain", name="finalize_node")
async def finalize_node(state: GraphState) -> GraphState:
    started = time.perf_counter()
    request_id = state.request_id

    logger.info(
        "finalize.started",
        extra=bind_log_context(
            request_id=request_id,
            node_name="finalize",
        ),
    )

    final_outcome = _resolve_final_workflow_outcome(state)
    state.workflow_outcome = final_outcome

    latency_ms = round((time.perf_counter() - started) * 1000, 2)

    state.additional_metadata["finalize"] = {
        "request_id": request_id,
        "latency_ms": latency_ms,
        "final_workflow_outcome": final_outcome,
        "has_response_draft": state.response_draft is not None,
        "is_safe": state.is_safe,
        "human_approved": state.human_approved,
        "current_step_id": state.agent_state.current_step_id if state.agent_state else None,
    }

    logger.info(
        "finalize.completed",
        extra=bind_log_context(
            request_id=request_id,
            node_name="finalize",
            latency_ms=latency_ms,
            final_workflow_outcome=final_outcome,
            has_response_draft=state.response_draft is not None,
            is_safe=state.is_safe,
            human_approved=state.human_approved,
        ),
    )

    return state
```

---

# Τι κάνει σωστά αυτό το v1

## Case 1 — blocked upstream

Αν το input shield ή human review έχει ήδη μπλοκάρει τη ροή:

```python
workflow_outcome = "blocked"
```

μένει blocked.

## Case 2 — pending human review

Αν δεν έχει δοθεί ακόμα ανθρώπινη απόφαση:

```python
workflow_outcome = "needs_human_review"
```

μένει έτσι.

## Case 3 — human approved

Αν ο άνθρωπος ενέκρινε:

```python
human_approved = True
```

το final outcome γίνεται `completed`.

## Case 4 — safe draft, no human gate needed

Αν υπάρχει draft και το guardrails layer το θεωρεί safe:

```python
is_safe = True and response_draft is not None
```

τότε κλείνει ως `completed`.

## Case 5 — ambiguous terminal state

Αν φτάσουμε στο finalize χωρίς draft ή χωρίς καθαρό terminal signal,
το node γίνεται **conservative** και γυρίζει `blocked`.

Αυτό είναι σωστό production-wise.

---

# `tests/nodes/test_finalize.py`

```python
import pytest

from app.graph_state import GraphState
from app.nodes.finalize import finalize_node
from app.schemas import ResponseDrafting, RetrievedDocument, SupportTicket


@pytest.mark.asyncio
async def test_finalize_node_marks_completed_for_safe_response():
    state = GraphState(
        request_id="req-finalize-001",
        initial_ticket=SupportTicket(
            customer_message="How long does shipping take?",
            customer_metadata={},
            order_account_metadata={},
        ),
        response_draft=ResponseDrafting(
            ticket_response="Shipping usually takes 3-5 business days.",
            related_documents=[
                RetrievedDocument(
                    source="faq.md",
                    content="Shipping usually takes 3-5 business days.",
                )
            ],
            unsupported_promises=False,
        ),
        is_safe=True,
        workflow_outcome="running",
    )

    updated_state = await finalize_node(state)

    assert updated_state.workflow_outcome == "completed"
    assert "finalize" in updated_state.additional_metadata
    assert updated_state.additional_metadata["finalize"]["final_workflow_outcome"] == "completed"


@pytest.mark.asyncio
async def test_finalize_node_keeps_needs_human_review_when_pending():
    state = GraphState(
        request_id="req-finalize-002",
        initial_ticket=SupportTicket(
            customer_message="I want a refund.",
            customer_metadata={},
            order_account_metadata={"order_id": "ORD-002"},
        ),
        is_safe=False,
        workflow_outcome="needs_human_review",
        human_approved=None,
    )

    updated_state = await finalize_node(state)

    assert updated_state.workflow_outcome == "needs_human_review"
    assert "finalize" in updated_state.additional_metadata
    assert updated_state.additional_metadata["finalize"]["final_workflow_outcome"] == "needs_human_review"


@pytest.mark.asyncio
async def test_finalize_node_marks_blocked_when_human_rejects():
    state = GraphState(
        request_id="req-finalize-003",
        initial_ticket=SupportTicket(
            customer_message="My account was hacked.",
            customer_metadata={},
            order_account_metadata={"account_id": "ACC-003"},
        ),
        workflow_outcome="running",
        human_approved=False,
        human_comments="Do not send draft. Needs manual handling.",
    )

    updated_state = await finalize_node(state)

    assert updated_state.workflow_outcome == "blocked"
    assert "finalize" in updated_state.additional_metadata
    assert updated_state.additional_metadata["finalize"]["final_workflow_outcome"] == "blocked"
```

---

# Run

```bash
pytest tests/nodes/test_finalize.py -q
```

---

# Η λογική όλης της ροής τώρα

Με αυτό, ο κύκλος έχει κλείσει:

```text
input_shield
  ↓
triage
  ↓
planner
  ↓
execute_plan
  ↓
guardrails
  ↓
human_review (if needed)
  ↓
finalize
```

και το `finalize_node` λειτουργεί σαν ο τελικός “state closer”.

---

# Τι ακολουθεί αμέσως μετά

Το επόμενο πιο σωστό βήμα είναι:

1. να τρέξεις τα tests του `finalize_node`
2. μετά να γράψουμε **2-3 end-to-end graph tests**

Γιατί τώρα έχεις πλέον όλα τα core nodes.
Άρα το πιο χρήσιμο πράγμα δεν είναι άλλο isolated node, αλλά να επιβεβαιώσουμε ότι το **graph ολόκληρο** περνά σωστά:

* happy path
* human review path
* blocked path

Στείλε μου το αποτέλεσμα από το `test_finalize.py` και αμέσως μετά πάμε στα end-to-end tests.
