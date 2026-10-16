# Session 4.2: upgrade validation

The migration in `engine/scaffold.py` plans and validates all changes before applying
them. `factory upgrade --dry-run` prints a unified diff without modifying the repository,
registry or service. Custom roles are written before their old sources are removed;
conflicting overrides stop the upgrade before any repository changes. Packaged v1
SHA256 hashes use LF-normalised bytes so Windows CRLF checkouts are recognised.

`engine/legacy.py` reads v1 job metadata directly. Legacy jobs never enter the durable v2
jobs/executions/attempts tables. Reports are served through the existing confined Markdown
route. Merge and Delete adapt a legacy job to an in-memory database and reuse the ported
git operations, including worktree confinement, changed-head and sensitive-path checks.
Successful actions publish a repo event so dashboards refresh. Resume, Stop and Rework
are rejected. New job sequence numbers account for legacy folders.

The CLI, version gate and runtime distinguish repo migration requirements for new jobs
from ordinary version/registry inspection. The existing version-based service restart
also applies to a successful `factory upgrade`. Shutdown now waits for the verified
service process to exit after releasing its lock, allowing Windows handles to close.

The board API schema, Board and new LegacyJobCard display legacy jobs, reports and the
two permitted actions. The v1 fixture and `tests/test_engine_upgrade.py` cover migration,
dry-run, conflicting overrides, both provider variants, configured custom roles, CRLF
hash recognition, legacy isolation, reports, merge/delete, sequence allocation and restart.

## Installed-tool evidence

Validated locally on Windows, Python 3.11.15, with isolated `FACTORY_HOME`, `UV_TOOL_DIR`
and `UV_TOOL_BIN_DIR`. No real provider was invoked.

- `uv build --out-dir .git/session-4.2-release-check` builds the candidate distributions.
- `uv run --no-project --python 3.11 python tools/release_smoke.py .git/session-4.2-release-check`
  exercises the installed wheel, then migrates the v1 fixture and verifies byte preservation
  of backlog/job evidence and read-only listing.
- `uv run --no-project --python 3.11 python tools/upgrade_smoke.py .git/session-4.2-release-check`
  builds controlled versions 1.0.0 and 1.1.0 from the candidate sdist. It installs 1.0.0,
  starts a sleeping fake attempt using base Python, force-installs 1.1.0 while it runs,
  invokes a command to restart the service, and checks the wrapper PID, frozen job data,
  invocation count and completed job. It then reinstalls 1.0.0 and checks the job still loads.

Both smoke scripts are wired into the existing Windows/Linux CI install matrix, also
used by the release workflow. Linux execution is left to CI. The two controlled versions
test installation/recovery and database readability; they do not establish compatibility
with a historical release's implementation. Real previous-release compatibility should
be checked against that release once one exists. Claude/mixed-provider parity remains
the separate session 1.11 prerequisite.

## Check results

- `uv sync --locked`: passed.
- `uv run --locked yapf --in-place <changed Python files>`, followed by
  `uv run --locked yapf --diff <the same files>`: passed, exit 0. Fixtures were not formatted.
- `uv run --locked pytest`: **728 passed, 2 skipped**, 509.30 seconds. The existing
  Starlette/httpx deprecation warning remains. An earlier run exposed the v1 inspection
  gate regression; the corrected implementation passed this full rerun.
- `uv run --locked pytest tests/test_registry.py tests/test_engine_upgrade.py tests/test_engine_api.py tests/test_engine_streams.py -q`:
  **95 passed**.
- From `web/`: `npm run typecheck`, `npm run lint`, `npm test` (**66 passed**), and
  `npm run build`: passed. Prettier checks on all changed web files passed.
- Both installed-tool commands above: **PASS**, including temporary-directory cleanup.

No material implementation questions remain. Historical-version compatibility and Linux
execution have the limits described above; no Claude parity claim is made by this session.
