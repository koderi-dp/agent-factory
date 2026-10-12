# Agent factory

A software factory for [Claude Code](https://code.claude.com). You give it a one-line feature request; a team of agents writes a spec, builds it on its own branch, reviews it from four angles, fixes what the reviewers find, and either merges it into `main` or hands it to you with the exact decision it needs.

```
feature → spec-writer → builder → [security, ux, ui, code] → wait for all
                           ▲                                      │
                           └──── any CHANGES: resume builder ◄────┤
                                                                  ▼ all PASS
            merge to main ◄─ auto ─ APPROVE ─ approver ─ ESCALATE → needs you
                                      └─ verify → waits for you to check it and merge
```

| Step | Agent | Writes |
| --- | --- | --- |
| Spec | `spec-writer` | `spec.md`: context, requirements, testable acceptance criteria, pages to check |
| Build | `builder` | commits on `factory/<job-id>` and `build.md` with evidence for every criterion |
| Review | `reviewer-security`, `reviewer-ux`, `reviewer-ui`, `reviewer-code` (in parallel) | `round-N/review-*.md`, each `PASS` or `CHANGES` |
| Approve | `approver` | `decision.md`: `APPROVE` (merge) or `ESCALATE` (you decide) |

The builder gets up to three review rounds by default. Every job runs in its own git worktree, on its own branch and ports, so you can run several at once and your own checkout is never touched until a merge.

## Requirements

- [Claude Code](https://code.claude.com).
- Git, with the repo's main branch named `main`.
- Python 3, for `factory/job.py` and the dashboard server. Nothing to install: they use only the standard library.
- [agent-browser](https://github.com/vercel-labs/agent-browser), for the agents that open pages:
  ```bash
  npm install -g agent-browser
  agent-browser install
  ```

## Add it to your repo

The factory is generic: nothing in it is specific to one project. It learns your project from your repo's `AGENTS.md` (or `CLAUDE.md`).

**1. Copy the files.** From a clone of this repo, with `<repo>` your project:

```powershell
$repo = 'C:\code\my-app'
New-Item -ItemType Directory -Force "$repo\.claude\skills", "$repo\factory" | Out-Null
Copy-Item -Recurse -Force claude\agents "$repo\.claude\"
Copy-Item -Recurse -Force claude\skills\factory "$repo\.claude\skills\"
Copy-Item -Force factory\*.* "$repo\factory\"
```

```bash
repo=~/code/my-app
mkdir -p "$repo/.claude/skills" "$repo/factory"
cp -r claude/agents "$repo/.claude/"
cp -r claude/skills/factory "$repo/.claude/skills/"
cp factory/*.* "$repo/factory/"
```

Agents also read any skill under your repo's `.claude/skills/` that covers the area they work in, so adding your own is how you teach them your project's traps.

**2. Ignore job state.** Add this line to your repo's `.gitignore`:

```
/factory/jobs/
```

**3. Describe your repo in `AGENTS.md` (or `CLAUDE.md`).** Every agent reads it. It needs to say:

- how to set up a fresh checkout (install dependencies, for example);
- how to start the dev server **on a given port**, and anything the page needs to run locally (an API, a database) with its own data;
- the validation commands: tests, build, lint, type-check;
- the conventions: code style, where things go, commit message style.

For example:

```markdown
## Dev server
npm run dev -- --port <port>          # the API it talks to is set with API_URL

## Validation
npm test
npm run lint
npm run build
```

The agents never touch your running app, database, containers or real accounts. Anything they need running, they start themselves from the job's worktree on the job's ports, so the dev server and backing services must be runnable that way.

**4. Commit** `.claude/agents/`, `.claude/skills/factory/`, `factory/` and the `.gitignore` change.

**5. Choose your settings** in the dashboard (below), or edit `factory/config.json`.

## Use it

Open Claude Code in your repo and type one of these:

| Command | What it does |
| --- | --- |
| `/factory <feature>` | Runs one job, from spec to merge. Example: `/factory Add a dark mode toggle to settings` |
| `/factory next` | Takes the first unchecked `- [ ]` item from `factory/backlog.md` and runs it. |
| `/factory approve <job-id>` | Merges a job that is waiting for you (or use the dashboard's Merge button). |
| `/factory rework <job-id> <note>` | Sends a waiting job back to a fresh builder with your note. |
| `/factory resume <job-id>` | Picks up a job whose Claude Code session ended mid-run. |
| `/factory dashboard` | Starts the board at `http://127.0.0.1:5099/dashboard.html`. |

The session you type the command in becomes the job's orchestrator: it spawns the agents, keeps the job's state, and reports one line per step. Keep it open until the job finishes.

**Queue work** in the dashboard's backlog box, or by adding lines to `factory/backlog.md`:

```markdown
- [ ] Add a dark mode toggle to settings
- [ ] Show the build version in the footer
```

**Run jobs in parallel** by opening several Claude Code sessions in the repo and starting `/factory` (or `/factory next`) in each.

## When a job needs you

A job stops in **Needs human** when:

- the approver approved it and the merge setting is **verify**: check the branch, then merge it;
- the approver escalates: the change touches auth, payments, permissions, deploy or CI config, a schema migration or a new dependency, or rests on a product decision. `decision.md` says exactly what it needs from you;
- the builder is blocked (`STATUS: BLOCKED` in `build.md`), or the review rounds ran out;
- the job was approved but couldn't merge yet: your main checkout was on another branch, had something staged, or had uncommitted edits in files the job also changes. Uncommitted edits to other files don't get in the way.

The branch and its worktree stay for you to inspect, at `<repo>/../<repo-name>-factory/<job-id>/`. Then merge it with the dashboard's **Merge** button or `/factory approve <job-id>`, or send it back with `/factory rework <job-id> <what to change>`. **Delete job** on the dashboard throws a job away: its worktree, its branch and its folder.

Merged jobs can still have **follow-ups**: checks only you can do (on your real environment) or small decisions left for later. They are listed under `## Follow-ups` in `decision.md`, counted on the dashboard card, and reported when the job ends.

The factory never pushes. Merges land in your local `main`.

## The dashboard

`/factory dashboard` starts it, or start it yourself from your repo:

```bash
python factory/server.py
```

Then open `http://127.0.0.1:5099/dashboard.html` (`--port` picks another port). It shows every job from every session, live:

- a column per stage, with each review round's verdicts as chips, and the model each step uses;
- how long a job has been in its stage, and a **quiet** warning with the resume command when a running job hasn't moved for three hours;
- the backlog queue, and a count of jobs that need you (also in the tab title);
- click a card to read its spec, build notes, reviews and decision, with the stage timeline. The URL keeps the open file, so you can share or reload it.

And it lets you change things:

- **Settings**: the model for each step, how many review rounds a job gets, and what happens after APPROVE: merge straight away, or wait for you to check it first. Saved to `factory/config.json`; running jobs pick changes up at their next step.
- **Merge** and **Delete job** buttons on a job's card.
- **Backlog**: add, reorder and remove queued features.

The server listens on 127.0.0.1 only and refuses requests from other sites. Served by any plain static server instead (`python -m http.server --directory factory`), the board still works but is read-only.

## What a job leaves behind

```
factory/jobs/<job-id>/
  job.json            state: stage, round, verdicts, timeline (written only through job.py)
  spec.md             what to build and how to check it
  build.md            what was built, evidence per criterion, deviations, round log
  round-<N>/review-security.md, review-ux.md, review-ui.md, review-code.md
  rework-<k>.md       your notes from /factory rework
  decision.md         APPROVE or ESCALATE, why, and follow-ups
```

Job `n` uses ports `5100 + 10n` to `5108 + 10n`: three each for the builder and the UX and UI reviewers. Job 003 uses 5130–5138.

## Customising

- **Models, review rounds and merge policy:** the dashboard's Settings, or `factory/config.json`. A model set to `default` uses the `model:` line in the agent's file under `.claude/agents/`.
- **What each reviewer checks, and what the approver escalates:** the agent files are plain Markdown instructions; edit them.
- **Project knowledge:** add a skill under `.claude/skills/<name>/SKILL.md`. Agents read the ones whose description covers their area.

Claude Code picks up edits to agent files in a running session; no restart needed.

## Working on this repo

`claude/` is the source of the agents and skills. `sync-claude.ps1` copies it into `.claude/` (which is gitignored), so the factory runs in this repo too:

```powershell
.\sync-claude.ps1           # copy changed files into .claude
.\sync-claude.ps1 -DryRun   # list what would change
```

## License

See [LICENSE](LICENSE).
