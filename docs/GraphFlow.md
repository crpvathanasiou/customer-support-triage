# 1. High-level graph flow

START
  │
  ▼
input_shield_node
  │
  ├── if shield_result.decision == "block" --------------------------► finalize_node
  │
  ├── if shield_result.decision == "needs_clarification" ------------► finalize_node
  │
  └── otherwise ------------------------------------------------------► triage_node
                                                                          │
                                                                          ▼
                                                                     planner_node
                                                                          │
                                                                          ├── if no plan ----------► finalize_node
                                                                          │
                                                                          └── otherwise -----------► execute_plan_node
                                                                                                         │
                                                                                                         ▼
                                                                                                    guardrails_node
                                                                                                         │
                                                                                                         ├── if not is_safe --------------------► human_review_node
                                                                                                         │
                                                                                                         ├── if triage requires human ----------► human_review_node
                                                                                                         │
                                                                                                         ├── if shield flagged human -----------► human_review_node
                                                                                                         │
                                                                                                         └── otherwise ------------------------► finalize_node
                                                                                                                                                  │
                                                                                                                                                  ▼
                                                                                                                                                 END


----------------------------------------------------------------------------------------------------------
# 2. Με τις εσωτερικές συναρτήσεις ανά node

START
  │
  ▼
input_shield_node(state)
  │
  ├─ build_fail_fast_shield_output(ticket)
  ├─ build_input_shield_prompt(ticket)
  ├─ AsyncOpenAIWrapper.generate_structured(...)
  ├─ _normalize_llm_shield_output(...)
  └─ writes:
       - state.shield_result
       - state.workflow_outcome
       - state.additional_metadata["input_shield"]

  │
  ▼
route_after_input_shield(state)
  │
  ├─ "block" --------------------------► finalize_node(state)
  ├─ "needs_clarification" ------------► finalize_node(state)
  └─ otherwise ------------------------► triage_node(state)


triage_node(state)
  │
  ├─ build_triage_system_prompt()
  ├─ build_triage_user_prompt(...)
  ├─ AsyncOpenAIWrapper.generate_structured(...)
  ├─ _normalize_triage_output(...)   [αν υπάρχει normalization helper]
  └─ writes:
       - state.triage_result
       - state.workflow_outcome
       - state.additional_metadata["triage"]

  │
  ▼
planner_node(state)
  │
  ├─ build_planner_system_prompt()
  ├─ build_planner_user_prompt(...)
  ├─ AsyncOpenAIWrapper.generate_structured(...)
  ├─ _normalize_planner_output(...)
  ├─ _build_fallback_plan(...)   [σε recoverable failure]
  └─ writes:
       - state.agent_state.plan
       - state.agent_state.current_step_id
       - state.workflow_outcome
       - state.additional_metadata["planner"]

  │
  ▼
route_after_planner(state)
  │
  ├─ if no plan ----------------------► finalize_node(state)
  └─ otherwise -----------------------► execute_plan_node(state)


execute_plan_node(state)
  │
  ├─ loops through state.agent_state.plan
  │
  ├─ for retrieval step:
  │    ├─ _build_retrieval_query(state, step)
  │    ├─ _execute_retrieval_step(state, step)
  │    │    └─ retrieve_relevant_documents(...)
  │    └─ _mark_step_completed(...) / _mark_step_failed(...)
  │
  ├─ for response step:
  │    ├─ _execute_response_step(state, step)
  │    │    ├─ build_response_drafting_system_prompt()
  │    │    ├─ build_response_drafting_user_prompt(...)
  │    │    ├─ AsyncOpenAIWrapper.generate_structured(...)
  │    │    └─ writes state.response_draft
  │    └─ _mark_step_completed(...) / _mark_step_failed(...)
  │
  ├─ for human step:
  │    └─ _mark_step_pending(step)
  │
  ├─ _get_next_pending_step_id(updated_plan)
  └─ writes:
       - state.retrieved_documents
       - state.response_draft
       - state.agent_state.plan (updated statuses)
       - state.agent_state.current_step_id
       - state.workflow_outcome
       - state.additional_metadata["execute_plan"]

  │
  ▼
guardrails_node(state)
  │
  ├─ validate_response_draft(state)
  ├─ summarize_guardrail_issues(issues)
  └─ writes:
       - state.is_safe
       - state.safety_feedback
       - state.workflow_outcome
       - state.additional_metadata["guardrails"]

  │
  ▼
route_after_guardrails(state)
  │
  ├─ if not state.is_safe --------------------------► human_review_node(state)
  ├─ if state.triage_result.requires_human_approval ► human_review_node(state)
  ├─ if state.shield_result.should_route_to_human --► human_review_node(state)
  └─ otherwise ------------------------------------► finalize_node(state)


human_review_node(state)
  │
  ├─ reads:
  │    - state.response_draft
  │    - state.safety_feedback
  │    - state.agent_state.plan
  │
  └─ writes:
       - state.human_approved
       - state.human_comments
       - state.workflow_outcome

  │
  ▼
finalize_node(state)
  │
  └─ prepares final outcome / terminal state

  │
  ▼
END


-------------------------------------------------------------------------------------------------------------
# 3. Plan → Act → Validate → Human Review mapping

PLAN
  ├─ triage_node
  └─ planner_node

ACT
  └─ execute_plan_node
       ├─ retrieval step
       └─ response drafting step

VALIDATE
  └─ guardrails_node

HUMAN-IN-THE-LOOP
  └─ human_review_node

FINALIZE
  └─ finalize_node

--------------------------------------------------------------------------------------------------------
# 4. Το πιο σύντομο diagram 

input_shield_node
   ↓
triage_node
   ↓
planner_node
   ↓
execute_plan_node
   ├─ _execute_retrieval_step()
   └─ _execute_response_step()
   ↓
guardrails_node
   ↓
human_review_node (if needed)
   ↓
finalize_node

-----------------------------------------------------------
# LLM friendly

input_shield_node
  -> triage_node
  -> planner_node
  -> execute_plan_node
      -> _execute_retrieval_step()
      -> _execute_response_step()
  -> guardrails_node
  -> human_review_node (if needed)
  -> finalize_node

1. input_shield_node validates the incoming ticket
2. triage_node classifies the case
3. planner_node produces the execution plan
4. execute_plan_node executes retrieval and drafting steps
5. guardrails_node validates the drafted response
6. human_review_node is called if needed
7. finalize_node closes the workflow

