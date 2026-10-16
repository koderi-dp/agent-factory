# Session 1.10: Codex Full end-to-end

Captured on Windows on 2026-10-16. Claude and mixed-provider parity remain **pending
session 1.11**. The Astra phase review and Sol review fixes are separate, pending steps.
This session does not claim release readiness.

## Reproduce

From this checkout, with Python 3.11+, Git, Node and a logged-in Codex CLI:

```powershell
uv sync --locked
uv run --locked python tools/sandbox.py new --dir "$env:TEMP/factory-e2e-fake"
uv run --locked python tools/sandbox.py run --dir "$env:TEMP/factory-e2e-fake" --ports 32000-32899
uv run --locked python tools/sandbox.py new --dir "$env:TEMP/factory-e2e-real"
uv run --locked python tools/sandbox.py run --real --dir "$env:TEMP/factory-e2e-real" --ports 31000-31899
```

Each suite contains two Git repos and its own `machine/` FACTORY_HOME. All three backlog
items in both repos are started before the first scheduling tick. Both use the built-in
Full pipeline, Codex `gpt-6-luna` / `low`, per-job limit 4, global limit 6, one round and
automatic merging. Bypass permissions apply inside these throwaway repos. The harness
uses the checkout's Runtime, scheduler, wrapper, packets, report/git validation, port
leases and merge implementation. Fake mode replaces only the CLI process; it retains
the production Codex parser and classifier.

The fake CLI commits the actual changes, runs Node tests, and binds every assigned port
for its execution. The harness independently checks all three features on both main
branches, including title changes after toggling and adding a todo, and runs `node --test`.
It checks concurrency, distinct leases and their release. Job IDs intentionally repeat
across repos. Screenshots and browser sessions are isolated per attempt.

The real harness adds `--ignore-user-config` to the production command, preserving saved
authentication and project instructions. This keeps user configuration/MCPs out of the
controlled experiment. It does not add `--ignore-rules`, change the installed Codex config,
or change the production adapter. This flag is documented in
[official non-interactive mode guidance](https://learn.chatgpt.com/docs/non-interactive-mode).

Use distinct port ranges for suites running together, including Windows and WSL: WSL's
localhost relay can hold the same Windows ports. Windows tests use 32000-32899; Linux
uses 33000-33899. `status` reads the suite. `--resume` explicitly continues its existing
six jobs and resumes paused jobs, preserving attempts and reports. Concurrency checks
include historical attempt intervals. A fresh `run` refuses existing jobs. No successful
run automatically deletes evidence. `bench --dir <legacy-v1-repo>` still reads the original
v1 captures; new-engine benchmarking remains session 2.3.

## Captured results

Final fresh real run: `%TEMP%/factory-110-real-final`, Codex CLI **0.162.1**, all six
Full jobs merged in **238.02 seconds** with **no manual intervention**. There were
42 valid attempts and two automatic contract retries for missing reports: **44 attempts**
total, no stopped or failed attempts. Peak concurrency was six attempts across both repos.
The six distinct blocks were 31000 through 31050 in increments of ten, all released after
merging. Both main branches passed the independent button/title/duplicate checks and
`node --test`. The final run started with the controlled `--ignore-user-config` harness
and did not reproduce the earlier shutdown hang. The summary is saved in
[`evidence/session-1.10-codex.json`](evidence/session-1.10-codex.json).

Fake acceptance: six merged jobs, 42 valid attempts, one invocation per execution, no
retries, peak six live attempts across two repos. Every allocated test port was bound
successfully; no listeners or leases remained after merging. The acceptance test also
covers spaces/non-ASCII paths, UTF-8 packets, unique browser sessions, backlog checkboxes,
and continuing a completed suite without relaunching anything.

Earlier completed real capture (Stop/Resume exercise): `%TEMP%/factory-110-real-4`, Codex CLI **0.162.1**. All six jobs merged;
42 valid attempts, one invalid report contract retry, and two explicitly stopped attempts
resumed with their exact recorded session IDs: **45 attempts total**. Peak concurrency
from attempt history was six attempts across two repos. Lease blocks were 31000, 31010,
31020, 31030, 31040 and 31050; all were released. Both main branches passed independent
feature checks and their Node suites.

| Repo | Feature | Final state | Valid / total attempts |
|---|---|---|---:|
| todo-app-1 | Add todo button text | merged | 7 / 7 |
| todo-app-1 | Open count in page title | merged | 7 / 7 |
| todo-app-1 | Ignore duplicate titles | merged | 7 / 7 |
| todo-app-2 | Add todo button text | merged | 7 / 7 |
| todo-app-2 | Open count in page title | merged | 7 / 8 |
| todo-app-2 | Ignore duplicate titles | merged | 7 / 9 |

The real suite ran from 00:32:14.853 to 00:42:51.314 UTC, including diagnosis and the
explicit Stop/Resume. The final `--resume` verification took about one second and launched
nothing; its `result.json` wall time describes that verification, not the full run.

**Qualification of the earlier capture:** it was not an uninterrupted clean real run. Two native Codex
processes emitted `turn.completed` and valid reports but did not exit for several minutes.
The engine correctly waited for exit evidence. They were explicitly stopped and resumed;
the continuation with `--ignore-user-config` exited normally. The cause of the original
CLI shutdown hang is not established. No exit code was invented, no report was manually
approved, and no terminal event was substituted for `exit.json`.

Earlier unsuccessful captures are retained in `%TEMP%/factory-110-fake`,
`factory-110-fake-2`, `factory-110-real`, `factory-110-real-2`, and `factory-110-real-3`.
They are diagnostic evidence, not successful runs. Pytest captures remain in its temporary
suite directories. Existing `tests/fixtures/` evidence was not changed.

## Verified fixes

- Recheck a conflicting listener before pausing: a parallel peer can exit between the
  socket snapshot and ownership inspection. A remaining foreign listener still blocks.
  Regression: `test_parallel_peer_exits_between_listener_snapshot_and_ownership_check`.
- Specify the exact report contract in each packet and clarify spec headings versus task
  status lines. Explain how inherited v1 report names map to the absolute attempt report,
  and that v2 has no `job.json`. Regression: the exact-contract packet test.
- Put screenshots/validation artifacts outside the shared worktree and give every attempt
  its own browser session. Real UI screenshots had dirtied a peer's checkout, and v1
  session names reused job IDs across repos. Regression: packet isolation and the six-job
  fake acceptance test. Substantive role rewriting remains Phase 2.
- Treat changed backlog checkboxes as expected main-checkout edits while still rejecting
  other uncommitted changes. Regression: fake acceptance verifies both cases.
- Correct the v1 POSIX `lsof` comma-separated port argument, discovered by the full Linux
  suite. The existing leftover-server merge test now also checks the full multi-port
  query before merge and launches the base interpreter, so its PID is the actual
  listener rather than a Windows virtualenv redirector. This is the only v1 runtime
  behavior change beyond formatting.
- Use real base hashes in the Codex adapter's Git fixture (lowercase `head` accidentally
  resolves on Windows) and choose a distinct old version even for source archives whose
  installed version is `0.0.0`. The existing integration tests cover both corrections.

## Formatting and tests

The initial baseline used the required commands:

```powershell
uv run --locked yapf --in-place --recursive .
uv run --locked yapf --diff --recursive .
uv run --locked pytest
```

The recursive formatting check exited zero. Fixture contents were preserved. AST comparison
confirmed 41 existing Python files changed only in formatting; other Python changes are
limited to the harness, fake CLI, fixes and regression tests described above.

Final Linux `uv run --locked pytest`: **621 passed, 4 skipped**, 244.22 seconds.
The skips cover three Windows-only cases and the installed-Codex help probe
(Codex is absent in WSL). The one existing warning is Starlette's httpx TestClient
deprecation. The final base-interpreter server fixture also passed separately in Linux
(0.86 seconds) and Windows (28.44 seconds).

Final Windows `uv run --locked pytest`: **625 passed**, 313.41 seconds, with the
same existing Starlette deprecation warning. The final recursive YAPF check exited zero
on both platforms. `git diff --exit-code -- tests/fixtures` and `git diff --check`
also exited zero.
Linux uses an isolated source copy under `/tmp/agent-factory-110`, its own `uv sync --locked`
environment (Python 3.12.3), temporary uv/Node tooling, and the same commands. Windows uses
Python 3.11.15. No development dependency was added.
