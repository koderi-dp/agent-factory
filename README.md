# Agent factory

A software factory for [Claude Code](https://code.claude.com) and [Codex](https://learn.chatgpt.com/docs/codex-cli). You give it a one-line feature request; a team of agents writes a spec, builds it on its own branch, reviews it from four angles, fixes what the reviewers find, and either merges it into `main` or hands it to you with the exact decision it needs.

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

This is the default pipeline. You can reorder or omit built-in steps, select reviewers, and add your own task, review and approval agents in `factory/config.json`. Every job runs in its own git worktree, on its own branch and ports, so you can run several at once and your own checkout is never touched until a merge.

## Requirements

- Claude Code or a current Codex client with subagent support. Headless Codex needs Codex CLI on PATH and signed in (`codex login`).
- To run factory jobs: Git, with a local `main` branch and at least one commit. Installation does not require Git.
- Python 3.9+, for the installer, `factory/job.py` and the dashboard server. Nothing to install: they use only the standard library.
- [agent-browser](https://github.com/vercel-labs/agent-browser), for the agents that open pages:
  ```bash
  npm install -g agent-browser
  agent-browser install
  ```

## Add it to your repo

The factory is generic: nothing in it is specific to one project. It learns your project from your repo's `AGENTS.md` (or `CLAUDE.md`).

**1. Install.** Run in your project folder. Any existing folder works; Git is needed only when you run factory jobs.

Windows PowerShell:

```powershell
Invoke-RestMethod https://raw.githubusercontent.com/koderi-dp/agent-factory/main/install.py | python -
```

macOS / Linux:

```bash
curl -fsSL https://raw.githubusercontent.com/koderi-dp/agent-factory/main/install.py | python3 -
```

Or download the single [install.py](install.py) file, inspect it, and run it:

```bash
python install.py --target /path/to/my-app
```

Choose a client with `--provider` (default `claude`):

```powershell
python install.py --target C:\code\my-app --provider codex
python install.py --target C:\code\my-app --provider both
```

With a piped download, append `--provider codex` or `--provider both` after `python -` / `python3 -`. Replace example target paths with your own existing project folder.

The installer downloads the factory from GitHub, installs `factory/`, and adds `/factory/jobs/` to `.gitignore`. Claude gets agents and four skills under `.claude/`. Codex gets seven Markdown agent instructions under `.codex/agents/` and a factory skill under `.agents/skills/factory/`. It preserves existing `factory/config.json`, `factory/backlog.md`, jobs, and unrelated agents and skills. It does not install dependencies or commit changes.

A fresh Codex-only install selects Codex for dashboard Start. Installing both defaults to Claude. Existing settings stay as they are; change the Start client in dashboard Settings after adding another provider.

Preview with `--dry-run`. If existing factory files differ, it stops before writing anything; use `--force` to replace them when updating. Settings and backlog are preserved even with `--force`.

```bash
python install.py --target /path/to/my-app --dry-run
python install.py --target /path/to/my-app --force
python install.py --target /path/to/my-app --source /path/to/agent-factory
```

Downloads use the `main` branch. `--repo owner/repo` selects a fork. `--source` installs from a local checkout without a network request. With a piped install, pass options after `python -` or `python3 -`.

**2. Check requirements.** Install any missing tools listed above. Agents also read any skill under your repo's `.claude/skills/` that covers the area they work in, so adding your own is how you teach them your project's traps.

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

**4. Commit** the installed agent and skill directories (`.claude/` for Claude; `.codex/agents/` and `.agents/skills/factory/` for Codex), `factory/` and the `.gitignore` change.

**5. Choose your settings** in the dashboard (below), or edit `factory/config.json`.

## Use it

Open Claude Code in your repo and type one of these:

| Command | What it does |
| --- | --- |
| `/factory <feature>` | Runs one job, from spec to merge. Example: `/factory Add a dark mode toggle to settings` |
| `/factory next` | Takes the first unchecked `- [ ]` item from `factory/backlog.md` and runs it. |
| `/factory watch` | Waits for the dashboard's **Start** button and runs each item you start there. Leave it open in a session. |
| `/factory approve <job-id>` | Merges a job that is waiting for you (or use the dashboard's Merge button). |
| `/factory rework <job-id> <note>` | Sends a waiting job back to a fresh builder with your note. |
| `/factory resume <job-id>` | Picks up a job whose Claude Code session ended mid-run. |
| `/factory dashboard` | Starts the board at `http://127.0.0.1:5099/`. |

In Codex, use the same modes with `$factory`:

```text
$factory Add a dark mode toggle to settings
$factory next
$factory watch
$factory approve 001-dark-mode
$factory rework 001-dark-mode Remember the system setting
$factory resume 001-dark-mode
$factory dashboard
```

The Codex skill delegates spec, build, four review lenses and approval to separate subagents. Reviewers run in parallel up to the client's capacity. Each pipeline execution selects its model and reasoning effort in `providers.codex`; the installed config uses `gpt-6.1-sol` with `medium` effort. Model availability and supported effort levels depend on your Codex account and client.

`/factory-next`, `/factory-watch` and `/factory-dashboard` are the same commands as their own entries in the `/` menu, and typing `/factory ` shows the rest as a hint.

The session you type the command in becomes the job's orchestrator: it spawns the agents, keeps the job's state, and reports one line per step. Keep it open until the job finishes.

**Queue work** in the dashboard's backlog box, or by adding lines to `factory/backlog.md`:

```markdown
- [ ] Add a dark mode toggle to settings
- [ ] Show the build version in the footer
```

**Run jobs in parallel** by opening several Claude Code sessions in the repo and starting `/factory` (or `/factory next`) in each.

## Define your pipeline

Edit `pipeline` in `factory/config.json`, or use its JSON editor in dashboard Settings. The installed config shows the default spec → build → four reviews → approval sequence. Omitting the pipeline selects that default sequence.

For example, add a documentation task and use two of the built-in reviewers:

```json
{
  "pipeline": [
    {
      "id": "spec",
      "uses": "spec-writer",
      "providers": {
        "claude": {
          "model": "opus",
          "effort": "medium"
        },
        "codex": {
          "model": "gpt-6.1-sol",
          "effort": "medium"
        }
      }
    },
    {
      "id": "build",
      "uses": "builder",
      "providers": {
        "claude": {
          "model": "opus",
          "effort": "medium"
        },
        "codex": {
          "model": "gpt-6.1-sol",
          "effort": "medium"
        }
      }
    },
    {
      "id": "docs",
      "uses": "docs-writer",
      "label": "Documentation",
      "providers": {
        "claude": {
          "model": "opus",
          "effort": "medium"
        },
        "codex": {
          "model": "gpt-6.1-sol",
          "effort": "medium"
        }
      }
    },
    {
      "id": "review",
      "parallel": [
        {
          "id": "reviewer-security",
          "uses": "reviewer-security",
          "providers": {
            "claude": {
              "model": "opus",
              "effort": "medium"
            },
            "codex": {
              "model": "gpt-6.1-sol",
              "effort": "medium"
            }
          }
        },
        {
          "id": "reviewer-code",
          "uses": "reviewer-code",
          "providers": {
            "claude": {
              "model": "opus",
              "effort": "medium"
            },
            "codex": {
              "model": "gpt-6.1-sol",
              "effort": "medium"
            }
          }
        }
      ],
      "onChanges": "build"
    },
    {
      "id": "approve",
      "uses": "approver",
      "providers": {
        "claude": {
          "model": "opus",
          "effort": "medium"
        },
        "codex": {
          "model": "gpt-6.1-sol",
          "effort": "medium"
        }
      }
    }
  ],
  "maxRounds": 3,
  "merge": "verify"
}
```

The top-level `provider` selects which client dashboard Start launches (`claude` or `codex`). Each execution then uses that client's settings from its own `providers` block.

Create `.claude/agents/docs-writer.md` for Claude, or `.codex/agents/docs-writer.md` for Codex. If you use both providers, create the file in both folders. Each can have different instructions. For example:

```markdown
---
name: docs-writer
description: Update documentation for a factory job.
type: task
output: docs.md
---

Update the documentation for this job's feature. Read the feature request,
available upstream reports and the branch diff. Work only in the job worktree.
Run relevant checks, commit documentation changes, and leave the worktree clean.
Write the assigned docs.md report with STATUS: READY on its first line, then
describe changes and validation. Use STATUS: BLOCKED if you cannot finish.
```

Every `uses` value is a role name, such as `builder` or `docs-writer`. It resolves to `.<provider>/agents/<uses>.md` in the project root. Names cannot contain paths or extensions. Built-in and user-created roles follow the same lookup. Missing or invalid files produce an error with their path; there is no fallback to the other provider. Commit the files with your config. The factory reads their Markdown instructions and delegates them to general-purpose agents, so native Codex TOML registration is unnecessary.

| Step type | Report's first line | Behavior |
| --- | --- | --- |
| `task` | `STATUS: READY` or `STATUS: BLOCKED` | Can change and commit code/docs. Built-in spec-writer instead writes a `# ` heading. |
| `review` | `VERDICT: PASS` or `VERDICT: CHANGES` | Checks the branch and writes its report; can run in a parallel reviewer group. |
| `approval` | `APPROVE` or `ESCALATE` | Makes the final decision; the factory performs any merge. |

Each step needs a unique `id` and either one `uses` agent or a `parallel` list of reviewer objects. Every execution has its own `id`, `uses` and `providers`, with an explicit `model` and `effort` for each configured provider. A job requires settings for its selected provider on every execution. A parallel group cannot repeat the same role. Optional `label` controls its dashboard heading. Pipelines have at most 32 steps, parallel groups at most 16 reviewers, and must end with exactly one approval step. The terminal names `approved`, `merged` and `needs-human` are reserved. Config is validated before saving or creating a job.

For a review step, `onChanges` names an earlier task to return to. All downstream results are invalidated and those steps run again. `maxRounds` bounds the whole job to `maxRounds - 1` automatic returns, even with multiple review groups. Without `onChanges`, CHANGES pauses for a human. BLOCKED and ESCALATE also pause. Rework preserves history and resets that allowance.

Each agent file declares `type` and a unique Markdown `output` filename in its frontmatter. Optional `contract: spec` supports heading-based task reports; otherwise the report contracts above apply. Report filenames must be unique among the selected roles and cannot overwrite job request/rework notes. Review reports go under `round-N/`; task and decision reports go in the job folder. Every completed attempt is archived under `steps/<step-id>/<attempt>/`, and `job.json` records its verdicts and branch head. Custom tasks receive the builder's ports; custom reviewers receive no ports, so use task steps or built-in UX/UI reviewers for checks needing services.

**Each job saves its resolved pipeline, agent definitions, provider model/effort choices, retry limit and merge policy.** Later config edits affect new jobs. Dashboard dropdowns edit the same per-execution `providers` blocks as the JSON editor. Global model/effort maps, string reviewer lists and `default` values are not supported.

The board shows configured columns and any additional steps from existing jobs. It shows only the reviewers selected for each run. Changing a branch after a review invalidates that evidence; changing it after final approval prevents a merge. Put tasks that modify the branch before review.

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

Then open `http://127.0.0.1:5099/` (`--port` picks another port). It shows every job from every session, live:

- a column per stage, with each review round's verdicts as chips, and the model each step uses;
- how long a job has been in its stage, and a **quiet** warning with the resume command when a running job hasn't moved for three hours;
- the backlog queue, and a count of jobs that need you (also in the tab title);
- click a card to read its spec, build notes, reviews and decision, with the stage timeline. The URL keeps the open file, so you can share or reload it.

And it lets you change things:

- **Settings**: pipeline steps, the Claude/Codex client for Start, per-execution provider model/effort choices, retry limit and merge policy. Job settings are frozen when each job starts. Add roles as Markdown files in the selected provider's agents folder.
- **Merge** and **Delete job** buttons on a job's card.
- **Start** on a backlog card runs that item. A session of the selected client running `/factory watch` (Claude) or `$factory watch` (Codex) picks it up. If none is watching, it waits for one, or Settings can launch a headless session. Automatic mode uses Claude's `auto` mode or Codex's workspace-write sandbox with automatic approval review. Bypass mode skips checks and is intended for isolated sandboxes. Logs go to `factory/jobs/headless-*.log`. If launch fails, fix the reported requirement and press Start again.
- **Backlog**: paste a feature (plain text or Markdown, with its details) or a whole list of them, drag cards to set the order (the top card is built next), and remove them.

The server listens on 127.0.0.1 only and refuses requests from other sites. Served by any plain static server instead (`python -m http.server --directory factory`, then open `/dashboard/`), the board still works but is read-only.

Codex headless runs also save `factory/jobs/headless-*.session.json`, containing the exact session ID and exit status. After stopping an interrupted run, resume that specific session with:

```bash
python factory/codex_runner.py resume factory/jobs/headless-<timestamp>.session.json
```

Resume output goes to the terminal; the session metadata continues updating. Alternatively use `$factory resume <job-id>` in a fresh Codex session after confirming the old orchestrator is stopped. Codex inherits its normal saved CLI login and permissions; the factory never silently enables bypass mode. The job's sibling worktree folder is added to the headless session's writable directories. A blocked action that cannot get approval needs human attention.

## What a job leaves behind

```
factory/jobs/<job-id>/
  job.json            state: stage, round, verdicts, timeline (written only through job.py)
  spec.md             what to build and how to check it
  build.md            what was built, evidence per criterion, deviations, round log
  round-<N>/review-security.md, review-ux.md, review-ui.md, review-code.md
  rework-<k>.md       your notes from /factory rework
  decision.md         APPROVE or ESCALATE, why, and follow-ups
  steps/<step-id>/<attempt>/  archived reports for every configured step attempt
```

Job `n` uses ports `5100 + 10n` to `5108 + 10n`: three each for the builder and the UX and UI reviewers. Job 003 uses 5130–5138.

## Customising

- **Models, effort, review rounds and merge policy:** the dashboard's Settings, or `factory/config.json`. Set explicit values in each execution's `providers` block; `default` is not a valid value. Fast mode is a setting of your whole Claude Code session (`/fast`), not of one step.
- **What each reviewer checks, and what the approver escalates:** the agent files are plain Markdown instructions; edit them.
- **Project knowledge:** add a skill under `.claude/skills/<name>/SKILL.md`. Agents read the ones whose description covers their area.

Claude Code picks up edits to agent files in a running session; no restart needed.

## Working on this repo

Python uses [YAPF](https://github.com/google/yapf), pinned in the development dependencies
with a shared style in `pyproject.toml`. Install the tooling and format changed files:

```powershell
uv sync --locked
uv run --locked yapf --in-place path/to/file.py
uv run --locked yapf --diff path/to/file.py
```

The `--diff` check exits zero when formatting is clean. To format or check the entire
repository, replace the file path with `--recursive .`. Run tests after formatting.
`AGENTS.md` and the run-plan Shared preamble require this for each session's changed
Python files; session 1.10 performs the initial full-repository formatting pass.

`claude/` is the source of the shared agent roles and workflow (`claude/skills/factory/references/workflow.md`). `codex/skills/factory/SKILL.md` is the Codex entrypoint. After changing the shared process or roles, regenerate the Codex workflow reference and seven Markdown agent files:

```bash
python tools/sync_codex.py
python tools/sync_codex.py --check
```

Use `install.py --source .` to install or refresh a project's files from this checkout.

To try the Codex integration from this checkout in an existing project, install the local source:

```powershell
python install.py --source . --target C:\path\to\your-project --provider codex
```

For an existing factory installation, preview with `--dry-run`, then use `--force` to replace differing factory code and agent files. Settings and backlog stay intact. The installer does not edit the user's Codex config or overwrite unrelated agents/skills.

### Testing the factory

Two layers, fastest first.

**Unit tests** for `job.py` and `server.py`: job state, merging (including the busy and conflict cases), deleting, settings, the API's security checks and backlog editing. No Claude involved; about 20 seconds.

```bash
python -m unittest discover -s tests
node --test tests/test_dashboard.cjs
```

**A sandbox** for the whole flow with real agents. `tools/sandbox.py` builds a throwaway repo from a small todo app (`tools/sandbox-fixture/`) with this repo's current agents, skill and `factory/`, light settings (haiku, low effort, one review round, auto merge) and the same three tiny features every time. It lives in your temp folder, never inside this repo.

```bash
python tools/sandbox.py new       # build it
python tools/sandbox.py serve     # its dashboard at http://127.0.0.1:5097/
python tools/sandbox.py run       # how to run the backlog (see below)
python tools/sandbox.py status    # jobs, backlog, branches, and whether main's tests pass
python tools/sandbox.py reset     # throw it all away and build it again
python tools/sandbox.py clean     # throw it away
```

To run the backlog, open Claude Code in the sandbox folder and type `/factory next` once per feature. Or let the script do it headless: `python tools/sandbox.py run --skip-permissions` runs `claude -p "/factory next"` for each feature. A headless session can't answer permission prompts, so that flag starts it with permission prompts bypassed: only use it on the sandbox. Stop `serve` before `reset` or `clean`, since Windows won't delete a folder a running server is using.

## License

See [LICENSE](LICENSE).
