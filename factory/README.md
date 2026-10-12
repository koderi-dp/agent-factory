# Software factory

A Claude Code loop that takes a one-line feature to a merged branch: a spec, a
build, four parallel reviews (security, UX, UI, code) with up to three rounds,
and a final approve-or-escalate gate. The orchestration rules are in
`.claude/skills/factory/SKILL.md`; each step's agent is in `.claude/agents/`.

## Add it to a repo

1. Copy this folder to `<repo>/factory/` and the `claude/` folder's `agents/`
   and `skills/factory/` into `<repo>/.claude/`.
2. Add `/factory/jobs/` to `.gitignore`. Jobs are runtime state.
3. Make sure the repo's `AGENTS.md` (or `CLAUDE.md`) says how to set up a fresh
   checkout, run the dev server on a given port, and run the validation
   commands. Every agent works from it; nothing repo-specific lives in the
   factory files.
4. Have Python 3 (for `job.py` and the dashboard server) and the
   [`agent-browser`](https://www.npmjs.com/package/agent-browser) CLI (for the
   agents that look at pages).

## Use it

| Command | What it does |
| --- | --- |
| `/factory <feature>` | Runs one job from spec to merge, in its own worktree. |
| `/factory next` | Takes the first `- [ ]` item from `backlog.md`. |
| `/factory approve <job-id>` | Merges a job waiting in Needs human. |
| `/factory rework <job-id> <note>` | Sends a waiting job back to a fresh builder with your note. |
| `/factory resume <job-id>` | Picks up a job whose session ended mid-run. |
| `/factory dashboard` | Starts the board at `http://127.0.0.1:5099/dashboard.html`. |

Run several jobs at once by starting `/factory` in several sessions. Each job
has its own worktree, branch, ports and `job.json`.

## Files

- `backlog.md`: the queue for `/factory next`.
- `dashboard.html`: the board. It reads `jobs/` over HTTP, so serve this folder
  with a server that lists directories, such as
  `python -m http.server 5099 --bind 127.0.0.1 --directory factory`.
- `job.py`: the only way the orchestrator changes a `job.json`. It keeps the
  history and timestamps and writes the file in one step.
- `jobs/<job-id>/`: one folder per job, with `job.json`, `spec.md`,
  `build.md`, `round-<N>/review-*.md` and `decision.md`.
