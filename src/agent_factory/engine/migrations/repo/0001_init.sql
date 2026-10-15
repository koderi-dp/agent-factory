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
