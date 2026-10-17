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

## Phase 1: guided `init` from an empty folder (done)

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

## Phase 2: `doctor` matches `init` (done)

- Each check has `required` and, when it fails, a `fix` line. `factory doctor` prints the fix.
- Exit code 0 only when required checks pass (git, port range, project setup when run in a
  project) and at least one provider CLI is installed. Output says "Ready to run jobs" or "Not
  ready".
- New `dashboard port` check (5099). It is informational.
- Unset folder: `doctor` reports `project setup` as the failing check and says to run `factory init`.
- Not reproduced: the Codex path that changed between runs. Both runs used different PATH
  settings, so it is not a bug in the check.

## Phase 3: CLI help and output (done)

- Every command has a one-line help string, and the top-level help has a description and an
  example.
- `factory merge` does not exist; `approve` is the merge step. The README now says so.
- The provider prompt already lists claude, codex, opencode and both.
- `factory serve` prints the URL. `--open` opens it in a browser, including when a service is
  already running.

## Phase 4: README and install (done)

- Release-candidate install command, checked with `uv pip --dry-run`:
  `uv tool install --default-index https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ agent-factory-engine`.
- Quick start from any folder, with the real init plan output.
- `/factory` (Claude Code) and `$factory` (Codex) are both shown.
- agent-browser is marked optional, matching what `doctor` reports.
- Commands table with the real commands; `factory merge` removed.
- `a.txt` removed.

## Phase 5: dashboard first run (done, scoped)

Scoped to the concrete fixes from the plan. The visual redesign (pipeline graph, live
kanban) is not started and needs a design decision first.

- Empty board shows "Describe a feature to build" with a feature field and Run. Run calls the
  existing start API with the default pipeline.
- Backlog template no longer includes the placeholder "Describe your next feature" as real work.
- Overview is hidden in the sidebar when only one repository is registered. With one repo, "/"
  redirects to the board, so Overview could never be reached.
- Factory home (chosen direction): a summary row (running, needs you, queued, merged), a
  "Waiting on you" panel linking to each job that needs a decision, and the feature box always
  visible above the pipeline lanes. The shared-ui and redix starter repos use the same component
  system as `web/src/ui`, and their dashboard is a generic item tracker, so they were used as
  reference only.
- Not done: the "Set up this folder" card with prerequisite fixes. The doctor checks are only
  in the CLI; a dashboard version needs a new API endpoint.
- Verified in the browser on a fresh folder: the empty board renders with the new card. The Run
  button was not clicked, because it starts real agents.

## Verification

Each phase is verified by running the flow in a fresh empty folder under `C:\source\projects`,
then deleting it. Repo rules apply: format changed Python with yapf, run the relevant tests.
