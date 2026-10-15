# Software factory

A configurable Claude Code or Codex pipeline that takes a feature to a merged
branch. The default runs spec, build, four reviews and approval. Configure your
own ordered steps with `pipeline` in
`config.json` or dashboard Settings. The orchestration rules are in the factory
skill's `references/workflow.md`; Python validates and advances the saved steps.
See the agent-factory repo's README for setup.

For Codex, the entrypoint is `.agents/skills/factory/SKILL.md` and the seven roles
are Markdown instructions under `.codex/agents/*.md`. Use `$factory` in place of `/factory` for every command
below. Install either client or both with the single-file installer's `--provider`.
Dashboard Settings selects which client Start launches. Each pipeline execution
has explicit model and effort settings under `providers.claude` and
`providers.codex`. The top-level `provider` selects the Start client.

Codex headless runs need a signed-in Codex CLI. Their JSONL logs have matching
`.session.json` metadata under `jobs/`. After stopping the prior run, resume the
exact session with `python factory/codex_runner.py resume <session-file>`, or use
`$factory resume <job-id>` from a fresh Codex session. Automatic mode retains
Codex's workspace-write sandbox and automatic approval review.

## Use it

| Command | What it does |
| --- | --- |
| `/factory <feature>` | Runs one job from spec to merge, in its own worktree. |
| `/factory next` | Takes the first `- [ ]` item from `backlog.md`. |
| `/factory watch` | Runs the items you press Start on in the dashboard. |
| `/factory approve <job-id>` | Merges a job waiting for you. |
| `/factory rework <job-id> <note>` | Sends a waiting job back to a fresh builder with your note. |
| `/factory resume <job-id>` | Picks up a job whose session ended mid-run. |
| `/factory dashboard` | Starts the board at `http://127.0.0.1:5099/`. |

Run several jobs at once by starting `/factory` in several sessions. Each job
has its own worktree, branch, ports and `job.json`.

## Files

- `backlog.md`: the queue for `/factory next`.
- `config.json`: settings: the model and effort for each step, review rounds, and whether
  an approved job merges straight away (`auto`) or waits for you (`verify`).
- `dashboard/`: the board (`index.html`, its CSS and JS, and SortableJS for dragging backlog cards).
- `server.py`: serves the board on 127.0.0.1 and lets it change settings, merge
  or delete jobs and edit the backlog. `python factory/server.py [--port 5099]`.
  Any static server that lists directories also works, read-only.
- `job.py`: the only way to change a `job.json`, merge a job or delete one. The
  orchestrator and the server both use it. `python factory/job.py --help`.
- `pipeline.py`: validates step IDs, agent definitions, report contracts and
  backward retry targets. New jobs snapshot their resolved pipeline, retry limit,
  merge policy and provider model/effort choices. Config edits apply to new jobs.
- `job.py step <id>` returns pending agent tasks. `job.py advance <id>` validates
  fresh reports and branch state, archives evidence and transitions the job.
- `.claude/agents/<uses>.md` or `.codex/agents/<uses>.md`: role instructions,
  resolved by name for the selected provider. Each file declares type
  (task/review/approval) and output in frontmatter. Add a file to define a new role.
- `jobs/<job-id>/`: one folder per job, with `job.json`, `spec.md`,
  `build.md`, `round-<N>/review-*.md` and `decision.md`. Gitignored.
