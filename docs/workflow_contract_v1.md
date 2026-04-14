Παρακάτω είναι η **νέα version** σε μορφή **canonical workflow / node contract**, με τα επιπλέον 5 στοιχεία:

1. Preconditions
2. Invariants
3. Failure semantics
4. Routing note
5. Ownership / responsibility boundary

Μπορείς να το βάλεις αυτούσιο σε ένα αρχείο τύπου:

`docs/workflow_contract.md`

---

# Customer Support Triage Copilot — Workflow Contract v1

## Global workflow states

```text
workflow_outcome ∈ {
  "running",
  "blocked",
  "needs_human_review",
  "completed"
}
```

---

# Global invariants

* `workflow_outcome` πρέπει πάντα να είναι ένα από τα:

  * `running`
  * `blocked`
  * `needs_human_review`
  * `completed`
* `agent_state.current_step_id` πρέπει:

  * είτε να είναι `null`
  * είτε να δείχνει σε υπαρκτό `PlanStep.step_id`
* ένα `PlanStep` με `status="completed"` δεν πρέπει να επιστρέφει σε `pending`, εκτός αν υπάρχει explicit retry/reset flow
* ένα `PlanStep` με `status="failed"` δεν πρέπει να αλλάζει σιωπηλά σε `completed`
* `blocked` θεωρείται terminal outcome, εκτός αν οριστεί μελλοντικά explicit retry/reset flow
* `completed` θεωρείται terminal outcome
* `needs_human_review` σημαίνει ότι το workflow δεν μπορεί να ολοκληρωθεί αυτόματα
* το `planner_node` δεν παράγει `response_draft`
* το `guardrails_node` δεν δημιουργεί νέο draft
* το `finalize_node` δεν εισάγει νέα business logic, μόνο consolidates existing decisions

---

# Global routing model

* τα nodes **δεν αποφασίζουν απευθείας** το επόμενο node
* τα nodes ενημερώνουν το `state`
* το **graph routing layer** αποφασίζει το επόμενο βήμα με βάση το ενημερωμένο `state`
* το `finalize_node` είναι terminal consolidator
* το `END` επιτυγχάνεται μόνο μετά το `finalize_node`

---

# Node contracts

---

## 1. `input_shield_node`

### Purpose

Ελέγχει το αρχικό input πριν μπει στο κύριο agent workflow.
Λειτουργεί ως fail-fast safety and scope gate.

### Input

* `state.initial_ticket`
* `state.request_id`

### Preconditions

* `initial_ticket` must exist
* `initial_ticket.customer_message` must exist
* `workflow_outcome` must not already be terminal (`blocked` / `completed`) unless this node is being invoked as the true entry point

### Updates

* `state.shield_result`
* `state.workflow_outcome`
* `state.additional_metadata["input_shield"]`
* `state.additional_metadata["input_shield_error"]` on failure

### Update rules

* αν το input είναι empty / clearly non-actionable / unsafe:

  * γράφει `shield_result.decision = "block"` ή `"needs_clarification"`
* αν περάσει τα deterministic checks:

  * καλεί shield classification
  * γράφει structured `ShieldOutput`
* `workflow_outcome`:

  * `blocked` όταν `decision == "block"`
  * `blocked` όταν `decision == "needs_clarification"`
  * `needs_human_review` όταν `should_route_to_human == True`
  * `running` όταν το input μπορεί να συνεχίσει

### Failure behavior

* recoverable:

  * αν αποτύχει το shield classification, μπορεί να γράψει cautious fallback
  * συνήθως `needs_human_review` ή conservative block
* non-recoverable:

  * block workflow
  * γράψε error metadata

### Routing note

* δεν επιλέγει το επόμενο node
* ο graph router κοιτάζει `shield_result.decision` και `workflow_outcome`

### Ownership boundary

* κάνει input safety / scope classification
* **δεν** κάνει triage
* **δεν** φτιάχνει execution plan
* **δεν** παράγει customer response

---

## 2. `triage_node`

### Purpose

Μετατρέπει το επιτρεπτό support input σε structured case understanding.

### Input

* `state.initial_ticket`
* `state.shield_result`
* `state.request_id`

### Preconditions

* `shield_result` must exist
* `shield_result.decision` must allow continuation
* `workflow_outcome` must not be `blocked`

### Updates

* `state.triage_result`
* `state.workflow_outcome`
* `state.additional_metadata["triage"]`
* `state.additional_metadata["triage_error"]` on failure

### Update rules

* καλεί triage classification
* γράφει structured `TriageOutput`
* `workflow_outcome`:

  * συνήθως `running` όταν το triage ολοκληρωθεί
  * `blocked` αν λείπουν απαραίτητα upstream δεδομένα
  * `needs_human_review` μόνο αν έχεις ορίσει fallback behavior για recoverable failure

### Failure behavior

* recoverable:

  * γράψε `triage_error`
  * route toward `needs_human_review` αν αυτό είναι η επιλεγμένη policy
* non-recoverable:

  * `blocked`

### Routing note

* δεν επιλέγει planner ή finalize
* το graph routing κάνει το επόμενο βήμα

### Ownership boundary

* κάνει ticket interpretation
* **δεν** κάνει retrieval
* **δεν** γράφει response draft
* **δεν** φτιάχνει plan execution artifacts

---

## 3. `planner_node`

### Purpose

Μετατρέπει το triage understanding σε structured execution plan.

### Input

* `state.initial_ticket`
* `state.shield_result`
* `state.triage_result`
* `state.request_id`

### Preconditions

* `shield_result` must exist
* `triage_result` must exist
* `workflow_outcome` must not be `blocked`

### Updates

* `state.agent_state.plan`
* `state.agent_state.current_step_id`
* `state.workflow_outcome`
* `state.additional_metadata["planner"]`
* `state.additional_metadata["planner_error"]` on failure

### Update rules

* καλεί planner generation
* γράφει structured `SupportAgentState`
* κάνει normalization:

  * όλα τα steps ξεκινούν `pending`
  * `current_step_id` δείχνει στο πρώτο step αν λείπει
* `workflow_outcome`:

  * `running` όταν υπάρχει valid plan
  * `blocked` όταν λείπει required upstream state
  * `needs_human_review` όταν αποτύχει το planner και χρησιμοποιηθεί fallback plan

### Failure behavior

* recoverable:

  * γράψε fallback plan
  * θέσε `workflow_outcome = "needs_human_review"`
  * γράψε `planner_error`
* non-recoverable:

  * `blocked`

### Routing note

* δεν κάνει execute το plan
* ο graph router αποφασίζει αν θα πάει `execute_plan` ή `finalize`

### Ownership boundary

* αποφασίζει **τι πρέπει να γίνει**
* **δεν** κάνει retrieval
* **δεν** συντάσσει response
* **δεν** κάνει semantic validation

---

## 4. `execute_plan_node`

### Purpose

Εκτελεί το plan που παρήγαγε ο planner.

### Input

* `state.initial_ticket`
* `state.triage_result`
* `state.agent_state.plan`
* `state.agent_state.current_step_id`
* `state.retrieved_documents`
* `state.request_id`

### Preconditions

* `agent_state` must exist
* `agent_state.plan` must not be empty
* `workflow_outcome` must not be `blocked`
* `triage_result` should exist for drafting steps

### Updates

* `state.retrieved_documents`
* `state.response_draft`
* `state.agent_state.plan`
* `state.agent_state.current_step_id`
* `state.workflow_outcome`
* `state.additional_metadata["response_drafting"]`
* `state.additional_metadata["execute_plan"]`
* `state.additional_metadata["execute_plan_error"]` on hard failure

### Update rules

* για `retrieval_agent` step:

  * χτίζει retrieval query
  * κάνει retrieval
  * γράφει `retrieved_documents`
  * step → `completed`
  * σε exception → `failed`
* για `response_agent` step:

  * φτιάχνει draft
  * γράφει `response_draft`
  * step → `completed`
  * σε exception → `failed`
* για `human` step:

  * το αφήνει `pending`
* μετά:

  * ενημερώνει `current_step_id` με το επόμενο pending step
* `workflow_outcome`:

  * `needs_human_review` αν υπάρχει failed step
  * `running` αλλιώς

### Failure behavior

* recoverable:

  * mark failing step as `failed`
  * set `workflow_outcome = "needs_human_review"` if needed
  * preserve partial work
* non-recoverable:

  * `blocked`
  * write `execute_plan_error`

### Routing note

* δεν αποφασίζει αν περνά ή όχι από guardrails
* ο graph router το κάνει downstream

### Ownership boundary

* εκτελεί το plan
* **δεν** αποφασίζει policy correctness
* **δεν** κάνει human review
* **δεν** κλείνει το workflow

---

## 5. `guardrails_node`

### Purpose

Ελέγχει αν το παραγόμενο response draft είναι αποδεκτό.

### Input

* `state.response_draft`
* `state.triage_result`
* `state.request_id`

### Preconditions

* `response_draft` should exist unless this node is intentionally validating missing-output failure
* `workflow_outcome` must not already be terminal `blocked` / `completed`

### Updates

* `state.is_safe`
* `state.safety_feedback`
* `state.workflow_outcome`
* `state.additional_metadata["guardrails"]`

### Update rules

* αν λείπει `response_draft`:

  * `is_safe = False`
  * `workflow_outcome = "needs_human_review"`
* αν λείπει grounding (`related_documents`):

  * `is_safe = False`
  * `workflow_outcome = "needs_human_review"`
* αν `unsupported_promises == True`:

  * `is_safe = False`
  * `workflow_outcome = "needs_human_review"`
* αν υπάρχει risky wording σε sensitive cases:

  * `is_safe = False`
  * `workflow_outcome = "needs_human_review"`
* αλλιώς:

  * `is_safe = True`
  * `workflow_outcome` παραμένει `running` εκτός αν upstream state απαιτεί human review

### Failure behavior

* recoverable:

  * set unsafe
  * route to `needs_human_review`
* non-recoverable:

  * `blocked` μόνο αν υπάρχει σοβαρό state inconsistency ή explicit policy

### Routing note

* δεν αποφασίζει μόνο του human review
* ο graph router κοιτάζει:

  * `is_safe`
  * `triage_result.requires_human_approval`
  * `shield_result.should_route_to_human`

### Ownership boundary

* κάνει validation
* **δεν** ξαναγράφει το draft
* **δεν** κάνει retrieval
* **δεν** παίρνει την τελική human decision

---

## 6. `human_review_node`

### Purpose

Εκφράζει το human-in-the-loop decision gate.

### Input

* `state.shield_result`
* `state.triage_result`
* `state.is_safe`
* `state.human_approved`
* `state.human_comments`
* `state.request_id`

### Preconditions

* ο node πρέπει να τρέχει μόνο όταν το workflow έχει reason για human review
* `workflow_outcome` should typically be `needs_human_review` or routed here by graph logic

### Updates

* `state.workflow_outcome`
* `state.additional_metadata["human_review"]`

### Update rules

* αν human review δεν απαιτείται:

  * `workflow_outcome = "running"`
  * `review_status = "not_required"`
* αν απαιτείται αλλά `human_approved is None`:

  * `workflow_outcome = "needs_human_review"`
  * `review_status = "pending"`
* αν `human_approved == True`:

  * `workflow_outcome = "completed"`
  * `review_status = "approved"`
* αν `human_approved == False`:

  * `workflow_outcome = "blocked"`
  * `review_status = "rejected"`

### Failure behavior

* recoverable:

  * pending review state
* non-recoverable:

  * conservative `blocked` only if state is inconsistent and cannot be trusted

### Routing note

* δεν κάνει reroute μόνο του
* το graph τον καλεί όταν το state το απαιτεί
* μετά ακολουθεί `finalize`

### Ownership boundary

* εκφράζει ανθρώπινη απόφαση
* **δεν** αλλάζει `human_approved` μόνο του
* **δεν** ξαναγράφει response
* **δεν** κάνει semantic validation

---

## 7. `finalize_node`

### Purpose

Κλείνει το workflow και σταθεροποιεί το terminal outcome.

### Input

* `state.workflow_outcome`
* `state.human_approved`
* `state.is_safe`
* `state.response_draft`
* `state.agent_state`
* `state.request_id`

### Preconditions

* πρέπει να είναι το terminal consolidator node
* δεν πρέπει να χρησιμοποιείται για να εισάγει νέο business logic

### Updates

* `state.workflow_outcome`
* `state.additional_metadata["finalize"]`

### Update rules

* αν `workflow_outcome == "blocked"`:

  * μένει `blocked`
* αν `human_approved == False`:

  * `blocked`
* αν `workflow_outcome == "needs_human_review"`:

  * μένει `needs_human_review`
* αν `human_approved == True`:

  * `completed`
* αν `is_safe == True` και υπάρχει `response_draft`:

  * `completed`
* αν `workflow_outcome in {"running", "completed"}` και υπάρχει draft:

  * `completed`
* αλλιώς:

  * `blocked`

### Failure behavior

* recoverable:

  * conservative terminal resolution
* non-recoverable:

  * default to `blocked`

### Routing note

* terminal node
* μετά από αυτό ακολουθεί `END`

### Ownership boundary

* κάνει terminal consolidation
* **δεν** φτιάχνει νέο draft
* **δεν** εγκρίνει αντί για άνθρωπο
* **δεν** αλλάζει upstream business decisions

---

# Recommended use of this document

Αυτό το document πρέπει να λειτουργεί ως **canonical source of truth** για:

* `GraphState`
* node implementations
* routing logic
* tests
* future refactors
* Cursor / AI-assisted implementation prompts

Αν υπάρξει σύγκρουση ανάμεσα σε code και αυτό το contract, είτε:

* ενημερώνεται το code για να ταιριάξει στο contract
* είτε ενημερώνεται το contract συνειδητά, όχι σιωπηλά

---

Αν θέλεις, στο επόμενο βήμα μπορώ να το μετατρέψω σε **πολύ καθαρό README/dev-doc version** με πίνακες και headings, ώστε να διαβάζεται πιο γρήγορα από interviewer ή teammate.
