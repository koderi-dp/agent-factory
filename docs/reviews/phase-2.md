Verified one job-status regression. The selected Phase 2 and process/recovery tests passed on Windows (261 passed, 2 skipped); deferred Claude session 1.11 work is not counted as a defect.

Review comment:

- [P2] Keep job status available when current role validation fails — C:\Source\projects\agent-factory\src\agent_factory\cli.py:253-254
  After creating a job, changing a configured role to an unresolved name now makes `factory status <job-id>` fail without displaying the job. Verified: the job endpoint still returns 200, but `/roles` returns 422 and this new mandatory request causes status to exit 1. Existing jobs retain frozen configuration under [§10.5](docs/cli-orchestrator-plan.md#105-freezing), so inspecting them should not depend on validating roles for new jobs. Make provenance reporting best-effort, preserve the job output, and add a regression test covering invalid current roles.

## Fixed

- Agreed with the status regression. The CLI now treats role provenance as best-effort:
  service and I/O failures emit a warning on stderr while preserving the job output and
  successful exit. JSON output remains parseable and omits unavailable role metadata.
  The primary job request still propagates failures normally.
- Added `test_cli_status_with_invalid_current_roles` in `tests/test_engine_api.py`,
  covering text and JSON output through the real API and SQLite after creating a job
  and changing its configured role to an unresolved name. Existing outdated-role tests
  continue to cover successful provenance reporting.

Validation on Windows (Python 3.11.15):

- `uv sync --locked` completed successfully.
- `uv run --locked yapf --in-place src/agent_factory/cli.py tests/test_engine_api.py`
  followed by `uv run --locked yapf --diff src/agent_factory/cli.py tests/test_engine_api.py`
  completed successfully; the diff check produced no output.
- `uv run --locked pytest tests/test_engine_api.py tests/test_engine_roles.py`:
  **103 passed**, with one existing Starlette/httpx deprecation warning.

## Not fixed

None; the single review finding is addressed. No spec deviations or open questions.
