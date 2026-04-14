# input_shield_node
  input:
    - initial_ticket
    - request_id
  updates:
    - shield_result
    - workflow_outcome
    - additional_metadata["input_shield"]
  update rules:
    - αν το input είναι empty / non-actionable / clearly unsafe, γράφει shield_result με decision "block" ή "needs_clarification"
    - αν περάσει fail-fast checks, καλεί το shield LLM και γράφει structured ShieldOutput
    - workflow_outcome:
        - "blocked" όταν decision == "block"
        - "blocked" όταν decision == "needs_clarification"
        - "needs_human_review" όταν should_route_to_human == True
        - "running" όταν το input μπορεί να συνεχίσει κανονικά
    - γράφει metadata για model / latency / guardrails / errors

# triage_node
  input:
    - initial_ticket
    - shield_result
    - request_id
  updates:
    - triage_result
    - workflow_outcome
    - additional_metadata["triage"]
  update rules:
    - τρέχει μόνο αφού υπάρχει shield_result που επιτρέπει συνέχεια
    - καλεί το triage LLM και γράφει structured TriageOutput
    - workflow_outcome:
        - συνήθως "running" όταν το triage ολοκληρωθεί σωστά
        - "needs_human_review" μόνο αν έχεις ορίσει fallback/recovery path σε error
        - "blocked" αν λείπει απαραίτητο upstream input
    - γράφει metadata για model / latency / attempts / triage result
    - σε recoverable failure μπορεί να γράψει triage_error metadata

# planner_node
  input:
    - initial_ticket
    - shield_result
    - triage_result
    - request_id
  updates:
    - agent_state.plan
    - agent_state.current_step_id
    - workflow_outcome
    - additional_metadata["planner"]
  update rules:
    - απαιτεί shield_result και triage_result
    - καλεί το planner LLM και παράγει structured SupportAgentState
    - κάνει normalization:
        - όλα τα plan steps ξεκινούν ως "pending"
        - current_step_id γίνεται το πρώτο step αν λείπει
    - workflow_outcome:
        - "running" όταν υπάρχει έγκυρο plan
        - "blocked" αν λείπει shield_result ή triage_result
        - "needs_human_review" αν αποτύχει το planner και χρησιμοποιηθεί fallback plan
    - σε failure γράφει fallback plan αντί να σπάει όλο το workflow
    - γράφει metadata για model / latency / attempts / plan_length / current_step_id

# execute_plan_node
  input:
    - initial_ticket
    - triage_result
    - agent_state.plan
    - agent_state.current_step_id
    - retrieved_documents
    - request_id
  updates:
    - retrieved_documents
    - response_draft
    - agent_state.plan
    - agent_state.current_step_id
    - workflow_outcome
    - additional_metadata["response_drafting"]
    - additional_metadata["execute_plan"]
  update rules:
    - διατρέχει το plan με τη σειρά
    - για κάθε retrieval_agent step:
        - χτίζει retrieval query
        - κάνει local KB retrieval
        - γράφει retrieved_documents
        - αλλάζει το step status σε "completed"
        - σε exception αλλάζει το step status σε "failed"
    - για κάθε response_agent step:
        - καλεί drafting LLM
        - γράφει response_draft
        - αλλάζει το step status σε "completed"
        - σε exception αλλάζει το step status σε "failed"
    - για κάθε human step:
        - το αφήνει "pending"
        - δεν το εκτελεί εδώ
    - μετά την εκτέλεση:
        - agent_state.current_step_id = το επόμενο pending step
    - workflow_outcome:
        - "needs_human_review" αν υπάρχει failed step
        - "running" αν η εκτέλεση ολοκληρώθηκε και απομένει downstream processing
    - γράφει metadata για retrieved docs count / failed steps / next_step_id
    - γράφει response_drafting metadata όταν παραχθεί draft

# guardrails_node
  input:
    - response_draft
    - triage_result
    - request_id
  updates:
    - is_safe
    - safety_feedback
    - workflow_outcome
    - additional_metadata["guardrails"]
  update rules:
    - ελέγχει deterministic semantic rules πάνω στο response_draft
    - αν λείπει response_draft:
        - is_safe = False
        - safety_feedback = reason
        - workflow_outcome = "needs_human_review"
    - αν λείπουν related_documents:
        - is_safe = False
        - safety_feedback περιγράφει missing grounding
        - workflow_outcome = "needs_human_review"
    - αν unsupported_promises == True:
        - is_safe = False
        - workflow_outcome = "needs_human_review"
    - αν εντοπιστεί risky wording για refund/security:
        - is_safe = False
        - workflow_outcome = "needs_human_review"
    - αλλιώς:
        - is_safe = True
        - safety_feedback = "passed"
        - workflow_outcome παραμένει "running" εκτός αν ήδη χρειάζεται human review από προηγούμενο στάδιο
    - γράφει metadata για issues_count / issues / is_safe

# human_review_node
  input:
    - shield_result
    - triage_result
    - is_safe
    - human_approved
    - human_comments
    - request_id
  updates:
    - workflow_outcome
    - additional_metadata["human_review"]
  update rules:
    - πρώτα αποφασίζει αν όντως απαιτείται human review:
        - όταν shield_result.should_route_to_human == True
        - ή triage_result.requires_human_approval == True
        - ή is_safe == False
    - αν δεν απαιτείται human review:
        - workflow_outcome = "running"
        - review_status = "not_required"
    - αν απαιτείται αλλά human_approved is None:
        - workflow_outcome = "needs_human_review"
        - review_status = "pending"
    - αν human_approved == True:
        - workflow_outcome = "completed"
        - review_status = "approved"
    - αν human_approved == False:
        - workflow_outcome = "blocked"
        - review_status = "rejected"
    - γράφει human_comments στα metadata, δεν τα αλλάζει

# finalize_node
  input:
    - workflow_outcome
    - human_approved
    - is_safe
    - response_draft
    - agent_state
    - request_id
  updates:
    - workflow_outcome
    - additional_metadata["finalize"]
  update rules:
    - κάνει terminal consolidation, δεν παράγει νέα business logic
    - priority rules:
        - αν workflow_outcome == "blocked" -> μένει "blocked"
        - αν human_approved == False -> "blocked"
        - αν workflow_outcome == "needs_human_review" -> μένει "needs_human_review"
        - αν human_approved == True -> "completed"
        - αν is_safe == True και response_draft υπάρχει -> "completed"
        - αν workflow_outcome in {"running", "completed"} και υπάρχει response_draft -> "completed"
        - αλλιώς -> "blocked"
    - γράφει final metadata για:
        - final_workflow_outcome
        - has_response_draft
        - is_safe
        - human_approved
        - current_step_id

