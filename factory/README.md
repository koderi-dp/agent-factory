# Software factory

A Claude Code loop that takes a one-line feature to a merged branch: a spec, a
build, four parallel reviews (security, UX, UI, code) over up to three rounds,
and a final approve-or-escalate gate. The orchestration rules are in
`.claude/skills/factory/SKILL.md`; each step's agent is in `.claude/agents/`.
See the agent-factory repo's README for setup.

## Use it

| Command | What it does |
| --- | --- |
| `/factory <feature>` | Runs one job from spec to merge, in its own worktree. |
| `/factory next` | Takes the first `- [ ]` item from `backlog.md`. |
| `/factory approve <job-id>` | Merges a job waiting for you. |
| `/factory rework <job-id> <note>` | Sends a waiting job back to a fresh builder with your note. |
| `/factory resume <job-id>` | Picks up a job whose session ended mid-run. |
| `/factory dashboard` | Starts the board at `http://127.0.0.1:5099/dashboard.html`. |

Run several jobs at once by starting `/factory` in several sessions. Each job
has its own worktree, branch, ports and `job.json`.

## Files

- `backlog.md`: the queue for `/factory next`.
- `config.json`: settings: the model for each step, review rounds, and whether
  an approved job merges straight away (`auto`) or waits for you (`verify`).
- `dashboard.html`, `dashboard.css`, `dashboard.js`: the board.
- `server.py`: serves the board on 127.0.0.1 and lets it change settings, merge
  or delete jobs and edit the backlog. `python factory/server.py [--port 5099]`.
  Any static server that lists directories also works, read-only.
- `job.py`: the only way to change a `job.json`, merge a job or delete one. The
  orchestrator and the server both use it. `python factory/job.py --help`.
- `jobs/<job-id>/`: one folder per job, with `job.json`, `spec.md`,
  `build.md`, `round-<N>/review-*.md` and `decision.md`. Gitignored.
