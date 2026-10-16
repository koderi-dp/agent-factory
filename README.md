# Agent factory

Agent factory runs a configurable pipeline of Claude Code and Codex agents against your
repository. Each job gets its own branch and worktree. The engine stores job state, streams
logs to a local dashboard, and either merges a verified result or pauses with a clear request
for you.

## Requirements

- Python 3.11+ and [uv](https://docs.astral.sh/uv/getting-started/installation/).
- Git, with a local `main` branch and at least one commit in the project repository.
- Claude Code or Codex CLI, signed in. Install the client you select in the pipeline.
- [agent-browser](https://github.com/vercel-labs/agent-browser) for browser checks:
  `npm install -g agent-browser && agent-browser install`.
- An `AGENTS.md` or `CLAUDE.md` describing project setup, development server, and validation.

## Install

Install the engine once per machine, then initialize each repository:

```powershell
uv tool install agent-factory
cd C:\path\to\your-project
factory init
```

`factory init` asks which provider to configure if needed. It writes `factory/config.json`,
`factory/backlog.md`, and the selected provider's skill wrapper. Commit those files and the
`.gitignore` update. A teammate installs the engine and can then use the repository setup.

If uv is not installed, `install.py` is an optional bootstrap. It prints uv's official
installation instructions and asks before running the official installer. It then installs
the engine; `--target` also runs `factory init` in an existing repository:

```powershell
python install.py --target C:\path\to\your-project
```

## Use it

Start work in the repository with the provider's skill command:

```text
$factory Add a dark mode toggle to settings
```

Claude Code uses `/factory <feature>`. Other commands include `factory status`,
`factory start <feature>`, `factory stop <job-id>`, `factory merge <job-id>`,
`factory rework <job-id> <note>`, and `factory delete <job-id>`. Use `factory --help` for the
available commands. Add work to `factory/backlog.md` or the dashboard backlog, then start it
with `factory start`.

## Define your pipeline

Edit the pipelines in `factory/config.json` or use Dashboard Settings. Full runs a spec,
builder, parallel reviewers, and approval. Fast uses the shorter implementation and approval
flow. Configure roles, providers, model and effort per execution, retry limits, and merge
policy. Jobs retain a snapshot of their pipeline and role instructions, so later settings
changes affect new jobs only.

## When a job needs you

Jobs pause when an agent is blocked, a review requests changes beyond the retry limit, the
approver escalates a product or risk decision, or the merge policy requires your review.
`factory status` and the dashboard show the reason and the reports to inspect. Resolve the
request in the branch, then resume or rework the job. The engine never pushes to a remote.

## The dashboard

Run `factory serve` and open the URL it prints (by default
`http://127.0.0.1:5099/`). The dashboard shows jobs across registered repositories, pipeline
steps, reports, timeline, logs and usage. It also supports backlog management, repository
and machine settings, and job actions such as stop, resume, merge, rework and delete. The
service listens on localhost.

## What a job leaves behind

The repository keeps its configuration and backlog in `factory/`. Job state, reports,
attempt logs and archived outputs live under `factory/jobs/<job-id>/`; worktrees are kept
beside the repository until merged or deleted. The exact report set depends on the pipeline.
The machine registry and service state live in the user data directory (or `FACTORY_HOME`).

## Customising

Built-in roles ship with the engine. To customize a role for one repository, run
`factory eject <role>` and edit the resulting file in `factory/roles/`. The dashboard Settings
page edits pipeline configuration; machine settings control provider executable paths,
concurrency and available ports. Ejected roles remain repository-local and can be compared
with the built-in version using `factory eject <role> --diff`.

## Working on this repo

Set up the development environment with `uv sync --locked`. The Python suite runs with
`uv run --locked pytest`. Dashboard commands and release checks are documented in
[`docs/releasing.md`](docs/releasing.md).

## License

See [LICENSE](LICENSE).
