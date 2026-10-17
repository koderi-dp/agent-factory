# Session 1.3: pipeline port

The repo migration is the SQL from plan §6.2, unchanged. `pipeline.py` separates
pure decisions (`advance`, `rewind`, `rework`, `step_plan`) from SQLite application
(`advance_job`, `rework_job`). Results, supersession, pending executions, job state
and events commit together under `db.tx()`. `Completion` accepts already validated
report evidence and the observed head; it does not execute git or start processes.
`reports.py` handles first lines, strict post-launch freshness, UTF-8 and handoff
section extraction. Missing custom-role sections are allowed (§7.2).

`freeze` captures the selected pipeline, profiles, exact role text/source/hash,
limits, merge/permission/sensitive-path policy, engine version, and caller-supplied
preamble text/version. Frozen text is used even if a role file is later removed.

Rework notes are committed as numbered `note` events in the same transaction as
the rewind, then atomically materialised to `factory/jobs/<id>/rework-<k>.md` after
commit. `materialize_rework_notes` can repair a write interrupted after commit.
Existing files preserve numbering after old events have been pruned. Session 1.5
must call this repair before pruning events or scheduling at service startup.

## Ported cases

The original v1 suites remain untouched. The names below refer to
`tests/test_pipeline.py::PipelineTests`; their new counterparts are in
`tests/test_engine_pipeline.py` unless stated otherwise.

| v1 case | New coverage |
|---|---|
| `test_custom_pipeline_finishes_and_merges` | `test_custom_pipeline_finishes_with_verify_and_retains_reports`; `test_auto_approval_is_ready_for_merge`; git merge deferred to 1.8 |
| `test_retry_is_bounded_and_invalidates_downstream_results` | Same name; also no target / no budget, new-round execution history, rework note and reset |
| `test_pipeline_and_model_settings_are_frozen` | `test_pipeline_models_limits_roles_and_preamble_are_frozen` extends this to exact role text, sources, hashes and preamble |
| `test_resume_only_dispatches_missing_reviewers` | Same name; also paused/resumed jobs preserve completed rows |
| `test_parallel_reviewers_use_their_own_provider_settings` | Same name, with mixed providers in one group |
| `test_reusing_a_role_in_later_steps_keeps_separate_settings` | `test_reusing_role_in_later_steps_keeps_separate_settings`; also repeated roles in one group |
| `test_missing_selected_provider_prevents_job_creation` | `test_missing_provider_settings_prevents_new_job` and session 1.2 config tests |
| `test_stale_and_invalid_reports_do_not_advance` | `tests/test_engine_reports.py`: missing, stale/equal mtime, invalid first lines and valid fresh reports; invalid completions roll back in pipeline tests |
| `test_blocked_task_cannot_be_merged` | `test_blocked_task_cannot_be_accepted_by_human` |
| `test_configured_steps_cannot_be_skipped` | Same name |
| `test_missing_custom_instructions_are_reported` | Invalid/missing roles fail config validation in session 1.2; frozen roles survive deletion in `test_pipeline_models_limits_roles_and_preamble_are_frozen` |
| `test_selected_provider_resolves_its_own_markdown_file` | `test_selected_provider_uses_its_own_frozen_role` and session 1.2 role tests |
| `test_missing_provider_file_does_not_fall_back_to_other_provider` | `tests/test_engine_roles.py::test_no_cross_provider_fallback_and_error_lists_all_paths` (session 1.2) |
| `test_commit_after_approval_cannot_merge` | `test_commit_after_approval_cannot_be_accepted`; actual git merge deferred to 1.8 |
| `test_escalation_needs_explicit_human_acceptance` | Same name |
| `test_new_commit_invalidates_review_and_approval` | Same name, parameterised for review/approval, using observed head inputs; git inspection deferred to 1.8 |
| `test_default_pipeline_runs_through_all_builtin_steps` | `test_default_full_runs_all_builtin_steps` |
| `test_task_after_review_cannot_silently_invalidate_evidence` | Same name, using observed head inputs |

`tests/test_job.py::JobStateTests` mapping:

| v1 case | New coverage |
|---|---|
| `test_new_writes_a_full_job` | `test_new_writes_full_job_and_history` |
| `test_new_refuses_an_unclaimed_or_existing_job` | `test_new_refuses_existing_job_or_sequence_without_partial_history` |
| `test_stage_changes_append_history_with_the_round` | `test_state_changes_append_history_with_round_and_clear_note` |
| `test_a_stage_change_clears_the_note_unless_it_sets_one` | Same test above, plus BLOCKED/CHANGES/ESCALATE reason/note assertions |
| `test_bad_values_change_nothing` | `test_invalid_results_change_nothing`, atomic transition rollback, SQL CHECK constraints |
| `test_bad_job_ids_are_refused` | Same name, parameterised |
| `test_parallel_writers_lose_nothing` | `test_concurrent_writers_do_not_duplicate_transitions_or_lose_history`: 30 operations with separate SQLite connections |

`PipelineValidationTests` and `ConfigTests` were ported in session 1.2 to
`test_engine_config.py` / `test_engine_roles.py`: safe role names, YAML metadata,
provider profiles, graph validation, defaults, pure v1 upgrades and role resolution.
Their deliberate changes are listed below. `MergeTests` and `DeleteTests` involve
git/worktrees/processes and belong to sessions 1.7–1.9; they continue to run against
v1. The builder commit and clean-worktree rules require git (session 1.8).

## Deliberate behavior changes required by the plan

| v1 behavior | New behavior and authority |
|---|---|
| Mutable `job.json`, free-form stage updates and file-lock claims | Jobs/executions/events in SQLite; stage derived from state/current step; unique job id/sequence; queued starts do not require a pre-created folder (§6.1–§6.3) |
| Round incremented on entering review; downstream result dictionary cleared | Round starts at 1 and increments on rewind/rework. Old rows/reports remain history; older downstream results cannot satisfy new prerequisites (§6.1, §6.2, §10.1) |
| Verify jobs stopped at `approved` | `needs_human(verify)` with `approved_head`; auto jobs reach `approved` for the later merge implementation (§6.3, §10.1) |
| Role files re-read while dispatching; deletion breaks running jobs | Exact roles, sources, hashes and preamble frozen at creation; edits/deletions/upgrades affect only new jobs (§10.5) |
| Role frontmatter `output` determines filenames; collisions rejected; same role forbidden twice in a group | Reports are attempt-local `report.md` paths keyed by step/execution/round/attempt; duplicate roles are allowed (§5.1, §5.3, §7.1) |
| Changed report signature implies freshness | Report mtime must be strictly later than launch (§7.2) |
| Approval only accepts APPROVE/ESCALATE | Contract parser and generic transition accept CHANGES only with `onChanges` (§7.2, §10.1). Fast workflow integration remains session 2.2 |
| Provider/model settings constrained to v1's known values; terminal words reserved as step ids | Per-execution provider (claude, codex or opencode) and explicit nonempty profiles; v2 id pattern permits terminal words without confusing states (§5.1; session 1.2 tests) |

There are no additional intended deviations. Git operations/validation (1.8),
scheduling/attempt recovery (1.5), Fast integration and sensitive-path checks (2.2)
remain TODOs at their integration points. No provider CLI is invoked by this port.
