# CLI orchestrator: a deterministic Python engine for the factory

Status: proposal · Replaces the "AI session as orchestrator" model and the copy-into-every-repo install

## 0. Summary

Today the factory's orchestrator is an LLM session (Claude Code or Codex) following
`references/workflow.md`. It spawns subagents, calls `job.py step/advance`, and needs a
`/factory watch` session or a headless watcher to react to the dashboard's Start button.
That costs orchestrator tokens on every step, makes behaviour depend on how well a model
follows ~150 lines of rules, and needs a client session to stay open for a job to finish.
The whole runtime is also copied into every project repo, so each repo must be updated
separately on every release.

This plan makes three changes:

1. **A deterministic engine.** A small Python **service** runs one headless CLI process
   (`claude -p` or `codex exec`) per pipeline execution, validates reports, advances the
   pipeline, and merges. It keeps state in SQLite and streams progress to the dashboard.
   Agents do only the work: spec, build, review, approve.
2. **A global install.** The engine, dashboard, and built-in roles are installed **once per
   machine** as a normal tool (`uv tool install agent-factory`). Each project repo commits only
   its own small setup (config, backlog, role overrides, thin skill wrappers), created by
   `factory init`. One service and one dashboard serve every repo on the machine.
3. **A React dashboard in srt-gateway's design.** It's built in CI and shipped inside the
   Python package, so users never need Node. It includes a live pipeline view and a
   current-activity line for every running agent.

The work is split into phases (§17). Each phase ships something usable and has exit criteria.
Speculative optimisations (code index, evidence reuse) run only when measurements justify them.

### Goals

1. **Correctness first:** no step is skipped silently, no CLI is launched twice, no merge happens
   on an unreviewed head, and the engine recovers after a crash at any point.
2. **Lower latency and token use:** no orchestrator model, a 2-call **Fast** pipeline, and
   focused handoffs instead of agents rereading the whole job history.
3. **Unattended operation:** Start in the dashboard runs a job to completion without any client
   session left open.
4. **Mixed providers:** each execution chooses Claude or Codex, so one review group can mix them.
5. **One install, many repos:** upgrade once per machine; repos carry only project-specific files.
6. **Measured, not promised:** per-phase timings and token usage for every attempt.

### Non-goals

- A model gateway, embeddings, vector store, or database server.
- Migrating in-flight legacy jobs. They become read-only (§15.6).
- Remote or multi-machine execution. Everything runs on one machine for one user.
- Replacing the CLIs' own retrieval. Agents still search the repo themselves.

### Success metrics (baselined in Phase 0)

| Metric | How measured |
|---|---|
| Wall time per job, Fast and Full, on the sandbox's 3 features | `jobs.created_at` → terminal state |
| Engine overhead (time not spent inside a CLI) | total − Σ attempt process time |
| Startup latency per attempt | spawn → first CLI event |
| Tokens per job (input / cached input / output) and cost where reported | CLI usage events |
| Agent calls per job | count of attempts |
| Crash safety | crash-injection tests (§16.3): zero duplicate launches |

Targets are set **after** Phase 0, as relative improvements over the baseline. The
expectation is that Fast uses ≤ 40% of Full's tokens and the engine adds < 2 s per execution.

---

## 1. Current state (what is being replaced)

| Component | Today | After |
|---|---|---|
| Orchestration | LLM session following `workflow.md` | `agent_factory.engine` (Python) |
| Distribution | `install.py` copies the runtime into each repo | `uv tool install agent-factory` once per machine; `factory init` per repo |
| Job state | `factory/jobs/<id>/job.json` + `.lock` files | SQLite per repo: `factory/state/factory.db` (gitignored) |
| Start from dashboard | request files + `/factory watch` or a headless watcher session | engine queue, launched directly |
| Codex headless | `codex_runner.py` wraps one long session | one `codex exec` per execution |
| Dashboard server | stdlib `http.server` per repo, polling | one FastAPI service per machine, SSE, all repos |
| Dashboard UI | `dashboard.js` (~1000 lines), no build | React + TypeScript + Tailwind + shadcn, built in CI, shipped in the wheel |
| Roles | 7 files copied into `.claude/agents/` and `.codex/agents/` per repo | built into the engine; repos hold overrides only |
| Skills | full orchestration instructions | thin wrappers around the `factory` command |
| Ports | `5100 + 10n` per repo (collides across repos) | machine-wide port leases |
| Dependencies | stdlib only | pinned deps in the tool's own venv, managed by uv |

Kept as-is in behaviour, and ported with their tests: worktree/branch layout, report
contracts (first-line `STATUS:`/`VERDICT:`/`APPROVE`), the `advance` semantics (clean
worktree, exact reviewed head, builder must commit, `onChanges` rewind, `maxRounds` budget),
merge rules (busy checkout = exit 3, refuse a changed head), "never push", and
localhost-only serving with Host/Origin checks.

---

## 2. Technology choices

| Area | Choice | Why |
|---|---|---|
| Python | **3.11+**, provisioned by uv when missing | `asyncio.TaskGroup`, better asyncio subprocess on Windows. `uv tool install` downloads a suitable Python itself, so users don't manage it. |
| Distribution | Python wheel on PyPI, installed with `uv tool install` (or `pipx install`) | One isolated venv per machine, simple upgrades and pinning, no Node for users. |
| HTTP + SSE | FastAPI, Uvicorn | Async server in the same event loop as process supervision; native SSE. |
| Validation | Pydantic v2 | Config schema, API bodies, report-section models. |
| Persistence | **stdlib `sqlite3`** | One DB per repo and one per machine, ~7 tables each. No ORM. |
| Migrations | `PRAGMA user_version` + numbered SQL files | Enough for embedded DBs. |
| Processes | `asyncio` subprocess, psutil | Spawn, process identity (`pid` + `create_time`), tree inspection. |
| Windows process trees | Job Objects via `ctypes` (~60 lines) | Kills the CLI and every grandchild (dev servers) together. |
| Frontmatter | PyYAML `safe_load` | Replaces the hand-written parser in `pipeline.py`. |
| Machine paths | platformdirs | Correct per-OS data directory (§4.3). |
| CLI | argparse (stdlib) | Few commands; no extra dependency. |
| Dashboard | React 19, Vite, TypeScript, Tailwind v4, shadcn/ui (Radix), lucide-react, sonner, TanStack Query + Router, react-markdown | Same stack as srt-gateway, so its primitives and theme are reused directly (§13.7). |
| Tests | pytest, pytest-asyncio, HTTPX, Vitest, Playwright | Engine, API, dashboard units, end-to-end UI. |
| Build and release | hatchling (+ hatch-vcs for versions), GitHub Actions, PyPI trusted publishing | Tag → tested, built, published wheel (§15.2). |

**Deferred (Phase 5, only if measurements justify them):** Tree-sitter and SQLite FTS5 for
a code index.

**Rejected:**
- SQLAlchemy/Alembic: heavier than the data model needs.
- Copying the engine into each repo: per-repo upgrades, drifting versions, port collisions,
  and a venv that can't be committed anyway (§15.1).
- Preact + htm without a build: viable, but once CI builds the dashboard, the srt-gateway
  stack gives Radix accessibility and lets us reuse its components unchanged.
- A graph library for the pipeline view: our layout is fixed and simple (§13.6).
- DBOS, LangGraph, Agent Orchestrator: each brings its own state or execution model. We need a
  small deterministic pipeline around long-running external CLIs, with our own
  duplicate-launch guarantees, which none of them provide out of the box.

Runtime Python dependencies: `fastapi`, `uvicorn` (no `[standard]` extras), `pydantic`,
`psutil`, `pyyaml`, `platformdirs`. All have prebuilt wheels for Windows, macOS, and Linux on
3.11–3.13. Version ranges go in `pyproject.toml`. CI tests against `uv.lock`.

---

## 3. Process architecture

```
                ┌──────────────── factory service (one per machine) ───────────────┐
 dashboard ◄─SSE─┤ FastAPI routes ── Engine (asyncio) ── Scheduler ── Supervisor    │
 factory   ─HTTP─┤        │                 │                             │        │
 (CLI/skills)    │  machine.db      repo DBs: <repo>/factory/state/factory.db │ spawn │
                └──────────────────────────────────────────────────────────┼───────┘
                                                                           ▼
                        run_cli.py wrapper (base Python, own process group / Job Object)
                                         │ stdout → events.jsonl (file, not pipe)
                                         ▼
                        claude -p … | codex exec …   (cwd = job worktree)
                                         │
                                         ▼
                        dev servers, test runners, git … (grandchildren)
```

### 3.1 The service

- `factory serve` starts it in the foreground. Every other `factory` command, and therefore
  every skill, starts it detached when it isn't running (§12.1).
- **Single instance per machine.** On start it takes an exclusive lock on
  `<machine-dir>/service.lock` (`msvcrt.locking` on Windows, `fcntl.flock` on POSIX; held for
  the process lifetime). It writes `{pid, create_time, port, token, version}` to
  `<machine-dir>/service.json`, readable only by the user. A second start finds the lock held,
  prints the existing URL, and exits 0.
- It binds `127.0.0.1` only. The default port stays 5099 (`--port` or the
  `FACTORY_PORT` env var change it).
- It serves **every registered repo** (§4.3). Each repo keeps its own SQLite DB, job folders,
  and config. Concurrency limits and ports are machine-wide.
- The service *is* the job runner. With no service, no jobs advance.
- **Version check:** if `service.json` reports a different engine version than the
  `factory` command calling it (e.g. after an upgrade), the command asks the service to
  restart (§15.4).

### 3.2 The wrapper: `run_cli.py`

Every CLI launch goes through a tiny stdlib-only wrapper, so the engine can survive its own
restarts and upgrades without losing or duplicating executions.

1. At service start, the engine copies `run_cli.py` (from the package) to
   `<machine-dir>/runtime/run_cli-<sha8>.py` if it isn't there already.
2. It spawns that copy with the **base interpreter** (`sys._base_executable`, the uv-managed
   Python), not the tool venv's `python.exe`. Running attempts therefore don't lock any file
   in the tool venv, and `uv tool upgrade` works on Windows while jobs run.
   Flags: Windows `CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW`, POSIX `start_new_session=True`.
   Args: `--attempt-dir <dir> --token <owner_token>`.
3. The wrapper atomically writes `<dir>/proc.json` = `{wrapper_pid, wrapper_create_time, token}`.
4. On Windows it creates a Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` and assigns
   itself, so every descendant is in the job. On POSIX the session/process group serves the
   same purpose.
5. It reads `<dir>/command.json` (argv, cwd, env additions, stdin file) written by the
   engine, and runs the CLI with stdout+stderr redirected **to `<dir>/events.jsonl`**, never
   to a pipe owned by the engine.
6. When the CLI exits, it atomically writes `<dir>/exit.json` = `{exit_code, ended_at}`, then exits.

The engine tails `events.jsonl` by byte offset. Because output lives in a file and the exit
code in `exit.json`, a restarted engine can **re-adopt** a running attempt: it checks that
`psutil.Process(wrapper_pid).create_time()` matches and the token appears in its cmdline,
then resumes tailing from the stored offset.

### 3.3 What happens when…

| Event | Behaviour |
|---|---|
| Dashboard tab closed | Nothing. Jobs keep running. |
| Claude/Codex session that ran `/factory` is closed | Nothing. The job belongs to the service. |
| Service stopped normally (`factory service stop`, Ctrl+C) | Running attempts are left running and re-adopted on next start. `--stop-running` stops them instead. |
| Service crashes or the machine sleeps | Wrappers keep running. The next service start re-adopts or finalises them (§6.4). |
| Engine upgraded while jobs run | The service restarts on the new version and re-adopts running attempts (§15.4). |
| Machine reboots | Wrappers are gone. Attempts in `running` with no live process become `lost`, then follow the recovery rules (§6.4). |
| User presses Stop | The engine kills that attempt's process tree (§9) and marks it `stopped`. |
| A registered repo's folder is missing | The repo shows as "missing" in the dashboard; nothing runs for it until it is back or removed (`factory repos remove`). |

---

## 4. Layout: package, repo, machine

### 4.1 The agent-factory source repo (this repo)

```
pyproject.toml           hatchling; entry point  factory = agent_factory.cli:main
uv.lock
src/agent_factory/
  __init__.py            __version__ (from hatch-vcs)
  cli.py                 the `factory` command (§12)
  run_cli.py             stdlib-only wrapper (§3.2)
  paths.py               machine dir, repo paths
  engine/
    db.py                connection factory, pragmas, migrations runner, tx helpers
    migrations/repo/0001_init.sql …     per-repo DB
    migrations/machine/0001_init.sql …  machine DB
    registry.py          registered repos (§4.3)
    config.py            Pydantic config models, v1→v2 upgrade, validation
    roles.py             role resolution: repo overrides → built-ins (§5.3)
    pipeline.py          pure functions: next step, rewind, verdict aggregation (ported from job.advance)
    scheduler.py         claims executions under machine-wide limits
    supervisor.py        launch, tail, re-adopt, stop, exit classification
    adapters/base.py, claude.py, codex.py
    packet.py            builds the execution prompt/handoff (§8)
    reports.py           report parsing and contract validation (§7)
    gitops.py            worktree create/remove, head, diff, merge (ported from job.py)
    ports.py             machine-wide port leases and freeing (§9)
    events.py            event bus: DB insert + in-memory fan-out to SSE
    winjob.py            Job Object helpers (Windows only)
    scaffold.py          `factory init` / `upgrade` / `eject` file generation (§12.2)
    legacy.py            v1 install detection, migration, read-only legacy jobs (§15.6)
  api/
    app.py               FastAPI app, security middleware, static dashboard
    routes_*.py
  roles/                 built-in roles: spec-writer, builder, reviewer-*, approver, fast-approver
    builder.md, builder.codex.md, …       (provider variants where they differ)
  templates/             files `factory init` writes: config.json, backlog.md, SKILL.md (claude, codex)
  dashboard_dist/        (gitignored) built dashboard; included in the wheel
web/                     dashboard source (React, §13.3)
tests/                   pytest; tests/fake_cli.py
tools/sandbox.py         throwaway sandbox repo (uses the engine from this checkout)
.github/workflows/ci.yml, release.yml
```

### 4.2 A project repo (e.g. srt-gateway) after `factory init`

```
factory/
  config.json          committed: pipelines, models, sensitivePaths, "engine": ">=1.0,<2"
  backlog.md           committed
  roles/               committed, optional: overrides and custom roles only (§5.3)
  state/factory.db     gitignored: this repo's jobs, executions, attempts, events
  jobs/<job-id>/       gitignored: request.md, reports, prompts, logs
.claude/skills/factory/SKILL.md    committed: thin /factory wrapper (if Claude selected)
.agents/skills/factory/SKILL.md    committed: thin $factory wrapper (if Codex selected)
.gitignore             + /factory/state/  + /factory/jobs/
```

Job worktrees stay next to the repo, as today: `<parent>/<repo>-factory/<job-id>/`.

### 4.3 The machine

| What | Windows | macOS | Linux |
|---|---|---|---|
| Engine (tool venv, managed by uv) | `%APPDATA%\uv\tools\agent-factory\` | `~/.local/share/uv/tools/agent-factory/` | same as macOS |
| `factory` command | `%USERPROFILE%\.local\bin\factory.exe` | `~/.local/bin/factory` | same |
| Machine dir (platformdirs user data) | `%LOCALAPPDATA%\agent-factory\` | `~/Library/Application Support/agent-factory/` | `~/.local/share/agent-factory/` |

The machine dir holds:
```
machine.db            registered repos, port leases, machine settings
service.lock          held by the running service
service.json          pid, create_time, port, token, version (user-only permissions)
runtime/run_cli-<sha8>.py
logs/service.log      rotated, 5 × 5 MB
```

`FACTORY_HOME` overrides the machine dir, which tests and the sandbox use.

`machine.db` (`migrations/machine/0001_init.sql`):
```sql
CREATE TABLE repos (
  id          TEXT PRIMARY KEY,            -- slug of the folder name + 6 hex of sha256(path), e.g. "srt-gateway-3f9a1c"
  path        TEXT NOT NULL UNIQUE,        -- absolute, normalised
  name        TEXT NOT NULL,               -- display name, defaults to the folder name
  added_at    TEXT NOT NULL,
  last_seen   TEXT
);
CREATE TABLE port_leases (
  base        INTEGER PRIMARY KEY,         -- first port of a 10-port block
  repo_id     TEXT NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
  job_id      TEXT NOT NULL,
  leased_at   TEXT NOT NULL,
  UNIQUE (repo_id, job_id)
);
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);   -- global concurrency, CLI paths
```

A repo is registered by `factory init`, and also automatically the first time any `factory`
command runs inside an initialised repo (e.g. after a fresh clone).

---

## 5. Configuration (v2)

### 5.1 Repo config: `factory/config.json`

```jsonc
{
  "version": 2,
  "engine": ">=1.0,<2",                     // PEP 440 specifier; checked by every command (§15.4)
  "defaultPipeline": "full",
  "pipelines": {
    "fast": [
      { "id": "build", "uses": "builder", "provider": "claude",
        "profiles": { "claude": { "model": "opus", "effort": "medium" },
                      "codex":  { "model": "gpt-6.1-sol", "effort": "medium" } } },
      { "id": "approve", "uses": "fast-approver", "provider": "codex",
        "profiles": { "claude": { "model": "opus", "effort": "medium" },
                      "codex":  { "model": "gpt-6.1-sol", "effort": "medium" } },
        "onChanges": "build" }
    ],
    "full": [
      { "id": "spec",  "uses": "spec-writer", "provider": "claude", "profiles": { … } },
      { "id": "build", "uses": "builder",     "provider": "claude", "profiles": { … } },
      { "id": "review", "onChanges": "build", "parallel": [
          { "id": "security", "uses": "reviewer-security", "provider": "claude", "profiles": { … } },
          { "id": "ux",       "uses": "reviewer-ux",       "provider": "claude", "profiles": { … } },
          { "id": "ui",       "uses": "reviewer-ui",       "provider": "claude", "profiles": { … } },
          { "id": "code",     "uses": "reviewer-code",     "provider": "codex",  "profiles": { … } }
      ] },
      { "id": "approve", "uses": "approver", "provider": "claude", "profiles": { … } }
    ]
  },
  "maxRounds": 3,
  "merge": "verify",                        // auto | verify
  "permissions": "auto",                    // auto | bypass
  "concurrency": { "perJob": 4 },
  "timeouts": { "executionMinutes": 60 },
  "sensitivePaths": [                       // §10.3
    "**/migrations/**", ".github/**", "**/*.lock", "package.json",
    "pyproject.toml", "**/auth/**", "Dockerfile", "**/*.tf"
  ]
}
```

Rules, enforced by Pydantic validators and checked before saving or creating a job:

- Pipeline names match `[a-z0-9][a-z0-9-]{0,31}`. `defaultPipeline` must exist.
- Step and execution `id`s are unique **within a pipeline** and match `[a-z0-9][a-z0-9-]{0,31}`.
  Step ids no longer double as stage names (§6.1), so nothing is reserved.
- `uses` matches the existing `NAME` pattern and must resolve to a role for the selected
  provider (§5.3).
- `provider` ∈ {claude, codex}. `profiles[provider]` must exist with an explicit `model` and
  `effort`. Profiles for the unselected provider are optional and preserved, so switching
  back in the dashboard restores them.
- **Roles may repeat** across executions, including inside one parallel group (e.g. the
  same reviewer through both providers), because report paths are keyed by execution id (§7.1).
- Each pipeline ends with exactly one `approval` step. ≤ 32 steps, ≤ 16 executions per group.
- `onChanges` must name an **earlier task** step. It is allowed on review and approval steps.
- `concurrency.perJob` is 1–16. The machine-wide limit lives in machine settings (§5.2).
- An optional per-execution `timeoutMinutes` overrides `timeouts.executionMinutes`.
- An optional per-execution `maxBudgetUsd` is passed to Claude's `--max-budget-usd` and
  ignored for Codex, with a validation warning.

### 5.2 Machine settings

Stored in `machine.db`, edited in the dashboard's machine Settings or with `factory config --machine`:
- `concurrency.global` (default 6, range 1–32): live attempts across all repos.
- `cli.claude`, `cli.codex`: absolute paths, detected by `factory doctor` (§12.1), so the
  service doesn't depend on the PATH of whatever started it.
- `ports.range`: default `5100-5999`.

### 5.3 Roles: built-ins, overrides, eject

Resolution for `uses: <name>` with provider `<p>`, first match wins:

1. `factory/roles/<name>.<p>.md` (repo, provider-specific)
2. `factory/roles/<name>.md` (repo, both providers)
3. built-in `<name>.<p>.md`
4. built-in `<name>.md`

Nothing found → a validation error listing the paths tried. There's still **no fallback to
the other provider's file**. A provider-specific file is only used for that provider.

- **Built-in roles** ship in the package: `spec-writer`, `builder`, `reviewer-security`,
  `reviewer-ux`, `reviewer-ui`, `reviewer-code`, `approver`, `fast-approver`. Engine upgrades
  improve them in every repo that hasn't overridden them.
- **`factory eject <name> [--provider p]`** copies the built-in role into `factory/roles/`
  so the repo can customise it. Ejected files record the built-in's version and sha256 in
  frontmatter (`ejectedFrom: builder@1.3.0#a1b2c3d4`). The dashboard and `factory status`
  say "ejected; built-in has changed since" when the built-in differs, and
  `factory eject <name> --diff` shows the change.
- **Custom roles** are new files in `factory/roles/` with the same frontmatter.
- Frontmatter: `type` (task | review | approval, required), `contract` (optional, `spec`),
  `description`. `output` is accepted for v1 files but ignored (§7.1).
- Built-in role text has a size budget: the builder ≤ 6 KB, others ≤ 4 KB. All built-ins are
  rewritten to start from the packet, without "re-read the whole job history", and to write the
  report sections in §7.2.
- Built-ins have one source in `src/agent_factory/roles/`. Provider variants exist only
  where wording must differ. `tools/sync_codex.py` is replaced by that convention.

### 5.4 Upgrading v1 config

`config.py` upgrades a v1 file on load, and `factory upgrade` writes it back (§12.2):

- `pipeline` → `pipelines.full`, and `defaultPipeline = "full"`. The bundled `fast` pipeline is added.
- Top-level `provider` → each execution's `provider`. `providers` → `profiles`.
- `start`, `headlessPermissions` → `permissions` (`bypassPermissions` → `bypass`). `start` is dropped.
- `engine` is added with the current major version.

---

## 6. Data model and state machines (per repo)

### 6.1 Concepts

- **Job**: one feature request run through one frozen pipeline.
- **Step**: a position in the pipeline. It has one execution or a parallel group.
- **Execution**: one (step, execution id, round) unit of work. A rewind creates new execution rows; old ones are kept as history.
- **Attempt**: one CLI process for an execution: fresh, resume, or contract-retry.

Stages shown on the board are derived from `jobs.current_step` + `jobs.state`. They are not
stored as free-form strings.

### 6.2 Schema (`migrations/repo/0001_init.sql`)

```sql
PRAGMA foreign_keys = ON;

CREATE TABLE jobs (
  id            TEXT PRIMARY KEY,              -- "007-dark-mode"
  seq           INTEGER NOT NULL UNIQUE,       -- 7
  title         TEXT NOT NULL,
  pipeline_name TEXT NOT NULL,
  frozen        TEXT NOT NULL,                 -- JSON: resolved pipeline, role texts + sources + sha256, limits, merge, permissions, sensitivePaths, engine version
  state         TEXT NOT NULL CHECK (state IN
                ('queued','running','paused','needs_human','approved','merged','cancelled')),
  pause_reason  TEXT,                          -- auth | permission | process | contract | blocked | changes | escalate | budget | busy_merge | timeout | ports
  note          TEXT,
  current_step  TEXT,                          -- step id
  round         INTEGER NOT NULL DEFAULT 1,
  returns_used  INTEGER NOT NULL DEFAULT 0,    -- automatic onChanges returns
  port_base     INTEGER,                       -- machine-wide lease (§9); NULL when released
  branch        TEXT NOT NULL,
  worktree      TEXT NOT NULL,
  base_head     TEXT,
  approved_head TEXT,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);

CREATE TABLE executions (
  id          INTEGER PRIMARY KEY,
  job_id      TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  step_id     TEXT NOT NULL,
  exec_id     TEXT NOT NULL,
  round       INTEGER NOT NULL,
  type        TEXT NOT NULL CHECK (type IN ('task','review','approval')),
  uses        TEXT NOT NULL,
  provider    TEXT NOT NULL,
  model       TEXT NOT NULL,
  effort      TEXT NOT NULL,
  state       TEXT NOT NULL CHECK (state IN
              ('pending','claimed','running','done','failed','stopped','superseded')),
  result      TEXT,                            -- READY | BLOCKED | PASS | CHANGES | APPROVE | ESCALATE
  head_before TEXT,                            -- branch head when dispatched
  head_after  TEXT,
  report_path TEXT,
  created_at  TEXT NOT NULL,
  UNIQUE (job_id, step_id, exec_id, round)
);

CREATE TABLE attempts (
  id              INTEGER PRIMARY KEY,
  execution_id    INTEGER NOT NULL REFERENCES executions(id) ON DELETE CASCADE,
  n               INTEGER NOT NULL,            -- 1, 2, … within the execution
  kind            TEXT NOT NULL CHECK (kind IN ('fresh','resume','contract_retry')),
  state           TEXT NOT NULL CHECK (state IN
                  ('launching','running','exited','valid','invalid','stopped','lost','failed')),
  owner_token     TEXT NOT NULL UNIQUE,        -- uuid4, passed to the wrapper argv
  session_id      TEXT,                        -- claude: preassigned; codex: from thread.started
  wrapper_pid     INTEGER,
  wrapper_ctime   REAL,                        -- psutil create_time
  exit_code       INTEGER,
  error_class     TEXT,                        -- auth | permission | rate_limit | budget | timeout | crash | contract
  error_detail    TEXT,
  dir             TEXT NOT NULL,               -- attempt artifact dir (relative to job folder)
  log_offset      INTEGER NOT NULL DEFAULT 0,
  last_activity   TEXT,                        -- one-line summary of the latest tool call (§13.4)
  last_activity_at TEXT,
  t_queued        TEXT, t_launch TEXT, t_first_event TEXT, t_exit TEXT, t_validated TEXT,
  packet_bytes    INTEGER,
  usage           TEXT,                        -- JSON as reported by the CLI, raw + normalised
  UNIQUE (execution_id, n)
);
CREATE UNIQUE INDEX one_live_attempt ON attempts(execution_id)
  WHERE state IN ('launching','running');      -- hard guarantee: never two live attempts

CREATE TABLE events (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,   -- SSE id (per repo)
  job_id  TEXT,
  ts      TEXT NOT NULL,
  type    TEXT NOT NULL,                       -- job.state, execution.state, attempt.state, usage, note, config, backlog
  payload TEXT NOT NULL                        -- JSON
);
CREATE INDEX events_job ON events(job_id, id);

CREATE TABLE start_queue (                     -- dashboard Start / factory start, consumed by the engine
  id          INTEGER PRIMARY KEY,
  title       TEXT NOT NULL,
  details     TEXT,
  pipeline    TEXT,
  backlog_key TEXT UNIQUE,                     -- sha256 of the exact backlog item; prevents double start
  created_at  TEXT NOT NULL,
  job_id      TEXT REFERENCES jobs(id)
);
```

Pragmas on every connection: `journal_mode=WAL`, `synchronous=NORMAL`, `foreign_keys=ON`,
`busy_timeout=5000`. All writes go through `db.tx()`, which uses `BEGIN IMMEDIATE` and
holds the transaction for milliseconds, **never across a process launch, a git command, or
any `await`**. The engine uses one writer thread per open DB (repo DBs and `machine.db`).
API reads use separate read connections.

Events are pruned to the last 30 days, or 100k rows per repo, at service start.

### 6.3 State machines

```
Job:        queued → running ⇄ paused
                     running → needs_human → (rework → running) | (approve → merged) | cancelled
                     running → approved → merged            (merge: auto, or human approve)
                     approved → needs_human                 (busy checkout / merge failed / sensitive paths)
Execution:  pending → claimed → running → done(result) | failed | stopped
            any non-terminal → superseded                  (rewind or rework)
Attempt:    launching → running → exited → valid | invalid
            launching|running → stopped | lost | failed
```

`paused` covers conditions a human must fix outside the factory (auth, permissions, a
missing CLI, budget, ports). **Resume** retries from the same execution. `needs_human` is a
pipeline decision (BLOCKED, CHANGES without budget, ESCALATE, verify-merge, sensitive
paths). It continues with approve or rework.

### 6.4 Claim → launch → record protocol (the duplicate-launch guarantee)

The scheduler loop runs on any wake-up: a new job, an execution finishing, a config change,
or a 2 s timer. It considers all registered repos, round-robin, so one busy repo can't starve the others.

1. **Claim (tx in the repo DB):** select `pending` executions of `running` jobs in pipeline
   order, while `live_per_job < perJob` and the machine-wide `live_global < global` (tracked in
   memory and recomputed from all repo DBs at start). For each, set the execution to `claimed`
   and insert an attempt `launching` with a new `owner_token`. For Claude, also generate
   `session_id = uuid4()` now. The `one_live_attempt` index makes a concurrent duplicate claim fail.
2. **Prepare (no tx):** create the attempt dir, then write `prompt.md`, `system.md`,
   `command.json`, and `packet.json` (§8).
3. **Spawn (no tx):** start the wrapper with the token (§3.2).
4. **Record (tx):** `UPDATE attempts SET state='running', wrapper_pid=?, wrapper_ctime=?, t_launch=? WHERE id=? AND owner_token=? AND state='launching'`. Mark the execution `running`.

**Recovery at service start.** Re-adopting happens for every registered repo before the
scheduler runs, for every attempt in `launching` or `running`:

| Found | Action |
|---|---|
| `proc.json` exists, its process is alive, create_time and token match | Re-adopt: set `running` and continue tailing from `log_offset`. |
| `exit.json` exists | Finalise as a normal exit (§6.5). |
| `proc.json` exists, process dead, no `exit.json` | `lost`. |
| No `proc.json`, and a process with the token in its cmdline exists (psutil scan) | Wait up to 10 s for `proc.json`, then re-adopt. |
| No `proc.json`, no such process, `t_launch` unset | The spawn never happened. Delete the attempt row and set the execution back to `pending`. This is the only case where an attempt is relaunched automatically, and it is safe because no process ever existed. |

A `lost` attempt is **never** relaunched automatically. The job goes to `paused`
(`pause_reason=process`). When the user presses Resume:
- with a known `session_id`, start a `resume` attempt using that exact ID;
- otherwise start a `fresh` attempt. Its packet says the worktree may contain partial
  uncommitted work from an interrupted attempt and must be inspected first.

Heartbeats are not used for liveness. Liveness is always `pid` + `create_time` + token.

### 6.5 Exit classification

When `exit.json` appears (or the process is gone), the adapter reads the tail of `events.jsonl`:

| Condition | Attempt | Next |
|---|---|---|
| exit 0, report exists, contract valid | `valid` | Execution `done(result)`; the pipeline advances when the step is complete (§10). |
| exit 0, report missing or invalid, attempt kind ≠ `contract_retry` | `invalid` | One `contract_retry` attempt with a **fresh session**. The packet includes the validation error. |
| exit 0, invalid again | `invalid` | Job → `needs_human`, `pause_reason=contract`. |
| Auth error detected (adapter rules) | `failed`, `auth` | Job → `paused`. |
| Permission denial that stopped the work | `failed`, `permission` | Job → `paused`. The dashboard shows the denied tool and command. |
| Budget exceeded (`--max-budget-usd`) | `failed`, `budget` | Job → `paused`. |
| Rate limit / overloaded after the CLI's own retries | `failed`, `rate_limit` | Job → `paused`. Resume retries. |
| Timeout (engine-enforced, §9) | `stopped`, `timeout` | Job → `paused`. |
| Any other non-zero exit | `failed`, `crash` | Job → `paused`, `pause_reason=process`. |

Nothing switches pipelines, providers, or models automatically.

---

## 7. Reports

### 7.1 Location (in the project repo, gitignored)

```
factory/jobs/<job-id>/
  request.md                       verbatim request + details
  rework-<k>.md                    human rework notes
  executions/<step-id>.<exec-id>/r<round>/a<n>/
      system.md  prompt.md  packet.json  command.json
      proc.json  exit.json  events.jsonl
      report.md                    ← the agent writes here (absolute path given in the prompt)
```

Paths are keyed by execution id, so the same role can run twice in one group without
collisions. The dashboard shows a human-friendly label (`label` or the role name).

### 7.2 Contract and sections

First line (unchanged): task `STATUS: READY|BLOCKED` (spec contract: `# ` heading), review
`VERDICT: PASS|CHANGES`, approval `APPROVE|CHANGES|ESCALATE`. Approval `CHANGES` is new and
only valid when the step has `onChanges`.

The built-in roles must also write these Markdown sections. The parser in `reports.py`
extracts them for handoffs. Custom roles may omit them, and only the first line is validated.

| Role type | Required sections |
|---|---|
| spec | `## Acceptance criteria` (numbered), `## Pages to check` |
| builder | `## Changes` (path list), `## Decisions`, `## Acceptance` (criterion → evidence), `## Checks run` (``command`` → result) |
| review | `## Findings` — each `- [blocker|major|minor] path:line — text`; `## Checks run` |
| approval | `## Reasons`, `## Follow-ups` |

Validation also checks that:
- the report's mtime is after `t_launch` (freshness);
- reviews and approvals left the branch head unchanged (`head_after == head_before`) and the worktree clean;
- a task with READY left the worktree clean;
- the `builder` role added at least one commit since `base_head` (ported rule).

---

## 8. Prompts and the execution packet

### 8.1 Layout (stable first, for prompt caching)

Claude:
```
claude -p --output-format stream-json --verbose
       --session-id <uuid> --model <model> --effort <effort>
       --permission-mode auto|bypassPermissions
       --append-system-prompt-file <attempt>/system.md
       --add-dir <repo>/factory/jobs/<job-id>
       [--max-budget-usd N]
       < prompt.md            (stdin)
cwd = worktree
```
Resume: `claude -p --resume <session_id> --output-format stream-json --verbose …` with a short "continue" prompt.

Codex:
```
codex exec --json -m <model> -c model_reasoning_effort="<effort>"
           -C <worktree> --add-dir <repo>/factory/jobs/<job-id>
           --approve-for-me | --dangerously-bypass-approvals-and-sandbox
           -                 (prompt on stdin)
```
Resume: `codex exec resume --json <session_id> -` with the same flags.

The executable comes from machine settings `cli.claude` / `cli.codex` (§5.2), falling back to PATH.

- `system.md` = the factory's fixed preamble (contracts, report path rules, port rules, "never push",
  "never touch the main checkout") + the resolved role instructions. It is byte-identical
  across jobs for the same role and engine version, so providers can cache it. For Codex,
  which has no system-prompt flag in `exec`, `system.md` is prepended to the prompt. Phase 0
  checks whether `-c developer_instructions=…` is supported and better.
- **Project instructions are not copied in.** Both CLIs load `CLAUDE.md`/`AGENTS.md` from
  the worktree themselves. The packet only refers to them.
- `prompt.md` = the packet rendered as Markdown, in a fixed section order.

### 8.2 Packet contents (built without any model call)

1. Job: id, title, pipeline, step, execution id, round, attempt kind.
2. Paths: worktree, branch, job folder, **absolute report path**, ports (tasks and UX/UI reviewers).
3. Request: `request.md` verbatim, plus the latest `rework-<k>.md` if present.
4. Acceptance criteria: from the spec report (Full) or the builder's `## Acceptance` (Fast, for the approver).
5. Branch state: `base_head..HEAD` log (oneline, ≤ 50), `git diff --stat`, and the full diff
   when ≤ 24 KiB, otherwise per-file hunks up to the budget, with the rest listed as omitted.
6. Upstream results (current round): for each completed execution, its result, `## Decisions`,
   `## Checks run`, and the report path.
7. Open findings: for the builder on round > 1, every `CHANGES` finding from the previous round,
   grouped by reviewer, with report paths.
8. Retry context: for a `contract_retry`, the validation error. For a fresh attempt after
   `lost`, a warning that the worktree may hold partial work.
9. Omitted material: a list of paths the agent can read if it needs more.

The packet is capped at **64 KiB**. Sections 1–3, 7 and 8 are never truncated. When the
cap is hit, sections 5, then 6, are trimmed with explicit `[omitted: … see <path>]`
markers. `packet_bytes` is recorded per attempt.

Role instructions tell agents: "Start from this packet. Read further files only when you
need them. Do not re-read earlier rounds' reports unless the packet points you to them."

---

## 9. Concurrency, ports, timeouts, stop

- **Limits:** `perJob` (repo config) and `global` (machine settings) are enforced by the
  scheduler (§6.4). Within a job only one step is active at a time. Parallelism comes from
  parallel groups, from multiple jobs, and from multiple repos.
- **Ports: machine-wide leases.** When a job starts, the engine leases a free 10-port block
  from `ports.range` in `machine.db` (`port_leases`). It's the lowest free `base` whose ports
  also have no current listener (psutil check). `jobs.port_base` records it. Port-holding
  executions get three ports each, as today (builder, UX, UI). The lease is released when the
  job reaches `merged`/`cancelled` or is deleted, and kept while it waits in `needs_human` so a
  human can still run the worktree. With no free block, the job is `paused(ports)` until one frees up.
  This replaces `5100 + 10n`, which collided across repos.
- **Freeing ports:** before a step starts and after it ends, the engine frees ports held by
  processes in that attempt's process tree. It never kills processes it doesn't own. A port
  held by a foreign process makes the job `paused(ports)` with the owner's pid and name.
- **Timeouts:** the engine starts a timer per attempt. When it expires, it stops the attempt.
- **Stop (attempt or job):**
  1. Mark the attempt `stopping` in memory and the job `paused` (tx).
  2. Windows: terminate the wrapper's Job Object. The wrapper watches for a `stop` flag file
     in its attempt dir and calls `TerminateJobObject`; failing that, `psutil` terminates
     the wrapper's tree. POSIX: `killpg(SIGTERM)`, wait 10 s, then `SIGKILL`.
  3. The attempt becomes `stopped`. Completed executions in the same step are kept. The worktree is left as is.
- **Ownership:** the engine only signals processes whose wrapper token matches a row it owns.

---

## 10. Pipeline engine

### 10.1 Advancing (`engine/pipeline.py`, pure functions + one tx)

When every execution of the current step is `done`:

1. Aggregate results. Any `BLOCKED` → `needs_human(blocked)`.
2. Any `CHANGES` (review or approval):
   - with `onChanges` and `returns_used < maxRounds - 1`: `returns_used += 1`, `round += 1`.
     Supersede all non-terminal executions from the target step onward, then create
     `pending` executions for the target step;
   - otherwise `needs_human(changes)`.
3. Approval `ESCALATE` → `needs_human(escalate)`.
4. Approval `APPROVE`: check that every review step's `head_after` equals the current head,
   then check sensitive paths (§10.3), then set `approved_head`. With `merge=auto`, merge; else `needs_human(verify)`.
5. Otherwise, create `pending` executions for the next step (`head_before` = current head).

These rules are ported from `job.advance` and its tests. The tests are kept and adapted,
not rewritten from scratch.

### 10.2 Fast and Full

- **Full**: spec → build → parallel reviews → approve. This is today's default, unchanged in behaviour.
- **Fast**: build → `fast-approver`.
  - Without a spec, the builder writes `## Acceptance` itself (criteria derived from the request, then evidence).
  - `fast-approver` reviews the diff against the request and criteria, runs the project's
    validation commands, and covers security and code quality in one pass. It returns `APPROVE`,
    `CHANGES` (→ build), or `ESCALATE`. It keeps the approver's escalation list.
  - Two agent calls when no rework is needed.
- Pipeline choice is per job (`factory start --pipeline fast`, or the selector next to Start),
  with `defaultPipeline` as the default. Jobs never switch pipelines.

### 10.3 Sensitive paths (an engine guard, not a model judgement)

Before any merge, `auto` or human, the engine computes `git diff --name-only base_head..approved_head`.
If any path matches `sensitivePaths`:
- `merge=auto` → `needs_human(sensitive)`, listing the matching paths;
- a human approve shows the list and needs a confirmation flag (`--confirm-sensitive` / a checkbox).

This makes Fast + auto-merge safe by construction for the changes that matter most.

### 10.4 Rework and human approval

- `rework <id> "<note>"` writes `rework-<k>.md`, supersedes pending/approval executions,
  sets `returns_used = 0`, and creates pending executions for the first `builder` step, or
  the first task step if there is none. Same semantics as today.
- `approve <id>` → merge (§11), allowed from `needs_human(verify|escalate|sensitive|busy_merge)`
  only when every step finished and the head equals `approved_head`. For an escalate, the head equals the head the decision saw.

### 10.5 Freezing

At job creation, `frozen` stores the resolved pipeline, the exact role texts with their
source (repo override or `built-in@<version>`) and sha256, the profiles, limits, `merge`,
`permissions`, `sensitivePaths`, the preamble version, and the engine version. Later edits
to config or roles, **and engine upgrades**, affect only new jobs: a running job keeps its
frozen roles and preamble. The dashboard shows when a job's frozen role differs from what a
new job would get.

---

## 11. Git operations (ported, unchanged rules)

- Worktree `<parent>/<repo>-factory/<job-id>` on branch `factory/<job-id>` from `main`. The
  project's fresh-checkout setup from `AGENTS.md` runs **as the first task execution's
  responsibility** (as today), not in the engine, because the engine can't interpret free-form setup text.
- Merge: unchanged checks (on main, nothing staged, no uncommitted edits in files the branch
  changes → `busy_merge`), `git merge --no-ff`, abort on conflict → `needs_human`. Never push.
- Delete job: stop live attempts, remove the worktree (retry up to 5 × 1 s on Windows file locks),
  delete the branch, delete the folder, release the port lease, and cascade-delete the DB rows.

---

## 12. Entry points

### 12.1 The `factory` command

Run inside a project repo, commands act on that repo (found by walking up to the folder
holding `factory/config.json`). `--repo <path|id>` overrides this.

```
Setup
  factory init [--provider claude|codex|both] [--dry-run]   set up this repo (§12.2)
  factory upgrade [--dry-run]                               update this repo's files to the engine's format (§15.4)
  factory eject <role> [--provider p] [--diff]              copy a built-in role into factory/roles/
  factory doctor                                            check git, claude/codex (+ login), agent-browser, ports, PATH
  factory repos [list | remove <id> | rename <id> <name>]

Jobs
  factory start "<title>" [--details-file F] [--pipeline fast|full|<name>] [--wait]
  factory next [--pipeline …] [--wait]          first unchecked backlog item
  factory status [<job-id>] [--json]
  factory stop <job-id> | resume <job-id>
  factory approve <job-id> [--confirm-sensitive]
  factory rework <job-id> "<note>"
  factory delete <job-id> [--force]

Service and dashboard
  factory dashboard                             start the service if needed, open the browser on this repo
  factory serve [--port N]                      run the service in the foreground
  factory service status | stop [--stop-running] | restart
  factory config --machine [key=value …]        machine settings (§5.2)
  factory version
```

- Every job and dashboard command starts the service detached when it isn't running, and
  waits until `/api/health` answers (max 10 s).
- Commands talk to the service over HTTP with the token from `service.json`.
- Without `--wait`, `start`/`next` return immediately with the job id and the dashboard URL.
  With `--wait`, they stream one line per state change until a terminal or paused state.
- Before acting, every command checks the repo config's `engine` specifier against the
  installed version, and fails with the exact fix (§15.4).
- Exit codes: 0 ok, 1 error, 2 usage, 3 merge busy (as today), 4 version mismatch, 5 repo not initialised.

### 12.2 `factory init`, `upgrade`, `eject`

`factory init`, in a project folder:
1. Checks that it's a Git repo with a `main` branch and at least one commit, and that
   `AGENTS.md` or `CLAUDE.md` exists. If not, it explains what's missing (no files written).
2. Asks for the client (claude / codex / both), unless `--provider` is given.
3. Writes `factory/config.json` (from the template, with `"engine": ">=<major>.0,<<major+1>"`),
   `factory/backlog.md`, the selected skill wrappers, and the `.gitignore` entries.
   Existing files are never overwritten; it reports them and skips.
4. Registers the repo in `machine.db`.
5. Runs a short `factory doctor` and prints the next steps: commit the listed files, check
   that `AGENTS.md` says how to start the dev server on a given port and how to run checks,
   then `factory start "…"`.

It's idempotent: running it again in an initialised repo changes nothing and says so. On a
v1 install it stops and points to `factory upgrade` (§15.6).

`factory upgrade` rewrites repo files that the engine owns (config format, skill wrappers)
to the installed engine's format. It never touches `backlog.md` or `factory/roles/`. It
prints a summary and leaves the diff for the user to review and commit.
`--dry-run` prints the diff only.

`factory eject` is described in §5.3.

### 12.3 Skills (committed in the repo, written by `init`)

`/factory <feature>` (Claude) and `$factory <feature>` (Codex) are ~30-line wrappers:

- They map the user's words to commands: `<feature>` → `factory start`, plus `next`,
  `status`, `approve`, `rework`, `stop`, `resume`, `dashboard`, and an optional
  `--pipeline fast|full`.
- They run the command and relay its output: job id, dashboard link, errors.
- They say the job keeps running if the session is closed.
- If the `factory` command isn't found, they reply with the install command
  (`uv tool install agent-factory`) and stop.
- `watch` is removed, because dashboard Start runs jobs directly.

### 12.4 Backlog

`backlog.md` stays the source of truth for queued items, with the same format and the same
dashboard editing. Starting an item inserts a `start_queue` row keyed by the item's hash,
then rewrites the line to `- [x] <title> (<job-id>)` under a file lock. Starting the same
item twice is rejected by the unique `backlog_key`.

---

## 13. API, SSE and dashboard

### 13.1 Security (all routes)

- Bind 127.0.0.1. Reject any `Host` header other than `127.0.0.1:<port>` or `localhost:<port>` (DNS rebinding).
- Mutating routes require `Origin` to be absent or same-origin, **and** the header
  `X-Factory-Token: <token>`. The dashboard gets the token embedded in `index.html`, served
  only to an allowed Host. Requests from other sites can't read it, and the custom header
  forces a CORS preflight, which is never approved. The `factory` command reads the token from
  `service.json`.
- Body limit 100 KB (as today). No CORS headers at all.
- Repo ids in URLs must match a registered repo. File routes are confined to that repo's
  `factory/jobs/` folder.

### 13.2 Routes

```
GET  /api/health                                  {version, pid}; no token needed
GET  /api/overview                                all repos: name, path, status, counts (running, needs-you, queued)
GET  /api/overview/stream                         SSE: in-memory overview updates (no replay; on reconnect refetch /api/overview)
GET  /api/machine/settings   PUT …                concurrency.global, cli paths, ports.range
GET  /api/repos/{repo}/state                      snapshot: jobs, current executions, backlog, config summary, last event id
GET  /api/repos/{repo}/jobs/{id}                  job + executions + attempts (+ usage/timings)
GET  /api/repos/{repo}/jobs/{id}/files/{path}     report/request text
GET  /api/repos/{repo}/attempts/{id}/log?offset=  raw events.jsonl slice (≤ 256 KB per call)
GET  /api/repos/{repo}/events                     SSE: id = events.id; honours Last-Event-ID; keepalive every 15 s
GET  /api/repos/{repo}/attempts/{id}/stream       SSE: batched log lines (every 500 ms or 64 KB), from the file tail
POST /api/repos/{repo}/jobs                       {title, details?, pipeline?}
POST /api/repos/{repo}/jobs/{id}/stop | resume | approve | rework | delete
GET  /api/repos/{repo}/config   PUT …             validated; returns field errors
GET  /api/repos/{repo}/roles                      resolved roles with source (override / built-in / ejected-outdated)
POST /api/repos/{repo}/backlog                    add / reorder / remove / start (as today)
```

SSE recovery: on reconnect the browser sends `Last-Event-ID`, and the server replays
`events.id > last`. If the gap exceeds 5000 events or the id was pruned, it sends `reset`
and the client refetches state. Log streaming never writes to the DB. `activity` events
(§13.4) are in-memory only.

### 13.3 Dashboard: React, same stack as srt-gateway

**Stack:** React 19, Vite, TypeScript, Tailwind v4, shadcn/ui (new-york) on Radix,
lucide-react, sonner, TanStack Query + Router, react-markdown + remark-gfm, IBM Plex via
`@fontsource`. This matches `srt-gateway/web`, so its primitives, theme, and patterns are
reused directly (§13.7).

**Source and build:**
- Source in `web/` in this repo. `npm run build` outputs to `src/agent_factory/dashboard_dist/`,
  which is gitignored and included in the wheel (§15.2).
- The service serves it as static files, with `index.html` templated to embed the token. The
  router uses hash URLs, so links like `#/repo/srt-gateway-3f9a1c/job/007-dark-mode?file=…`
  are shareable and survive reloads.
- Development: `npm run dev` runs Vite with a proxy for `/api` to the service on 5099, so the
  UI hot-reloads against real jobs. Users never run Node.

```
web/src/
  main.tsx                 router, QueryClient, Toaster
  shell.tsx                sidebar: repo switcher (with needs-you counts), Board / Backlog / Settings; header with connection state
  lib/
    api.ts                 fetch wrapper: token header, JSON, error mapping (zod-validated responses)
    sse.ts                 EventSource per repo + overview, Last-Event-ID, reset → refetch
    reducer.ts             pure: reduce(state, event) → state; applied to the Query cache
    format.ts              durations, tokens, relative times
  features/
    overview/              all repos at a glance (default page when > 1 repo)
    board/                 Board, JobCard, ActivityLine (§13.4), compact pipeline strip
    job/                   JobDetail: tabs Pipeline / Reports / Timeline / Log / Usage; actions
      pipeline/            PipelineView, layout.ts, PipelineNode, PipelineEdge, GroupFrame (§13.6)
      ReportView.tsx       react-markdown (§13.5)
      LogView.tsx          live log (virtualised, last 2000 lines), raw toggle
      Timeline.tsx, UsagePanel.tsx, PauseBanner.tsx
    backlog/               add / drag reorder / remove / Start with pipeline selector
    settings/              repo: pipelines editor (Step / Execution / Provider / Model / Effort table + JSON), roles list with eject status
                           machine: global concurrency, CLI paths, ports
  ui/                      copied from srt-gateway/web/src/ui (§13.7)
    primitives/            button, badge, card, dialog, sheet, tabs, tooltip, dropdown-menu, context-menu, input, select, skeleton
    status.tsx, status-text.ts, page-header.tsx, page-shell.tsx, confirm-dialog.tsx, settings-section.tsx, utils.ts
  styles.css               srt-gateway theme + factory additions
```

**State and data flow:**
- TanStack Query holds server state (`/overview`, `/repos/{repo}/state`, job details).
- SSE events go through the **pure reducer** and are written into the Query cache with
  `setQueryData`. There's no polling, except refetching after a `reset`.
- Mutations call `api.ts` and then wait for the SSE event. There are no optimistic updates,
  except a loading state on the pressed button (srt-gateway's `Button loading`), so the UI
  never shows a state the engine didn't reach.
- **Unsaved settings edits** live in local form state. Incoming config events don't
  overwrite a dirty form. Instead a banner reads "Settings changed elsewhere — reload / keep
  editing". Switching an execution's provider swaps which profile is edited and keeps the other.

**Features.** It keeps today's board: columns per step, review chips, time in stage, the
needs-you count in the tab title (summed across repos), the shareable file URL,
Merge/Delete, and backlog drag-and-drop. New:

- A **repo switcher** and an **overview** of all repos.
- A pipeline selector on Start, and the per-pipeline settings editor.
- Stop / Resume per job, and a pause reason banner (auth: "run `claude login`", ports: "port 5131 is held by node.exe (pid 4242)").
- The **live pipeline view** (§13.6) and a **current activity line** on every running card (§13.4).
- An execution timeline: queued → launch → first event → exit → validated, per attempt.
- A usage panel: tokens (input/cached/output), cost where reported, packet size, call count.
- A live log for running attempts (rendered tool calls and messages, raw view toggle).
- A "quiet" warning when a running attempt has had no activity for 10 min, replacing the 3-hour stage warning.
- Legacy jobs (§15.6) are shown read-only in a collapsed section.

### 13.4 Current activity

Each running execution shows one line on its card, e.g.:

```
build · builder (claude)        ⟳ running `npm test`                      12s ago
review · ux (claude)            ⟳ opening http://127.0.0.1:5172/settings  3s ago
review · code (codex)           ⟳ reading src/settings/theme.ts           1s ago
review · security               ✓ PASS
```

A parallel step shows one line per execution, so the card shows which reviewers are done
and what the others are doing.

**Source.** The adapters already parse every event while tailing `events.jsonl`. Each
adapter maps tool-call events to a short summary through `summarize(event) -> str | None`:

| Event | Summary |
|---|---|
| Shell command (Claude `Bash`/`PowerShell` tool_use; Codex `command_execution` item) | ``running `<command>` `` (first line, ≤ 80 chars) |
| File read (`Read`, `Grep`, `Glob`; Codex read items) | `reading <path>` / `searching "<pattern>"` |
| File edit (`Edit`, `Write`; Codex `file_change`) | `editing <path>` (several files: `editing 3 files`) |
| Browser (agent-browser commands) | `opening <url>` / `checking <page>` |
| Git commit | ``committing "<subject>"`` |
| Report written to the attempt's report path | `writing report` |
| Assistant text with no tool | `thinking` (only when nothing else happened for 20 s) |

Paths are shown relative to the worktree. Commands are trimmed to the first line, and
anything that looks like a secret (`--token=…`, `KEY=…`, long base64) is masked with `…`.

**Delivery.**
- Activity is **ephemeral**. It is sent as SSE event type `activity`
  (`{attempt_id, text, at}`) from an in-memory map, throttled to at most one event per
  attempt per second. It is not stored in the `events` table, so it doesn't bloat replay.
- The repo state snapshot includes the latest activity per running attempt, so a
  reconnecting or reloading browser shows it immediately.
- `attempts.last_activity` / `last_activity_at` are written at most every 10 s, so they
  survive a service restart. They also drive the "quiet" warning (no activity for 10 min).
- When an execution finishes, the line is replaced by its result chip (PASS, CHANGES,
  READY, …) and its duration.

**Optional step progress.** Built-in roles may print a marker line in their normal
output, e.g. `FACTORY-PROGRESS: 3/5 acceptance criteria verified`. When an adapter sees
one, the card shows it next to the activity. It is informational only and never used
for pipeline decisions. Custom roles that don't print markers just show activity.

### 13.5 Report rendering

Reports are agent-written Markdown and must be treated as untrusted. They are rendered with
`react-markdown` + `remark-gfm`, **without** `rehype-raw`, so raw HTML in a report is shown
as text and never executed. Links get `rel="noopener noreferrer"` and `target="_blank"`;
`javascript:` and `data:` URLs are dropped by a `urlTransform`. Images are not rendered.
Paths like `src/a.ts:12` become copyable chips. Styling uses srt-gateway's `.asset-markdown`.

### 13.6 Live pipeline view

A diagram of the job's frozen pipeline that updates live, in the style of super-board's
skill map but specialised to our fixed shape: React components, HTML node cards, and one SVG
layer for edges. No graph library.

```
┌ TASK ─────────┐    ┌ TASK ──────────────────┐    ┌ review · 2/4 done ─────────┐    ┌ APPROVAL ─────┐
│ spec          │    │ build                  │    │ ┌ REVIEW security ✓ PASS ┐ │    │ approve       │
│ claude · opus │───►│ claude · opus          │───►│ ├ REVIEW ux  ⟳ opening … ┤ │───►│ claude · opus │
│ ✓ READY  2m   │    │ ✓ READY  14m · r2      │    │ ├ REVIEW ui  · pending   ┤ │    │ · waiting     │
└───────────────┘    └────────────────────────┘    │ └ REVIEW code ✗ CHANGES  ┘ │    └───────────────┘
                              ▲                    └────────────────────────────┘
                              └ ─ ─ ─ ─ CHANGES · round 2 ─ ─ ─ ─ ─ ─ ─ ┘
```

**Where it appears.**
1. **Job detail → Pipeline tab** (the default tab while a job runs). Clicking a node opens
   that execution's report, log, or timeline in a side `Sheet`. The URL records the
   selection (`…/job/007-dark-mode?node=build`).
2. **Settings → pipeline preview.** The same component, read-only and without live state,
   re-rendered as the pipeline JSON or table is edited. Validation errors highlight
   the offending node.
3. **Board card (compact mode).** A one-row strip of small step dots (done / running /
   pending / changes) under the title. It uses the same `layout.ts`, with no edges.

**`layout.ts` (pure, unit-tested).**
Input: the frozen pipeline + the latest execution per (step, exec id) in the current round.
Output: `{nodes: [{id, step, x, y, w, h, kind}], groups: [{id, x, y, w, h, label}], edges: [{from, to, kind: 'flow'|'rework', label}], width, height}`.
- Column `i` = step index. `x = i * (NODE_W + GAP_X)`.
- A parallel group stacks its executions vertically inside a `GroupFrame`. The column
  height is the tallest group. Single steps are vertically centred.
- Flow edges connect the right port of step `i` to the left port of step `i+1`. For a
  group, they connect the frame, not each member, which keeps the diagram calm.
- A rework edge exists for every step with `onChanges`. It is routed orthogonally
  under all columns (`y = height + LOOP_GAP`), from the bottom of the source to the bottom
  of the target, as a dashed line. When a rewind has happened, it is labelled
  `CHANGES · round N`. Multiple loops get stacked lanes, one per loop, ordered by length.
- Constants are in one place: `NODE_W=220`, `NODE_H=96`, `GAP_X=56`, `GAP_Y=12`.

**`PipelineNode`.** A `Card` (srt-gateway primitive) with:
- a kind `Badge` (TASK / REVIEW / APPROVAL), the role name and its `label`;
- provider · model · effort in mono;
- a status line with `StatusDot`;
- the activity line (§13.4) while running, or the result + duration when done;
- the attempt count when > 1 (`a2 · contract retry`) and the round when > 1.

States map to theme tones (§13.7): pending = muted, running = signal with `status-pulse`,
done-PASS/READY/APPROVE = signal, CHANGES = warn, BLOCKED/ESCALATE/failed = danger,
paused = warn with the pause reason, superseded = dimmed, struck through, hidden behind
a "show history" toggle.

**`PipelineEdge`.** SVG `path` with `marker-end` arrows. Flow edges into the running step
get a moving-dash animation (`stroke-dasharray` + `stroke-dashoffset` keyframes). Rework
edges are dashed. Colours come from the theme's CSS variables.

**Behaviour.**
- The canvas scrolls horizontally on overflow. There's no zoom/pan: ≤ 32 steps fit with
  scrolling, and the compact board strip covers the overview.
- Nodes are memoised (`React.memo` on execution `id` + `state` + activity), so only changed nodes re-render.
- Keyboard: the canvas is a `role="list"` of nodes; arrow keys move focus and Enter opens the node.
  Every status is text + colour, never colour alone.
- `prefers-reduced-motion` disables pulses and moving dashes.

**When to add a library** (not planned): drag-and-drop pipeline *editing* → evaluate React
Flow for Settings only. Arbitrary graphs (branches/joins) → add dagre for layout only and
keep our node rendering.

### 13.7 Visual design: srt-gateway's theme and components

The dashboard uses the same design as `srt-gateway/web`: dark, quiet, IBM Plex Sans/Mono, a
muted teal "signal" accent, 1-px ring shadows instead of borders, rounded cards, and
sonner toasts styled as `.pylon-toast`.

- **Copied, not reimplemented.** These are copied from `srt-gateway/web/src` into `web/src`:
  - `styles.css` (the `@theme` tokens, base layer, toast styles, `.status-pulse`, `.asset-markdown`);
  - `ui/primitives/*`;
  - `ui/status.tsx`, `ui/status-text.ts`, `ui/page-header.tsx`, `ui/page-shell.tsx`,
    `ui/confirm-dialog.tsx`, `ui/settings-section.tsx`, `ui/utils.ts`;
  - `components.json` (shadcn new-york, neutral, CSS variables).
- **Provenance.** `web/src/ui/SOURCE.md` records the srt-gateway commit they came from.
  Factory-specific styles go in a separate `factory.css` layer, so re-copying the shared files
  later is a clean overwrite.
- **Status vocabulary.** `status-text.ts` is extended with the factory's states (queued,
  running, paused, needs-you, approved, merged, PASS, CHANGES, BLOCKED, ESCALATE). Status is
  always text + colour, as in srt-gateway.
- **Colour rules.** Board columns, nodes, and chips use only the semantic tokens (signal /
  warn / danger / muted / term-*). No ad-hoc colours.
- **Dark only**, like srt-gateway (`color-scheme: dark`).
- **Accessibility:** srt-gateway's focus-visible ring, 4.5:1 contrast for muted text on
  cards (checked in tests), Radix keyboard handling, and reduced motion respected.
- **Later, optional:** if more projects share this design, extract the shared `ui/` and
  theme into a small private package. Not part of this plan.

Third-party notices (IBM Plex: SIL OFL 1.1; Lucide: ISC; Radix, shadcn, React, and others:
MIT) are generated at build time into `dashboard_dist/THIRD_PARTY_NOTICES.txt` and shipped in the wheel.

---

## 14. Observability

- Timings per attempt are in the `attempts` columns. Derived: `startup = t_first_event − t_launch`,
  `process = t_exit − t_launch`, `validation = t_validated − t_exit`, and per job
  `engine_overhead = total − Σ process`.
- Usage is parsed by the adapters and stored raw + normalised as `{input, cached_input, output, cost_usd|null}`.
  - Claude: the final `result` event (`usage`, `total_cost_usd`, `duration_ms`, `num_turns`, `permission_denials`).
  - Codex: `turn.completed` events (`usage.input_tokens`, `cached_input_tokens`, `output_tokens`); cost is null.
  - Missing values stay `null` and show as "unknown". Never estimate.
- Logs: `events.jsonl` per attempt, in the repo's gitignored `factory/jobs/`. Folders older
  than 30 days for merged or deleted jobs are compressed (gzip) at service start. The
  service's own log is `<machine-dir>/logs/service.log`. Logs are never uploaded anywhere.
- Secrets: the engine never passes credentials in argv or `command.json`. The CLIs use
  their own saved logins. Environment additions are limited to `PYTHONUTF8`, `FACTORY_*`
  values, and port variables.

---

## 15. Distribution, installation and upgrades

### 15.1 Model

| Where | What | Who changes it |
|---|---|---|
| Machine (global) | engine, dashboard, built-in roles, Python deps (`uv tool install`) | `uv tool upgrade` |
| Machine dir | repo registry, port leases, machine settings, service files | the engine |
| Project repo (committed) | `factory/config.json`, `factory/backlog.md`, `factory/roles/` (overrides), skill wrappers | you; `factory init` / `upgrade` / `eject` |
| Project repo (gitignored) | `factory/state/`, `factory/jobs/` | the engine |

Why global rather than per repo: one upgrade serves every repo, repos don't drift between
engine versions, ports and concurrency are coordinated across repos, one dashboard shows
everything, and the repo diff stays small and reviewable. The venv could never be committed
anyway, so a per-repo copy still needed an install step on every clone.

### 15.2 Release pipeline (GitHub Actions)

`ci.yml` runs on every PR and push to `main`:
- **Python:** pytest on Windows, macOS, and Linux × Python 3.11–3.13, with `uv sync --locked`.
- **Web:** `npm ci`, `npm run typecheck`, `npm run lint`, `npm test` (Vitest), `npm run build`.
- **End to end:** Playwright against the service with the fake CLI (Windows + Linux).
- **Lock file:** `uv lock --check`.

`release.yml` runs on tags `v*`:
1. Runs the CI jobs above.
2. Builds the dashboard into `src/agent_factory/dashboard_dist/`, then the wheel and sdist with
   `uv build`. The version comes from the tag (hatch-vcs). The wheel is pure Python (`py3-none-any`).
3. **Smoke install** on Windows and Linux:
   - `uv tool install ./dist/*.whl`;
   - `factory init --provider both` in a temp Git repo;
   - `factory serve` in the background, then `/api/health` and `/api/overview` respond, and the dashboard `index.html` loads;
   - `factory start` with the fake CLI completes a Fast job.
4. Publishes to **PyPI with trusted publishing** (OIDC; no stored tokens).
5. Creates a **GitHub Release** with the wheel, sdist, `SHA256SUMS`, and build provenance
   (`actions/attest-build-provenance`). Release notes come from the changelog and say
   whether repos need `factory upgrade`.

Pre-release tags (`v1.2.0rc1`) publish as pre-releases; users opt in with
`uv tool install --prerelease allow agent-factory`.

Versioning: semver.
- **Patch/minor:** never require repo changes. Repo config written by any 1.x engine works with any later 1.x.
- **Major:** may change repo files, which `factory upgrade` handles.

### 15.3 Installing

**Once per machine:**
```
uv tool install agent-factory          # or: pipx install agent-factory
factory doctor                          # checks git, claude/codex + login, agent-browser
```
If uv isn't installed, the README links to uv's official install instructions. `install.py`
remains as an optional bootstrap for users without uv:
- it checks for uv, and if it's missing, prints the official install command and asks before running it;
- then it runs `uv tool install agent-factory` and, with `--target`, `factory init` in that repo.

It never copies the runtime into a repo any more.

**Once per repo:**
```
cd C:\Source\projects\srt-gateway
factory init
git add factory .claude/skills/factory .agents/skills/factory .gitignore
git commit -m "Add agent factory"
```

**A teammate or fresh clone:** install the engine once. The repo is already set up, and the
first `factory` command registers it. If the engine is missing, the skill says how to install it.

### 15.4 Upgrading

| Situation | What the user does |
|---|---|
| New engine release | `uv tool upgrade agent-factory`, once per machine. The next `factory` command sees that `service.json` reports an older version and restarts the service. Running attempts keep going (the wrapper uses the base Python, §3.2) and are re-adopted. Running jobs keep their frozen roles and preamble (§10.5). |
| Release that changes repo files (major only) | The engine reports "srt-gateway needs `factory upgrade`" in the CLI and dashboard. Run it in the repo, review the small diff, commit. Until then, new jobs in that repo are refused with that message; running jobs continue. |
| A repo requires an older engine | Its `engine` specifier doesn't match, so commands exit 4 with: "srt-gateway needs agent-factory >=1.0,<2; installed 2.1.0. Install a matching version: `uv tool install agent-factory==1.*`" (one engine per machine; use the lowest common version, or upgrade the repo). |
| An ejected role is behind the built-in | Shown in `factory status` and the dashboard's roles list; `factory eject <role> --diff` shows the change. Nothing is automatic. |
| Roll back | `uv tool install agent-factory==<previous>`. Repo DB migrations are additive within a major version, so older 1.x engines can read DBs written by newer 1.x ones. CI tests this with the previous minor. |

### 15.5 Contributors

```
git clone …/agent-factory && cd agent-factory
uv sync                                   # Python env from uv.lock
cd web && npm ci && npm run build && cd ..
uv tool install --editable .              # `factory` command runs this checkout
```

- Dashboard work: `factory serve` plus `cd web && npm run dev`, with Vite proxying `/api` to 5099.
- `tools/sandbox.py` creates a throwaway repo with `FACTORY_HOME` pointing at a temp machine
  dir, so it never touches your real registry or ports, and runs `factory init` there.

### 15.6 Existing v1 installs and legacy jobs

`factory init` and `factory upgrade` detect a v1 layout (`factory/job.py`,
`factory/server.py`, `factory/dashboard/`, roles in `.claude/agents/` / `.codex/agents/`).
`factory upgrade` then:
1. converts `factory/config.json` to v2 (§5.4);
2. compares each v1 role file with the original v1 built-in text (hashes shipped in the
   package). Unchanged files are removed. Changed files are moved to `factory/roles/`
   (provider-specific names where needed), so customisations survive;
3. removes the v1 runtime files (`factory/*.py`, `factory/dashboard/`) and the v1 skills,
   then writes the new skill wrappers and `.gitignore` entries;
4. keeps `backlog.md` and `factory/jobs/` untouched;
5. prints the summary and leaves everything uncommitted for review.

Legacy jobs (`factory/jobs/*/job.json`) are listed in the dashboard as **Legacy
(read-only)**: reports are viewable, and Merge and Delete still work through the ported git
ops, but nothing can run them. The README's upgrade note says: "Finish or delete in-flight
jobs before upgrading."

---

## 16. Testing

### 16.1 Fake CLI

`tests/fake_cli.py` acts as either `claude` or `codex`, depending on `argv[0]` / a flag. The
adapters take an executable override from `FACTORY_CLAUDE_BIN` / `FACTORY_CODEX_BIN`. It is
driven by a scenario file named in the prompt or an env var:

```json
{ "events": "claude-success.jsonl", "report": "VERDICT: CHANGES\n## Findings\n- [major] a.py:3 — x",
  "commit": true, "sleep": 2, "exit": 0, "spawn_child_listening_on_port": true,
  "auth_error": false, "hang": false, "invocation_marker": "<dir>" }
```

Each invocation drops a marker file, so tests can assert exact launch counts. All tests set
`FACTORY_HOME` to a temp dir.

### 16.2 Engine and API (pytest, real git, real SQLite, fake CLI)

- Pipelines: Full and Fast happy paths; mixed providers in one group; the same role twice
  in one group; CHANGES → rewind within budget; budget exhausted → needs_human; BLOCKED; ESCALATE.
- Contracts: a missing report → one contract retry → needs_human; a stale report (mtime before launch) is rejected;
  a reviewer that changed the head is rejected; a builder with no commits is rejected.
- Concurrency: perJob and global limits hold under 2 repos × 3 jobs × 4 reviewers;
  round-robin across repos; one_live_attempt holds under concurrent claims.
- Ports: leases never overlap across repos; a block with a foreign listener is skipped;
  leases are released on merge/cancel/delete and kept for needs_human; exhaustion → `paused(ports)`.
- Stop/Resume: stop during a parallel group keeps completed executions; resume uses the exact session id; fresh-after-lost includes the warning.
- Timeouts, auth failure, permission denial, budget: each pauses with the right reason.
- Process trees: Stop kills a grandchild dev server (Windows and POSIX); ports are freed; foreign port holders are not killed.
- Merge: auto, verify, busy checkout, conflict, changed head, sensitive paths (auto blocked, human confirm).
- Config: v1 → v2 upgrade; validation errors per field; frozen config is not affected by later edits.
- Roles: resolution order (§5.3); no cross-provider fallback; eject writes provenance; outdated-eject detection.
- Version gate: a mismatched `engine` specifier → exit 4 with the fix; a service of another version is restarted.
- Registry: auto-register on first command; a missing repo folder shows as missing; remove/rename.
- API security: wrong Host, cross-site Origin, missing token, unknown repo id, path traversal on files.
- SSE: Last-Event-ID replay; reset on a gap; log batching; overview stream.
- Activity: `summarize()` per adapter against recorded Claude and Codex event fixtures
  (shell, read, edit, browser, commit, report, secret masking); throttling to ≤ 1/s;
  the state snapshot carries the latest activity; no `activity` rows in `events`.
- Packet: the cap is respected; never-truncated sections survive; omitted markers list paths.

### 16.3 Crash and upgrade injection

`FACTORY_CRASH_AT ∈ {after_claim, after_prepare, after_spawn, before_record, during_tail, before_advance_commit}`
makes the service call `os._exit(1)` at that point. Each test starts the service, lets it crash,
restarts it, and asserts:
- the fake CLI's invocation count is exactly 1, or 0 for `after_claim`/`after_prepare`, where relaunch is allowed;
- the final job state matches a crash-free run, or is `paused(process)` for a lost attempt.

Upgrade test: with an attempt running, replace the installed tool with a newer build
(`uv tool install --force`). On Windows, this must succeed while the attempt runs. The
restarted service re-adopts the attempt and the job completes.

### 16.4 Dashboard

- **Vitest:** `reducer.ts` for every event type, including reset and out-of-order replay;
  `layout.ts` for Fast, Full, a 16-reviewer group, two `onChanges` loops (lane stacking), and
  32 steps (width); `format.ts`; the dirty-settings guard; status text for every state.
- **Report safety:** a hostile report (`<script>`, `onerror=`, `javascript:` links, raw
  HTML) renders as inert text.
- **Playwright with the fake CLI:**
  - start from the backlog, and columns and pipeline nodes update over SSE;
  - activity lines change;
  - Stop/Resume;
  - provider/model editing keeps unsaved edits across SSE updates;
  - the Settings preview follows JSON edits and highlights invalid steps;
  - clicking a node opens the right report or log, and the keyboard moves between nodes;
  - the repo switcher and overview counts;
  - killing the server and reconnecting.
- **Visual:** Playwright screenshots of the overview, board, job detail, and pipeline view are
  compared against committed baselines (dark theme), so theme drift is caught. Contrast is
  checked with axe-core.

### 16.5 Install and upgrade

In CI on Windows and Linux (also part of the release smoke test, §15.2):
- `uv tool install` from the built wheel, then `factory doctor`;
- `factory init` writes exactly the expected files and is idempotent; `--dry-run` writes nothing;
- `factory upgrade` converts a v1 fixture repo: customised roles are kept in `factory/roles/`
  and unchanged ones removed; legacy jobs are listed read-only;
- install the previous minor, create jobs, upgrade to the new build, and the jobs still load;
  roll back, and the jobs still load.

### 16.6 Real smoke test

`tools/sandbox.py run --real` runs the sandbox's 3 features with Fast (Claude), then Full
(mixed providers, haiku / low effort), and prints the benchmark table from §0. It is not
part of CI.

---

## 17. Phases

Development happens in this repo on `main`. The v1 runtime stays installable from the last
v1 tag until Phase 4 publishes 1.0 of the new engine. Each phase's exit criteria must pass
before the next starts.

### 17.0 Models and cost for building this plan

Implementation is done with Codex, with Sol as the default builder. Each finished phase
is cross-reviewed with Astra through `codex exec review`, so a different model checks the
work. The runner supports `--reviewer codex|claude|none` (default `codex`).

**Codex-first execution:** Claude usage is currently unavailable. Session 0.2 captures
only Codex CLI evidence; 1.6 builds the provider-neutral interface and Codex adapter.
Claude spikes (1, 2, 6 and its half of 7), adapter implementation and real Claude/mixed
smoke tests move to session 1.11, skipped unless `--include-claude` is supplied.
The engine must run and be testable without Claude installed. Preserve partial baseline
and spike evidence; unknown or rate-limited measurements must never be presented as
successful experiments. Codex-only exit criteria allow later development to proceed,
but Claude/mixed parity remains pending and is required before release.

**Codex models** (from OpenAI's Codex models page, October 2026; prices per million tokens,
input/output, from third-party API price lists):

| Model | Positioning | API price (in / out) | Relative cost |
|---|---|---|---|
| `gpt-6.1-sol` | Recommended for complex coding and agentic work; "near-Astra performance at lower cost" | $2 / $10 | 1× |
| `gpt-6-astra` | Most capable; hardest end-to-end work | $10 / $50 | 5× |
| `gpt-6-luna` | Focused, high-volume, well-specified tasks | $0.10 / $0.50 | 0.05× |

Effort levels: Light (`low`), Medium, High, Extra High, Max (more thinking on one task),
and Ultra (parallel subagents; not available on Luna). Higher effort means more output
(reasoning) tokens, which are the expensive side.

**What cost means depends on how Codex is signed in:**
- **ChatGPT sign-in (Plus/Pro/Business):** no per-token bill. Usage counts against the plan's
  limits, and Astra, Max, and Ultra use them up much faster. Here, cost means how many
  sessions you get before hitting a limit.
- **API key:** billed per token at the prices above. Cached input is cheaper, so keep the plan
  doc and stable instructions at the start of every session.

**Rules that keep cost down without hurting quality:**
1. **Sol is the default.** Astra only where §6.4/§3.2-class correctness is at stake *and*
   Sol has failed twice on it. Never Astra for UI, scaffolding, or docs implementation.
   The separate Astra phase review is an explicit exception to this escalation rule.
2. **Effort follows risk, not size.** A large but routine change (dashboard pages) → Medium.
   A small but subtle change (claim protocol) → Extra High.
3. **Luna for mechanical, fully specified work:** copying srt-gateway's `ui/`, renaming,
   writing fixture JSONL files, README edits, turning a written test list into test stubs.
   If Luna's output needs more than one correction round, switch to Sol. A failed
   cheap run isn't savings.
4. **Small sessions.** One sub-area per session, starting with "read
   `docs/cli-orchestrator-plan.md` §X–§Y, then implement …". Long sessions resend growing
   context on every turn, and that's where most tokens go.
5. **Tests define done.** Every session ends with the relevant tests passing. Rework
   caused by vague goals costs more than any model choice.
6. **Max/Ultra only on demand.** Max when one hard problem is stuck at Extra High. Ultra only
   for work that splits into independent parts (Phase 3 pages), never for the engine core.
7. **Cross-review once per phase, not per commit.** Review the phase diff with Codex
   Astra (`high`, or `xhigh` for Phase 1). Sol · High fixes verified findings and adds
   regression tests. Claude Opus is optional through `--reviewer claude`; `none` does
   not satisfy review criteria. Completing deferred 1.11 requires a fresh Phase 1 parity
   review even if the Codex-only Phase 1 review already passed.

**Agent prompts.** Every session in every phase has a ready-to-paste prompt, with its
model and effort, in [`cli-orchestrator-prompts.md`](cli-orchestrator-prompts.md). It also
has a shared preamble (spec-first, tests define done, Windows first-class, no scope creep)
and the cross-review prompt (Astra by default).

**Budget shape.** Exact token counts aren't predictable, so plan by share, not by number:
- **Phase 1:** about 40% of the total. It's the most code and the hardest code.
- **Phase 3:** about 25%. A lot of UI, but cheap per line.
- **Phases 2 and 4:** about 15% each.
- **Phase 0:** about 5%.

If Phase 1 uses noticeably more than that, the usual cause is sessions that are too large
or repeated failed attempts. Split the work smaller before moving to a stronger model.
Record actual usage per phase in `docs/benchmarks.md`, next to the factory benchmarks, so
the next large project starts from real numbers.

### Phase 0: Baseline and spikes (2–3 days)

- Add timing capture to the current sandbox flow (step timestamps already exist in
  `job.json` history; parse the headless log for usage). Record Full on the 3 sandbox features × 2 runs.
- Spikes, each answered in this doc's §18 before Phase 1:
  1. `claude -p --permission-mode auto` behaviour on a denied command: exit code, `permission_denials`, whether it keeps working.
  2. Claude `stream-json` fields: `result` event, `usage`, `total_cost_usd`, error shapes for auth or rate limits.
  3. `codex exec --json` events: `thread.started`, `turn.completed` usage, error events, behaviour under `--approve-for-me` on denial.
  4. Codex system-level instructions in `exec` (`-c developer_instructions`?) vs. prepending to the prompt.
  5. Windows Job Object assignment from a `CREATE_NEW_PROCESS_GROUP` child, and killing a tree that includes `npm run dev`.
  6. `--session-id` + `--resume` with `-p` across a killed process.
  7. Prompt-cache hit rates with a stable `system.md` across two consecutive attempts (from usage data).
  8. `uv tool upgrade` on Windows while a process started with `sys._base_executable` runs a copied script: no file locks in the tool venv.
  9. PyPI name availability for `agent-factory` and a free command name (`factory`; see §18).

**Prompts:** sessions 0.1–0.4 in [`cli-orchestrator-prompts.md`](cli-orchestrator-prompts.md#phase-0-baseline-and-spikes).

**Models:**

| Work | Model · effort |
|---|---|
| Spikes 1–4, 6–7 (CLI behaviour, events, sessions, caching) | Sol · Medium |
| Spikes 5 and 8 (Windows Job Objects, upgrade under a running process) | Sol · High. Run them yourself on Windows; don't accept the model's assumptions. |
| Spike 9 (names), benchmark capture script | Luna · Medium |

The baseline sandbox runs use the factory's own settings (haiku/low as in `tools/sandbox.py`)
to keep them cheap. Run them twice, not more.

**Exit for Codex development:** a baseline table committed in `docs/benchmarks.md`
(partial results clearly labelled); Codex, Windows and names spikes answered. Claude
spikes are explicitly deferred to 1.11 and do not block Phase 1.

### Phase 1: Package, engine and runner, Full pipeline parity

- **Package skeleton:** `pyproject.toml`, `src/agent_factory/`, `factory` entry point,
  `paths.py`, `machine.db` + registry, `FACTORY_HOME`.
- **Engine:**
  - `db.py` + repo migration 0001;
  - `config.py` (v2 + upgrade); `roles.py` (built-ins + overrides);
  - `pipeline.py` (ported advance); `gitops.py`; `ports.py` (machine-wide leases).
- **Runner:** `run_cli.py` (base-Python launch), `supervisor.py`, `winjob.py`, provider-neutral
  adapter interface and Codex adapter first; Claude follows in deferred session 1.11.
- **Scheduling:** machine-wide limits with round-robin across repos; claim/launch/record;
  recovery; stop; timeouts; exit classification.
- **Packet v1:** sections 1–3 and 5–9. Acceptance criteria come from the spec.
- **CLI:** `factory init`, `doctor`, `start/next/status/stop/resume/approve/rework/delete`,
  `serve`, `service`, `repos`, and the API routes. Polling is fine for this phase.
- Built-in roles moved into the package. Skill wrapper templates.
- **Dashboard:** a minimal read-only page served from the engine, enough to watch jobs. The
  full dashboard comes in Phase 3.

**Prompts:** sessions 1.1–1.10 in [`cli-orchestrator-prompts.md`](cli-orchestrator-prompts.md#phase-1-package-engine-and-runner).

**Models** (the most expensive phase; correctness matters most here):

| Work | Model · effort |
|---|---|
| Package skeleton, `paths.py`, `pyproject.toml`, CLI argument parsing | Sol · Medium |
| `db.py`, migrations, registry, `config.py` (Pydantic + v1→v2) | Sol · High |
| Porting `job.advance` → `pipeline.py` with its existing tests | Sol · High |
| **Claim/launch/record + recovery (§6.4), `run_cli.py` + `winjob.py` (§3.2), stop/timeouts (§9), port leases** | **Sol · Extra High**. Escalate to **Astra · High** only if the §16.3 crash tests still fail after two Sol attempts. |
| Codex adapter, exit classification, `summarize()`; Claude in deferred 1.11 | Sol · High (from recorded provider fixtures) |
| Packet builder, `roles.py`, `gitops.py` port | Sol · High |
| Fake CLI, test fixtures, skill wrapper templates, minimal read-only page | Luna · Medium (switch to Sol if it needs repeated fixes) |
| Phase review | Codex Astra · Extra High; Sol · High fixes |

Suggested sessions:
1. skeleton + DB + registry;
2. config + roles;
3. pipeline port;
4. wrapper + Job Objects;
5. claim/launch/record + recovery + crash tests;
6. Codex adapter (Claude fixtures + adapter + parity in deferred 1.11);
7. ports + stop + timeouts;
8. packet;
9. CLI commands + API;
10. review fixes.

**Exit for Codex development:** §16.2 (provider-neutral and Codex cases) and §16.3 green
on Windows and Linux. Full runs the 3 sandbox features with Codex in two sandbox repos
at once with no port collisions. Zero duplicate launches in crash tests. Claude-specific
§16.2 cases and real Claude/mixed Full runs remain deferred to 1.11, required before release.

### Phase 2: Fast pipeline and measurement

- The `fast-approver` role, builder `## Acceptance`, approval `CHANGES`, sensitive-path guard.
- `factory eject`, and roles-outdated reporting.
- The usage/timing data through the API, and the benchmark command in `tools/sandbox.py`.
- Built-in roles slimmed (no history rereads; required sections).

**Prompts:** sessions 2.1–2.3 in [`cli-orchestrator-prompts.md`](cli-orchestrator-prompts.md#phase-2-fast-pipeline-and-measurement).

**Models:**

| Work | Model · effort |
|---|---|
| `fast-approver` role, slimming built-in roles (prompt writing) | Sol · High. Role text decides agent quality and token use on every future job. |
| Approval `CHANGES`, sensitive-path guard, `factory eject` | Sol · Medium |
| Benchmark command, usage/timing API | Sol · Medium |
| Phase review | Codex Astra · High; Sol · High fixes |

Benchmark runs cost real agent tokens. Use haiku/low or Luna/Light for the sandbox
comparison (Codex Luna/Light while Claude is deferred), plus **one** run with your
production Codex models to confirm the ratio holds. Defer Claude comparisons to 1.11.

**Exit:** Fast and Full benchmarked against the Phase 0 baseline, results in `docs/benchmarks.md`.
Fast + auto-merge is blocked on sensitive paths in tests.

### Phase 3: Dashboard

- `web/` scaffold with the srt-gateway stack; shared `ui/` and `styles.css` copied (§13.7) with `SOURCE.md`.
- The SSE event bus, the overview stream, and log streaming; the reducer applied to the Query cache.
- Pages:
  - shell with repo switcher;
  - overview;
  - board with activity lines and the compact pipeline strip;
  - job detail with the Pipeline / Reports / Timeline / Log / Usage tabs;
  - backlog;
  - repo and machine settings, with the pipeline preview and roles list.
- Suggested order: copy theme + primitives → shell + overview + board → job detail + pipeline
  view → backlog → settings → polish against srt-gateway screenshots.

**Prompts:** sessions 3.1–3.6 in [`cli-orchestrator-prompts.md`](cli-orchestrator-prompts.md#phase-3-dashboard).

**Models** (lots of code, mostly routine):

| Work | Model · effort |
|---|---|
| Copy srt-gateway `ui/`, `styles.css`, `components.json`; Vite/Tailwind scaffold | Luna · Medium |
| `reducer.ts`, `sse.ts`, Query cache wiring, dirty-settings guard | Sol · High (live-state bugs are hard to spot later) |
| Pipeline view `layout.ts` + components | Sol · High for `layout.ts`, Medium for the components |
| Pages: shell/overview, board, job detail tabs, backlog, settings | Sol · Medium. **Ultra** is reasonable here: the pages are independent once the store and API types exist. |
| Playwright + Vitest tests, visual baselines | Sol · Medium |
| Phase review | Codex Astra · High, plus a manual side-by-side check with srt-gateway |

**Exit:** §16.4 green; no polling left except refetch after reset.

### Phase 4: Release pipeline, migration, switch-over

- `ci.yml` and `release.yml` (§15.2), PyPI trusted publishing, GitHub Release with provenance.
- `factory upgrade` including v1 migration (§15.6); version gate and service restart on upgrade.
- `install.py` reduced to the optional uv bootstrap; README rewritten around
  `uv tool install` + `factory init`.
- v1 runtime files and `workflow.md` orchestration removed from this repo; `tools/sync_codex.py` removed.
- Before publishing, complete deferred session 1.11, its Claude/mixed parity tests and
  its fresh phase review. Do not claim Claude support without that evidence.
- Publish `1.0.0rc1`, migrate srt-gateway and pylon with `factory upgrade`, run real jobs in both, then publish `1.0.0`.

**Prompts:** sessions 4.1–4.4 in [`cli-orchestrator-prompts.md`](cli-orchestrator-prompts.md#phase-4-release-migration-switch-over).

**Models:**

| Work | Model · effort |
|---|---|
| `ci.yml`, `release.yml`, trusted publishing, provenance | Sol · High (packaging mistakes surface only at release time) |
| `factory upgrade` v1 migration, version gate, service restart on upgrade | Sol · High |
| `install.py` bootstrap, README rewrite, removing v1 files | Luna · Medium |
| Phase review | Codex Astra · High, including security of the release workflow and installer |

Release-candidate dry runs: push `rc` tags to TestPyPI first, which costs nothing.

**Exit:** §16.5 green. On a clean Windows machine, `uv tool install agent-factory` →
`factory init` → dashboard Start runs a Fast job with no client session open. srt-gateway
and pylon are both migrated and running jobs at the same time.

### Phase 5 (conditional): Evidence reuse and code index

Start only if Phase 2 data shows a measurable win: for example, reviewers rerunning the
builder's test suite take > 25% of review time, or agents spend > 30% of tokens on
exploration reads before their first edit or finding.

- **Evidence reuse:** record `(command, cwd, head, sha256 of inputs)` for checks run by the
  engine itself, not taken from agent claims. Reuse only on an exact match, and only when
  `AGENTS.md` doesn't require fresh checks. Reviewers still run the checks their lens requires.
- **Code index:** Tree-sitter (Python, JS, TS, TSX) + FTS5 over tracked files, keyed by blob
  sha, incremental per worktree. It adds a "likely relevant files" section to the packet. Ship it
  only if an A/B on the sandbox shows lower tokens or time without a lower approval rate.

**Models:** Sol · High for both. The index is only worth building if Phase 2's numbers show
it pays for itself in agent tokens. Its own build cost is part of that decision.

---

## 18. Open questions

To be answered by Phase 0 spikes or by the maintainer:

1. Does `claude -p --permission-mode auto` stop or continue after a denial, and how is that reported?
   Deferred to session 1.11 until Claude usage returns; partial rate-limit captures are not an answer.
2. Is there a Codex `exec` flag for system/developer instructions?
3. **Names:** is `agent-factory` free on PyPI? Is `factory` too generic a command name
   (collisions with other tools on PATH)? Proposal: package `agent-factory`, commands
   `agent-factory` and the short alias `factory`; skills call `agent-factory` to avoid any clash.
4. Should the default pipeline for new repos be `fast` or `full`? Proposal: `full` as
   `defaultPipeline` (no behaviour change for migrated repos), `fast` offered next to Start.
5. Keep an optional interactive mode, where a Claude/Codex session watches the job and reports?
   Proposal: no. `factory start --wait` covers watching from a session.
6. Default global concurrency of 6: tune it after Phase 2 against rate limits and the machine's capacity
   (UX/UI reviewers run dev servers and browsers).
7. Start the service at login (Windows Task Scheduler / launchd / systemd user unit)?
   Proposal: not in 1.0. Commands start it on demand. Revisit if jobs started from the
   dashboard after a reboot become a common need.
8. Private distribution instead of public PyPI? If so, use `uv tool install git+https://github.com/koderi-dp/agent-factory@v1.0.0`.
   That works only if the built dashboard is present, so it would need the GitHub Release wheel URL instead:
   `uv tool install https://github.com/…/releases/download/v1.0.0/agent_factory-1.0.0-py3-none-any.whl`.
