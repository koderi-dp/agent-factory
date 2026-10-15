---
name: factory
description: Run the agent factory in Codex, from feature or backlog through spec, build, independent reviews and approval. Also handles factory watch, dashboard, approve, rework and resume requests.
---

# Factory for Codex

Run the requested mode using [the workflow](references/workflow.md). Read it before
starting or resuming a job. Use `python factory/job.py` for all job state and merges.
Commands in that reference use `$factory`, for example `$factory next` or
`$factory Add dark mode`. The workflow is shared with the Claude implementation;
the Codex-specific rules below govern how to execute it here.

## Before running

Run from the project's Git root. A local `main` branch with at least one commit,
the installed factory files, and the project's setup/validation instructions in
`AGENTS.md` (or `CLAUDE.md`) are required for jobs. The dashboard can run before Git
setup. Explain any missing requirement before creating a job.

Explicitly delegate each workflow role to subagents. Use the native spawning,
waiting, and follow-up tools available in this Codex session. If subagents are
disabled or unavailable, report that blocker; do not silently perform all roles
in the orchestrator. Reviewers must be independent of the builder.

## Agent roles and settings

Every role resolves by name to `<repo>/.codex/agents/<uses>.md`, including
built-ins and user-created agents. These Markdown files are read by the factory;
they do not require native Codex agent registration or TOML files. Read the task's
instructions file and include its body in a fresh general-purpose subagent's task,
followed by the job-specific dispatch prompt. Frontmatter declares type and output.

Use each `job.py step` task's explicit model and effort, frozen from that step's
`providers.codex` settings when the job was created. Pass them through the native
spawn fields, with a fresh context when required for overrides. If a client cannot
apply those choices, report it instead of silently substituting a model.

Keep the builder's returned agent ID in `job.json`. To rework a completed builder,
use the native follow-up operation that starts another turn (for example
`followup_task`); sending a message alone may not restart an idle agent. If its
thread is gone, create a fresh builder using the interrupted-build prompt.
Close completed spec/reviewer/approver threads when supported. Run the four
reviewers in as many parallel slots as are available, and wait for all four
report files before resuming the builder.

## Files, permissions and recovery

Use absolute paths for the job reports, worktree and role files. Run commands
with the worktree as their working directory using your shell tool's `workdir`
option, or the equivalent for your shell. Translate Bash examples in the
reference to the actual shell; on Windows use PowerShell commands as needed.
Use explicit UTF-8 encoding when reading or writing Markdown and JSON with
Python; Windows' default code page may not encode report text or backlog arrows.

Subagents inherit the current session's permissions. Jobs need access to the
worktree, `<repo>/factory/jobs/`, and Git metadata. Follow the client's normal
approval flow if access is needed. An unavailable permission in a headless run
is a blocker: record `needs-human` with what failed when job state is writable.
Never switch to bypass mode or change the user's Codex configuration yourself.

The config `provider` selects which client the dashboard starts; the active
Codex session always uses Codex roles and settings. Existing jobs and report
files can be resumed with either client after confirming that their previous
orchestrator is stopped. Builder IDs from another session are not reusable.

Headless Codex logs are JSONL in `factory/jobs/headless-*.log`. Matching
`.session.json` files hold the exact session ID, permission mode and exit code.
Resume a stopped session with `python factory/codex_runner.py resume <session-file>`;
never use `--last`, which could resume a different job. Confirm the prior process
is stopped before resuming. Use `$factory resume <job-id>` to recover a job in a
fresh interactive session instead.
