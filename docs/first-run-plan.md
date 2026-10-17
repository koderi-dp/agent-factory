# First-run experience plan

Goal: a user with an empty folder runs `factory init` (no arguments) and reaches a working
state with no manual git or doc setup. Status is updated as each phase lands.

Status legend: `not started`, `in progress`, `done`.

## Decisions

- **Git:** if the folder is not a repo, create one on `main` with a `.gitignore` and a first
  commit. If it is already a repo, use its current branch as the base and tell the user which
  branch it is. Never rename an existing branch.
- **Empty folder:** `init` succeeds, and the starter doc says the stack is decided by the first
  feature.
- **Mode:** `init` asks once, shows the full list of what it will do, then does it. No flags are
  needed. Without a terminal it uses the defaults and says so.

## Phase 1: guided `init` from an empty folder (in progress: core done)

Done:

1. **Detection pass.** `scaffold.inspect` reads git, branch, commits, doc and identity in one
   place and raises one clear error for states it cannot repair (v1 install, detached HEAD,
   folder inside another repo, missing git identity).
2. **Provider first, then one plan.** `factory init` asks for the provider, prints the plan
   (the same code path as a dry run), and asks "Continue? [Y/n]" once.
3. **Repo on `main` when missing.** `git init -b main`, then a first commit. The commit holds
   only the starter doc. Factory files stay uncommitted, as before. Identity is checked before
   anything is written, and the error gives the two `git config` commands.
4. **Configurable base branch.** `baseBranch` in `factory/config.json`, set from the repo's
   current branch at init. Used by job worktree creation (`runtime.py`), the merge checks and
   message (`gitops.py`), and legacy merges (`legacy.py`). Jobs snapshot it through the frozen
   config. Existing configs without the key default to `main`.
5. **Starter doc.** `AGENTS.md` (Codex, OpenCode, both) or `CLAUDE.md` (Claude Code), from
   `templates/AGENTS.starter.md`. `npm` scripts fill in `dev`, `test`, `build`, `lint` when a
   `package.json` exists. Everything else stays `TODO`.
6. **No terminal.** Scripted runs use the defaults. The Windows NUL device reports as a
   terminal, so EOF on input also falls back to the default.

Verified: `factory init` with no arguments in an empty folder ends with a repo on `main`, one
commit, the factory files, and a clean rerun. Tests: init, config and API suites pass (143);
the full suite passed before the last two small fixes, which the targeted tests cover.

Not done yet:

- `factory start` still says "run `factory init` first" when the repo is missing config. It
  should name the missing piece (step 7 is only partly done: init's own errors are clear).
- No test yet for a job worktree or merge on a non-`main` base branch. The code path is
  covered by the existing merge tests on `main`.
- Existing repos on another branch will now get a `baseBranch` at init. Repos set up before
  this change keep `main` and are unaffected.

## Phase 2: `doctor` matches `init` (not started)

- Report the same items as `init`, each with a fix command. Exit non-zero when something is
  missing.
- Add the dashboard port (5099) to the port check. Today `ports` checks 5100–5999 only
  (`scaffold.py:273`).
- Show one stable path per CLI. Codex currently appears under two different paths between runs.

## Phase 3: CLI help and output (not started)

- One-line descriptions for every command in `factory --help`.
- Resolve `merge`: the README documents `factory merge <job-id>`, but the CLI has no such
  command (it has `approve`). Either add it or fix the docs.
- Document `eject`, `doctor`, `dashboard`, `config` in the README.
- Provider prompt lists all three providers (claude, codex, opencode). It currently lists two.
- `factory serve` prints the dashboard URL, and `--open` opens it.

## Phase 4: README and install (not started)

- "Try the release candidate" block with the working command:
  `pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ agent-factory-engine`.
  The plain `pip install -i https://test.pypi.org/simple/` fails because TestPyPI has a
  placeholder `fastapi`.
- Quickstart from an empty folder: install, `factory init`, `factory start "build a todo app"`,
  with expected output.
- Say where to type `$factory` (Codex) vs `/factory` (Claude Code).
- Say whether `agent-browser` is required, and what `doctor` reports when it is missing.
- Remove the stray `a.txt` (contains only `x`) from the repo root.

## Phase 5: dashboard first run (not started)

- Empty state: "Set up this folder" card listing missing prerequisites with fix buttons. Once
  set up, show a "Describe a feature → Run" prompt box.
- Backlog: drop the placeholder "Describe your next feature" item and make Run the main action.
- Repo settings: pipeline as a visual flow (spec → build → reviewers → approve), with the JSON
  behind "Advanced".
- Navigation: fix the double highlight, and stop redirecting Overview to Board.
- Needs a design pass before implementation.

## Verification

Each phase is verified by running the flow in a fresh empty folder under `C:\source\projects`,
then deleting it. Repo rules apply: format changed Python with yapf, run the relevant tests.
