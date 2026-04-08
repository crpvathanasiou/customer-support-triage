import pytest

from app.graph_state import GraphState
from app.nodes.planner import planner_node
from app.schemas import (
    PlanStep,
    ShieldOutput,
    SupportAgentState,
    SupportTicket,
    TriageOutput,
)


# Simple fake result object that mimics the shape returned by the async LLM wrapper.
# We use it in tests so we don't call the real OpenAI API.
class FakeLLMResult:
    def __init__(self, parsed, model_name="gpt-4.1-mini", latency_ms=120.5, attempts=1):
        # The parsed structured output that the planner node expects back.
        self.parsed = parsed

        # Metadata fields used by the node for logging / tracing / observability.
        self.model_name = model_name
        self.latency_ms = latency_ms
        self.attempts = attempts


@pytest.mark.asyncio
async def test_planner_node_simple_informational_ticket(monkeypatch):
    """
    Happy-path test:
    Verifies that for a simple informational support request,
    the planner returns a short execution plan with:
    1. retrieval
    2. response drafting
    and does not require human review.
    """

    # Fake async implementation that replaces the real LLM call.
    # Instead of calling OpenAI, it returns a deterministic planner result.
    async def fake_generate_structured(self, *, system_prompt, prompt, response_schema):
        return FakeLLMResult(
            parsed=SupportAgentState(
                plan=[
                    # Step 1: retrieve relevant FAQ/support context
                    PlanStep(
                        step_id="step_retrieve_faq",
                        title="Retrieve FAQ context",
                        description="Retrieve relevant FAQ information for the customer question.",
                        owner="retrieval_agent",
                        status="pending",
                    ),
                    # Step 2: draft grounded response based on retrieved content
                    PlanStep(
                        step_id="step_draft_info_response",
                        title="Draft informational response",
                        description="Draft a grounded informational response using the retrieved FAQ context.",
                        owner="response_agent",
                        status="pending",
                    ),
                ],
                # The planner should point to the first executable step.
                current_step_id="step_retrieve_faq",
            )
        )

    # Patch the planner's wrapper call so the test stays isolated and fast.
    """
    Το monkeypatch.setattr(...) σημαίνει:
    για τη διάρκεια αυτού του test, αντικατέστησε την πραγματική μέθοδο 
    με μια ψεύτικη ώστε να ελέγξω μόνο τη λογική του node.
    Ειναι ενας τροπος για να κανουμε by-pass μια συναρτηση με μια αλλη
    """
    monkeypatch.setattr(
        "app.nodes.planner.AsyncOpenAIWrapper.generate_structured",
        fake_generate_structured,
    )

    # Build a realistic graph state as input to the planner node.
    # Upstream nodes (shield + triage) are assumed to have already run successfully.
    state = GraphState(
        request_id="req-test-001",
        initial_ticket=SupportTicket(
            customer_message="Can you tell me how long shipping usually takes?",
            customer_metadata={},
            order_account_metadata={},
        ),
        shield_result=ShieldOutput(
            decision="allow",
            risk_level="low",
            categories=["valid_support_request"],
            sanitized_message="Can you tell me how long shipping usually takes?",
            should_route_to_human=False,
            clarification_question=None,
            reasoning="Valid support request.",
        ),
        triage_result=TriageOutput(
            issue_category="other",
            intent="information_request",
            urgency="low",
            customer_tone="calm",
            requires_escalation=False,
            requires_human_approval=False,
            reasoning_summary="Customer is asking for general shipping information.",
        ),
    )

    # Execute the async planner node.
    updated_state = await planner_node(state)

    # The planner should populate agent_state.
    assert updated_state.agent_state is not None

    # For this simple case, we expect exactly 2 steps.
    assert len(updated_state.agent_state.plan) == 2

    # The current step should point to the first retrieval step.
    assert updated_state.agent_state.current_step_id == "step_retrieve_faq"

    # Workflow should continue normally.
    assert updated_state.workflow_outcome == "running"

    # Validate the planner created the expected logical steps.
    step_titles = [step.title for step in updated_state.agent_state.plan]
    assert "Retrieve FAQ context" in step_titles
    assert "Draft informational response" in step_titles


@pytest.mark.asyncio
async def test_planner_node_refund_ticket_includes_human_review(monkeypatch):
    """
    High-risk / policy-sensitive case:
    Verifies that a refund-related ticket produces a plan that includes:
    1. retrieval of refund policy
    2. drafting a response
    3. a human review step
    """

    # Fake planner output for a refund scenario.
    async def fake_generate_structured(self, *, system_prompt, prompt, response_schema):
        return FakeLLMResult(
            parsed=SupportAgentState(
                plan=[
                    # Step 1: retrieve relevant refund/billing policy context
                    PlanStep(
                        step_id="step_retrieve_refund_policy",
                        title="Retrieve refund policy",
                        description="Retrieve refund and billing policy relevant to the ticket.",
                        owner="retrieval_agent",
                        status="pending",
                    ),
                    # Step 2: prepare a grounded response draft
                    PlanStep(
                        step_id="step_draft_refund_response",
                        title="Draft refund response",
                        description="Draft a refund response grounded in the refund policy.",
                        owner="response_agent",
                        status="pending",
                    ),
                    # Step 3: require human review before any final response
                    PlanStep(
                        step_id="step_human_review",
                        title="Human review",
                        description="Review the refund case before any final response.",
                        owner="human",
                        status="pending",
                        requires_human_approval=True,
                    ),
                ],
                current_step_id="step_retrieve_refund_policy",
            )
        )

    # Patch out the real LLM call.
    monkeypatch.setattr(
        "app.nodes.planner.AsyncOpenAIWrapper.generate_structured",
        fake_generate_structured,
    )

    # Input state simulates a flagged but still valid refund complaint.
    state = GraphState(
        request_id="req-test-002",
        initial_ticket=SupportTicket(
            customer_message="I was charged twice and I want a refund immediately.",
            customer_metadata={},
            order_account_metadata={"order_id": "ORD-123"},
        ),
        shield_result=ShieldOutput(
            decision="allow_with_flag",
            risk_level="medium",
            categories=["valid_support_request", "policy_bypass_attempt"],
            sanitized_message="I was charged twice and I want a refund immediately.",
            should_route_to_human=False,
            clarification_question=None,
            reasoning="Valid support request with elevated policy risk.",
        ),
        triage_result=TriageOutput(
            issue_category="refund",
            intent="complaint",
            urgency="high",
            customer_tone="frustrated",
            requires_escalation=False,
            requires_human_approval=True,
            reasoning_summary="Refund-related complaint that should be reviewed by a human before final response.",
        ),
    )

    # Run planner node.
    updated_state = await planner_node(state)

    # Planner should create a plan.
    assert updated_state.agent_state is not None

    # In this scenario we expect exactly 3 steps.
    assert len(updated_state.agent_state.plan) == 3

    # Planner itself completed successfully, so workflow remains running.
    assert updated_state.workflow_outcome == "running"

    # Ensure that exactly one explicit human approval step exists.
    human_steps = [
        step for step in updated_state.agent_state.plan
        if step.owner == "human" and step.requires_human_approval
    ]
    assert len(human_steps) == 1
    assert human_steps[0].step_id == "step_human_review"

# αυτό το test είναι async test, άρα πρέπει να εκτελεστεί μέσα σε event loop.
@pytest.mark.asyncio 
async def test_planner_node_uses_fallback_plan_on_model_failure(monkeypatch):
    """
    Resilience / recovery test:
    Verifies that if the planner LLM call fails,
    the node does not crash the workflow, but instead:
    - creates a fallback plan
    - marks the workflow as needing human review
    - records planner error metadata
    """

    # Simulate a planner model failure (network issue, parsing issue, upstream outage, etc.)
    async def fake_generate_structured(self, *, system_prompt, prompt, response_schema):
        raise Exception("Simulated planner failure")

    # Patch the planner wrapper call.
    monkeypatch.setattr(
        "app.nodes.planner.AsyncOpenAIWrapper.generate_structured",
        fake_generate_structured,
    )

    # Input state represents a potentially sensitive account security issue.
    state = GraphState(
        request_id="req-test-003",
        initial_ticket=SupportTicket(
            customer_message="My account may have been accessed by someone else.",
            customer_metadata={},
            order_account_metadata={"account_id": "ACC-777"},
        ),
        shield_result=ShieldOutput(
            decision="allow_with_flag",
            risk_level="high",
            categories=["valid_support_request", "suspicious_input"],
            sanitized_message="My account may have been accessed by someone else.",
            should_route_to_human=True,
            clarification_question=None,
            reasoning="Potentially sensitive support request.",
        ),
        triage_result=TriageOutput(
            issue_category="account_security",
            intent="problem_report",
            urgency="high",
            customer_tone="frustrated",
            requires_escalation=True,
            requires_human_approval=True,
            reasoning_summary="Potential account security incident requiring human review.",
        ),
    )

    # Run planner node; it should recover internally.
    updated_state = await planner_node(state)

    # Even after failure, the node should return a usable agent_state.
    assert updated_state.agent_state is not None

    # Because fallback path is used, workflow should be routed to human review.
    assert updated_state.workflow_outcome == "needs_human_review"

    # Fallback plan should contain at least retrieval + drafting.
    assert len(updated_state.agent_state.plan) >= 2

    # Validate expected fallback step ids exist.
    step_ids = [step.step_id for step in updated_state.agent_state.plan]
    assert "step_retrieve_context" in step_ids
    assert "step_draft_response" in step_ids

    # Since this is a sensitive case, fallback should also include a human step.
    human_steps = [step for step in updated_state.agent_state.plan if step.owner == "human"]
    assert len(human_steps) == 1

    # Planner should persist error metadata for debugging / observability.
    assert "planner_error" in updated_state.additional_metadata
    assert updated_state.additional_metadata["planner_error"]["fallback_plan_used"] is True



"""
# Τι προϋποθέτουν αυτά τα tests

Αυτά τα tests υποθέτουν ότι στο `GraphState` έχεις ήδη:

```python
request_id: str
```

Αφού στο planner που σου έδωσα χρησιμοποιείται `state.request_id`.

Αν δεν το έχεις βάλει ακόμα, βάλε το στο `graph_state.py`:

```python
request_id: str = Field(..., description="Unique request identifier for tracing and logs.")
```

---

# Τι ελέγχουν ουσιαστικά

## Test 1

Ελέγχει ότι για low-risk informational case:

* ο planner επιστρέφει λογικό plan
* δεν στέλνει άσκοπα σε human review
* βάζει σωστό `current_step_id`

## Test 2

Ελέγχει ότι για refund/high-risk style case:

* υπάρχει retrieval
* υπάρχει drafting
* υπάρχει human review step

## Test 3

Ελέγχει resilience:

* αν το model call αποτύχει
* ο planner δεν καταρρέει
* γυρίζει fallback plan
* σημαίνει `needs_human_review`

Αυτό είναι πολύ σημαντικό production-wise.

---

# Αν το current planner σου δεν κάνει catch generic `Exception`

Τότε το 3ο test μπορεί να αποτύχει όχι επειδή το test είναι λάθος, αλλά επειδή ο node δεν κάνει recover από generic failure.

Στην περίπτωση αυτή, το σωστό είναι να πιάσεις broad operational failure στο planner και να το μετατρέπεις σε fallback path.

---

# Πώς να τα τρέξεις

```bash
pytest tests/nodes/test_planner.py -q
```

ή όλα μαζί:

```bash
pytest -q
```

---

# Τι περιμένω να γίνει

Πιθανότατα:

* τα 2 πρώτα να περάσουν εύκολα, αν ο planner node είναι κοντά σε αυτό που σχεδιάσαμε
* το 3ο ίσως αποκαλύψει αν το exception handling σου είναι αρκετά robust

Και αυτό είναι καλό σημάδι, όχι κακό.

Στείλε μου το αποτέλεσμα των tests ή τον planner implementation σου, και μετά αποφασίζουμε αν πάμε σε μικρό fix round ή κατευθείαν στο `execute_plan_node`.

"""