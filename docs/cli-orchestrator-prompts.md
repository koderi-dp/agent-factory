# CLI orchestrator: agent prompts per phase

Companion to [`cli-orchestrator-plan.md`](cli-orchestrator-plan.md). Each block below is one
Codex session: start a fresh session, set the model and effort shown, paste the prompt.
Sessions within a phase run in order. Each one ends with tests passing and a commit.

Model and cost rules are in plan §17.0. In short: Sol is the default, Astra only after Sol
has failed twice on engine-core correctness, Luna for mechanical work, and one Claude
cross-review per phase.

## Automatic: `tools/run_plan.py` (recommended)

```powershell
python tools/run_plan.py              # run the next phase's sessions, review, fix, then stop
python tools/run_plan.py --dry-run    # show what would run, with models and effort
python tools/run_plan.py --status     # which sessions are done
```

What the runner does:
- **Sessions:** it runs each session of the next phase in order with `codex exec`, using the
  model and effort from the session's heading.
- **Done check:** a session counts as done only when Codex ends with `SESSION-RESULT: PASS`,
  a `[session X.Y]` commit exists, and the working tree is clean.
- **Failures:** a failed session is retried once. Session 1.5 then escalates to Astra. If it
  still fails, the runner stops and tells you why.
- **End of each phase (from Phase 1):** Claude reviews the phase diff and writes the findings
  to `docs/reviews/phase-N.md`. A Codex fix session then works through them.
- **Pause:** the runner stops at the end of each phase for you to look things over. Run it
  again to continue.
- **Manual work:** it stops before sessions that need you (4.4).

Codex runs with `--dangerously-bypass-approvals-and-sandbox` by default (`--permissions
bypass`). Sessions need network access, package installs, and nested `claude`/`codex` runs.
Use `--permissions auto` for the sandboxed `--approve-for-me` mode.

Logs go to `.git/plan-run/`, so they're never committed. Progress is read from git history,
so it's safe to stop and rerun at any time.

## Manual: one session at a time

No copy-pasting is needed. Codex reads this file itself.

1. Start a fresh Codex session in this repo with the session's model and effort:
   ```powershell
   cd C:\Source\projects\agent-factory
   codex -m gpt-6.1-sol -c model_reasoning_effort="medium"
   ```
   (or start `codex` and pick them with `/model`). Model names: `gpt-6.1-sol`, `gpt-6-astra`,
   `gpt-6-luna`. Effort values: `low`, `medium`, `high`, `xhigh`.
2. Send one line, with the session number changed:
   ```text
   Read docs/cli-orchestrator-prompts.md. Do session 1.4: follow the "Shared preamble" and then the prompt for session 1.4.
   ```
3. When it finishes (tests run, commit made), close it. Start a **new** session for the next one.
4. After the last session of a phase, run the cross-review prompt (bottom of this file) in
   Claude Code, then a fix session in Codex with the findings.

`{PREAMBLE}` in the prompts below means "apply the Shared preamble". Codex does that when you use the one-liner.

## Shared preamble

Every prompt below starts with `{PREAMBLE}`. Paste this text in its place:

```text
You are implementing part of the agent-factory rewrite described in
docs/cli-orchestrator-plan.md. That document is the spec. Read the sections named below
before writing code, and follow them exactly. If the spec is ambiguous or seems wrong,
stop and ask instead of improvising. Note the question in your final message.

Rules:
- Work on the current branch. Commit at the end with a clear message; do not push.
- Match the existing code style. Python 3.11+, type hints, stdlib-first, no new
  dependencies beyond plan §2 without asking.
- Windows is a first-class platform: paths, process handling, file locks, line endings.
- Tests define done. Write or port the tests listed for this session, and run them.
  Report the exact test command and its result. Never claim tests pass without running them.
- Don't implement later sessions' scope. Leave a TODO with the plan section reference
  where a later session plugs in.
- Final message: what you built, files changed, test results, open questions, and
  anything that deviates from the spec and why.
- Commit message starts with "[session <number>]", e.g. "[session 1.4] Add run_cli wrapper".
- The very last line of your final message is exactly one of:
  SESSION-RESULT: PASS
  SESSION-RESULT: FAIL <one-line reason>
  PASS only if the session's "done when" criteria are met, the tests ran green, and the work
  is committed with a clean working tree.
```

---

## Phase 0: Baseline and spikes

### 0.1 Baseline capture · Luna · Medium

```text
{PREAMBLE}

Read: plan §0 (success metrics), §17 Phase 0, tools/sandbox.py, factory/job.py.

Task: add a `bench` subcommand to tools/sandbox.py that, after a sandbox run, reads each
job's job.json history and the headless logs and prints a table per job: wall time, time
per stage, number of agent runs, and tokens/cost if they appear in the logs (otherwise
"unknown"). Also write it as Markdown to docs/benchmarks.md under a "v1 baseline" heading.
Do not change factory/ runtime code.

Done when: running `python tools/sandbox.py bench` on an existing sandbox prints the table,
and a unit test covers the parsing with a fixture job.json.
```

Then run the baseline yourself: the sandbox's 3 features × 2 runs with the current factory.

### 0.2 CLI behaviour spikes · Sol · Medium

```text
{PREAMBLE}

Read: plan §6.5, §8.1, §13.4, §14, §17 Phase 0 spikes 1–4, 6, 7, §18.

Task: write throwaway scripts under spikes/ (not shipped) that answer spikes 1–4, 6 and 7
by actually running `claude -p` and `codex exec` against a scratch git repo:
1. A denied command under `claude -p --permission-mode auto`: exit code, the
   `permission_denials` field, and whether the session continues.
2. Claude stream-json: capture full event logs for a success, an auth failure (bad config
   dir), and a long tool run. Save them as fixtures in tests/fixtures/claude/.
3. `codex exec --json`: the same three cases, saved as fixtures in tests/fixtures/codex/.
   Include behaviour under --approve-for-me on a denied action.
4. Whether codex exec accepts developer/system instructions via -c, versus prepending them to the prompt.
6. `--session-id` then kill the process, then `--resume` with -p: does it continue the same session?
7. Two consecutive runs with an identical system prompt file: report cached-input tokens from usage.

Done when: docs/spikes.md answers each question with evidence (commands, exit codes,
trimmed event excerpts), and the fixtures are committed. Update plan §18 items 1–2 with the answers.
```

### 0.3 Windows process spikes · Sol · High (run on Windows)

```text
{PREAMBLE}

Read: plan §3.2, §9 (Stop), §15.4, §17 Phase 0 spikes 5 and 8.

Task: under spikes/windows/, write and run:
5. A launcher that starts a stdlib Python wrapper with CREATE_NEW_PROCESS_GROUP |
   CREATE_NO_WINDOW. The wrapper creates a Job Object with KILL_ON_JOB_CLOSE, assigns itself,
   and starts `npm run dev` (use a tiny Vite or Node http server in a temp dir). Verify
   that terminating the job kills the whole tree, including node.exe, and frees the port.
   Also test: the launcher exits, and the wrapper and its tree keep running.
8. Install a dummy package with `uv tool install` that starts a long-running process using
   sys._base_executable and a script copied to a temp dir. While it runs, run
   `uv tool install --force` of a newer version. Confirm no file-lock failure.

Done when: docs/spikes.md has a Windows section with results, and the ctypes Job Object
code that worked is saved as spikes/windows/winjob_proto.py for Phase 1 to reuse.
```

### 0.4 Names · Luna · Medium

```text
{PREAMBLE}

Read: plan §18 item 3.

Task: check whether the PyPI name `agent-factory` and alternatives are free (use the
PyPI JSON API: https://pypi.org/pypi/<name>/json → 404 means free). Check whether common
tools install a `factory` command (search PyPI and npm for packages exposing it). Write
findings and a recommendation to docs/spikes.md under "Names". Do not register anything.
```

---

## Phase 1: Package, engine and runner

### 1.1 Skeleton, DB, registry · Sol · Medium

```text
{PREAMBLE}

Read: plan §2, §4 (all), §6.2 (pragmas paragraph), §12.1 (command list only).

Task:
- Create pyproject.toml (hatchling, hatch-vcs, entry points `agent-factory` and `factory`
  → agent_factory.cli:main, the deps from §2) and src/agent_factory/ with __init__.py,
  paths.py (machine dir via platformdirs, FACTORY_HOME override, repo discovery by walking
  up to factory/config.json), and engine/db.py (connection factory with the §6.2 pragmas,
  a tx() context manager using BEGIN IMMEDIATE, and a migrations runner using
  PRAGMA user_version over numbered .sql files).
- Add migrations/machine/0001_init.sql (§4.3) and engine/registry.py (register,
  list, remove, rename, mark missing).
- cli.py: argparse with all §12.1 subcommands registered. Implement only `version`
  and `repos`; the rest print "not implemented yet (plan §…)".
- Do not touch the existing factory/ v1 code.

Tests: db migrations apply once and are idempotent; tx rolls back on error; registry
ids are stable for a path; FACTORY_HOME is respected. Run with `uv run pytest`.
```

### 1.2 Config and roles · Sol · High

```text
{PREAMBLE}

Read: plan §5 (all), §10.5, factory/pipeline.py and factory/config.json (v1, for upgrade
behaviour), claude/agents/*.md and codex/agents/*.md (current role texts).

Task:
- engine/config.py: Pydantic v2 models for the §5.1 schema with every listed rule, the §5.4
  v1→v2 upgrade (pure function), and machine settings §5.2 stored in machine.db.
- engine/roles.py: the §5.3 resolution order, frontmatter parsed with yaml.safe_load,
  and a validation error listing every path tried.
- Move the current built-in role texts into src/agent_factory/roles/ (unchanged for now;
  Phase 2 rewrites them), with .codex.md variants where the codex text differs.
- templates/config.json with the full and fast pipelines from §5.1.

Tests: one test per validation rule in §5.1; v1→v2 on the current factory/config.json
and on the README's example; role resolution order, including no cross-provider fallback;
roles may repeat in a parallel group.
```

### 1.3 Pipeline port · Sol · High

```text
{PREAMBLE}

Read: plan §6.1–§6.3, §7.2, §10.1, §10.4, §10.5, and factory/job.py (advance, rewind,
rework, step_plan, read_result) with tests/test_pipeline.py and tests/test_job.py.

Task: migrations/repo/0001_init.sql exactly as §6.2. engine/pipeline.py: pure functions
that take the job, executions and results, and return the transition (next step's pending
executions, rewind, needs_human with reason, approved). Apply it in one tx. engine/reports.py:
first-line contracts and the §7.2 checks that don't need git (freshness, verdict parsing,
section extraction).

Port every relevant case from tests/test_pipeline.py and tests/test_job.py to the new
model, keeping the same behaviour. List any case you deliberately changed and cite the plan
section that changes it.
```

### 1.4 Wrapper and Job Objects · Sol · Extra High

```text
{PREAMBLE}

Read: plan §3.2, §3.3, §9 (Stop, Ownership), docs/spikes.md (Windows section),
spikes/windows/winjob_proto.py.

Task: src/agent_factory/run_cli.py (stdlib only; it must run under the base interpreter
with no package imports) implementing steps 3–6 of §3.2, including the `stop` flag file.
engine/winjob.py for the engine side. The engine-side helper copies run_cli.py to
<machine-dir>/runtime/run_cli-<sha8>.py and spawns it with sys._base_executable and the
platform flags.

Atomic writes: write to a temp file, then os.replace. proc.json is written before anything else.

Tests (Windows and POSIX):
- the CLI is replaced by tests/fake_cli.py, a minimal version that sleeps, writes a
  report, spawns a child listening on a port, and exits with a given code;
- events.jsonl receives output; exit.json has the exit code;
- the stop flag kills the whole tree and frees the port;
- the wrapper survives its launcher exiting.
```

### 1.5 Claim, launch, record, recovery · Sol · Extra High (Astra · High after two failed attempts)

```text
{PREAMBLE}

Read: plan §6.4 (every word), §6.5, §3.3, §16.3, and the code from sessions 1.1–1.4.

Task: engine/scheduler.py and engine/supervisor.py:
- the claim → prepare → spawn → record protocol with the one_live_attempt index;
- per-job and global limits, round-robin across repos;
- re-adoption and the recovery table, applied at service start before scheduling;
- tailing events.jsonl by offset, persisting log_offset;
- exit classification as a pluggable adapter interface (adapters come in 1.6; use a stub
  adapter here).

Add FACTORY_CRASH_AT injection points exactly as named in §16.3.

Tests: every row of the §6.4 recovery table; every crash point in §16.3, asserting
fake-CLI invocation counts; concurrent claims never produce two live attempts; limits hold
under 2 repos × 3 jobs × 4 executions. Run the crash tests 20 times in a loop and report
any flakiness. Flaky is a failure.
```

### 1.6 Adapters · Sol · High

```text
{PREAMBLE}

Read: plan §6.5, §8.1, §13.4, §14, docs/spikes.md, tests/fixtures/claude/ and tests/fixtures/codex/.

Task: engine/adapters/{base,claude,codex}.py. Each adapter:
- builds the command line (§8.1), using machine settings for executable paths;
- parses events incrementally (session id, usage, errors, permission denials);
- implements summarize(event) (§13.4, with secret masking);
- classifies the exit (§6.5).

Codex: get the session id from thread.started. Claude: the session id is preassigned.

Tests: every fixture from the Phase 0 spikes parses; the classification table in §6.5 is
covered row by row; the summarize() table in §13.4 is covered row by row, including masking.
```

### 1.7 Ports, stop, timeouts · Sol · Extra High

```text
{PREAMBLE}

Read: plan §9, §4.3 (port_leases), §6.3 (paused reasons).

Task: engine/ports.py (machine-wide leases per §9, a listener check via psutil, release
rules) and stop/timeout handling in the supervisor. A job without a free block → paused(ports).
A foreign listener → paused(ports) with the owner's pid and name. Never kill a process
outside the attempt's tree.

Tests: as listed under "Ports" and "Process trees" in §16.2.
```

### 1.8 Packet and git ops · Sol · High

```text
{PREAMBLE}

Read: plan §7.1, §7.2 (git-dependent checks), §8 (all), §11, and factory/job.py
(git, branch_head, merge, delete, free_ports).

Task: engine/gitops.py (port merge/delete/worktree logic with its rules unchanged) and
engine/packet.py (§8.2 sections, the 64 KiB cap, the truncation order, omitted markers,
system.md assembly with byte-stable output).

Tests: port the merge tests from tests/test_job.py (busy, conflict, changed head); packet
cap and truncation order; system.md is byte-identical across two jobs with the same role.
```

### 1.9 CLI commands and API · Sol · High

```text
{PREAMBLE}

Read: plan §3.1, §12 (all), §13.1, §13.2, §16.2 (API security, Registry, Version gate).

Task: the FastAPI app (security middleware per §13.1, routes per §13.2, polling-friendly
responses; SSE comes in Phase 3), the service single-instance lock and service.json, the
auto-start from CLI commands, `factory init` (§12.2) with templates and skill wrappers
(§12.3), `doctor`, the job commands, `serve`, `service`, the version gate and exit codes.
Add a minimal read-only HTML page at / that lists jobs per repo (no build; a placeholder
until Phase 3).

Tests: the §16.2 API security list; init is idempotent and writes exactly §4.2's files;
the version gate exits 4 with the fix text; a second `serve` exits 0 and prints the URL.
```

### 1.10 End-to-end and review fixes · Sol · High

```text
{PREAMBLE}

Read: plan §17 Phase 1 exit criteria.

Task: make tools/sandbox.py drive the new engine (FACTORY_HOME in a temp dir, two sandbox
repos). Run Full on the 3 features in both repos at once with the fake CLI, then once with
real CLIs at haiku/low and Luna/Light. Fix whatever these runs expose; each fix gets a test.
(Claude's phase review and its fixes run after this session, in a separate step.)
```

---

## Phase 2: Fast pipeline and measurement

### 2.1 Roles rewrite · Sol · High

```text
{PREAMBLE}

Read: plan §5.3 (budgets), §7.2, §8.2 (last paragraph), §10.2, and all files in
src/agent_factory/roles/.

Task: rewrite every built-in role to:
- start from the packet, without re-reading the whole history;
- write the §7.2 sections;
- stay within the §5.3 size budgets.

Keep every substantive check the current roles make; list anything you removed and why.
Write the new fast-approver role per §10.2, keeping the approver's escalation list.

Done when: a test asserts budgets and required section names per role, and a diff summary
per role is in your final message.
```

### 2.2 Fast pipeline, sensitive paths, eject · Sol · Medium

```text
{PREAMBLE}

Read: plan §7.2 (approval CHANGES), §10.1–§10.4, §5.3 (eject), §12.1.

Task: approval CHANGES with onChanges; the sensitive-path guard (§10.3) for auto and human
merges, with --confirm-sensitive; `factory eject` with provenance frontmatter, `--diff`,
and outdated detection in `factory status`.

Tests: Fast happy path and CHANGES loop; auto-merge blocked on a sensitive path; a human
merge requires confirmation; eject provenance and outdated detection.
```

### 2.3 Benchmark · Sol · Medium

```text
{PREAMBLE}

Read: plan §0 (metrics), §14, §17 Phase 2.

Task: extend `tools/sandbox.py bench` to read the new engine's attempts (timings, usage,
packet bytes) and print the same table as the v1 baseline, plus Fast-vs-Full columns. Append
results to docs/benchmarks.md.
```

---

## Phase 3: Dashboard

### 3.1 Scaffold and theme copy · Luna · Medium

```text
{PREAMBLE}

Read: plan §13.3 (stack, source and build), §13.7.

Task: create web/ with Vite + React 19 + TypeScript + Tailwind v4, build output to
src/agent_factory/dashboard_dist/. Copy these files from C:\Source\projects\srt-gateway\web
exactly as they are:
- src/styles.css
- src/ui/primitives/*
- src/ui/status.tsx, src/ui/status-text.ts, src/ui/page-header.tsx, src/ui/page-shell.tsx,
  src/ui/confirm-dialog.tsx, src/ui/settings-section.tsx, src/ui/utils.ts
- components.json
- eslint, prettier and tsconfig settings

Also copy the matching dependency versions from its package.json. Write web/src/ui/SOURCE.md
with the srt-gateway commit hash. Add an empty factory.css layer. Serve the build from the
engine at / instead of the Phase 1 placeholder.

Done when: npm run build, typecheck, and lint pass, and the served page shows a primitives
test page.
```

### 3.2 Data layer · Sol · High

```text
{PREAMBLE}

Read: plan §13.2, §13.3 (state and data flow), §13.4 (delivery), and the API route code.

Task:
- Engine: the SSE endpoints (repo events with Last-Event-ID replay and reset, overview
  stream, attempt log stream, in-memory activity events).
- Web: lib/api.ts (zod schemas mirroring the API), lib/sse.ts, lib/reducer.ts (pure),
  lib/format.ts, and applying the reducer to the TanStack Query cache.

Tests: pytest for SSE replay, reset, and batching. Vitest for the reducer (every event type,
reset, out-of-order) and format.
```

### 3.3 Shell, overview, board · Sol · Medium (Ultra allowed)

```text
{PREAMBLE}

Read: plan §13.3 (features), §13.4, §13.6 (board compact strip only), §13.7.

Task: shell.tsx (repo switcher with needs-you counts, nav, connection state), the overview
page, the board (columns per step, JobCard with chips, ActivityLine, compact pipeline
strip, pause/needs-you badges, legacy section). Use only the copied primitives and theme
tokens; no ad-hoc colours.
```

### 3.4 Job detail and pipeline view · Sol · High for layout.ts, Medium for the rest

```text
{PREAMBLE}

Read: plan §13.3, §13.5, §13.6 (all), §13.7.

Task: layout.ts exactly per §13.6 (pure), plus PipelineView, PipelineNode, PipelineEdge, and
GroupFrame. JobDetail with the Pipeline / Reports / Timeline / Log / Usage tabs, the node →
Sheet behaviour, ReportView per §13.5, LogView (virtualised), PauseBanner, and actions
(Stop/Resume/Merge/Rework/Delete with ConfirmDialog).

Tests: Vitest for layout.ts (the §16.4 cases) and for hostile report rendering.
```

### 3.5 Backlog and settings · Sol · Medium

```text
{PREAMBLE}

Read: plan §12.4, §13.3 (settings, unsaved edits), §13.6 (Settings preview), §5.1–§5.3.

Task: backlog page (add, drag reorder, remove, Start with a pipeline selector). Repo
settings (pipelines table + JSON editor, field errors from the API, dirty-state guard,
provider switch keeping profiles, live pipeline preview with invalid-node highlighting,
roles list with source and eject status). Machine settings (global concurrency, CLI paths,
ports range).
```

### 3.6 E2E and visual tests · Sol · Medium

```text
{PREAMBLE}

Read: plan §16.4.

Task: the Playwright suite listed in §16.4 against the engine with the fake CLI, with
screenshot baselines (dark) for the overview, board, job detail, and pipeline view, plus
axe-core contrast checks. Wire it into CI (Windows + Linux).
```

---

## Phase 4: Release, migration, switch-over

### 4.1 CI and release pipeline · Sol · High

```text
{PREAMBLE}

Read: plan §15.2 (every step), §16.5.

Task: .github/workflows/ci.yml and release.yml exactly per §15.2:
- matrix tests;
- dashboard build into the wheel, with a check that the wheel contains dashboard_dist;
- smoke install on Windows and Linux;
- PyPI trusted publishing, using a TestPyPI environment for rc tags;
- GitHub Release with SHA256SUMS and actions/attest-build-provenance.

Pin every action to a full commit SHA. Use least-privilege permissions per job.
Don't add secrets. Trusted publishing needs none.
```

### 4.2 Upgrade, migration, version gate · Sol · High

```text
{PREAMBLE}

Read: plan §12.2 (upgrade), §15.4, §15.6, §16.5, and the v1 layout of this repo's factory/.

Task:
- `factory upgrade` with --dry-run, including the full v1 migration in §15.6 (ship hashes of
  the v1 role texts so unchanged roles can be detected);
- legacy jobs read-only in the API;
- service restart when the running service's version differs.

Tests: a v1 fixture repo with one customised and one unchanged role migrates as specified;
legacy jobs list read-only; an upgrade with a running attempt re-adopts it (§16.3 upgrade test).
```

### 4.3 Installer, README, removal · Luna · Medium

```text
{PREAMBLE}

Read: plan §15.3, §15.5, §17 Phase 4.

Task:
- reduce install.py to the optional uv bootstrap in §15.3;
- rewrite README.md around `uv tool install agent-factory` + `factory init`, keeping its
  user-facing sections (pipeline, needs-you, dashboard, what a job leaves behind) updated to
  the new behaviour;
- remove the v1 runtime (factory/*.py, factory/dashboard/), claude/ and codex/ role sources
  now moved into the package, tools/sync_codex.py, and the obsolete tests;
- update tests/test_install.py.
```

### 4.4 Release candidate and real migration · you + Sol · Medium

```text
{PREAMBLE}

Read: plan §17 Phase 4 exit criteria.

Task: tag v1.0.0rc1 and confirm the release workflow publishes to TestPyPI. Then, step by
step with me, install it with uv on this machine and run `factory upgrade --dry-run` in
C:\Source\projects\srt-gateway and in pylon. Review the diffs with me before applying them.
Then run one Fast job in each repo at the same time. Report any failure as an issue with
reproduction steps. Don't fix things in this session unless I ask.
```

---

## Phase 5 (conditional)

Only if Phase 2's benchmarks meet the §17 Phase 5 trigger. Write that phase's prompt then,
from the measured bottleneck.

---

## Cross-review prompt (Claude, once per phase)

Run in Claude Code on the phase's diff, at the effort shown in the phase's model table:

```text
/code-review high

Context: this diff implements Phase <N> of docs/cli-orchestrator-plan.md. Review it against
that spec, not just for generic bugs. In particular:
- every rule in the sections listed for this phase's sessions in docs/cli-orchestrator-prompts.md;
- duplicate-launch, crash-recovery, and process-ownership guarantees (Phase 1);
- Windows behaviour;
- tests that assert the spec's behaviour rather than the implementation's.

Rank findings most severe first.
```

`tools/run_plan.py` does this automatically: Claude writes `docs/reviews/phase-N.md`, then a
Codex session (Sol · High) fixes the findings and commits with `[phase N review fixes]`. When
running manually, do the same: save the findings to that file and run a fix session.
