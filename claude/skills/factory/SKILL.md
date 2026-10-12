---
name: factory
description: Run the software factory. Use when the user types /factory <feature>, /factory next, /factory approve <job-id>, /factory rework <job-id> <note>, /factory resume <job-id> or /factory dashboard. The session that runs it becomes the orchestrator of the spec → build → review → approve loop.
---

# Factory

You are the orchestrator for one job. You run the loop below, spawn every agent,
and are the only writer of your job's `job.json`. You never write code, specs,
reviews or decisions yourself. Agents do the work and each writes one file; you
move the job along and keep `job.json` current.

## The loop

```
feature → spec-writer → builder → [security, ux, ui, code] → WAIT FOR ALL
                           ▲                                      │
                           └──── any CHANGES: resume builder ◄────┤
                                                                  ▼ all PASS
                                     merge to main ◄─ APPROVE ─ approver ─ ESCALATE → needs-human
```

| Step         | Agent (`subagent_type`) | Writes                                      |
| ------------ | ----------------------- | ------------------------------------------- |
| spec         | `spec-writer`           | `spec.md`                                   |
| build        | `builder`               | `build.md` + commits on `factory/<job-id>`  |
| review       | `reviewer-security`     | `round-N/review-security.md`                |
|              | `reviewer-ux`           | `round-N/review-ux.md`                      |
|              | `reviewer-ui`           | `round-N/review-ui.md`                      |
|              | `reviewer-code`         | `round-N/review-code.md`                    |
| approve      | `approver`              | `decision.md`                               |

`MAX_RESUMES = 2`: after round 1 the builder can be resumed at most twice, so a
run has at most 3 review rounds. To change the factory, edit this file and the
agent files in `.claude/agents/`.

Every agent reads `AGENTS.md` (or `CLAUDE.md`) for the repo's setup, dev server,
validation commands and conventions, and may load the repo's own skills for the
area it works in. Nothing repo-specific lives in these files.

## Isolation

A job never touches anything outside its worktree, its job folder and its ports.
The user's running app, their database, containers, services and data belong to
them, and other jobs may be using them too. When a check needs a running
service (an API, a database, a queue), the agent starts its own copy from the
worktree, on its own ports and with its own throwaway data, the way AGENTS.md
describes running the app locally, and stops it afterwards. A check that can
only be done against the user's environment is not a job's acceptance
criterion: it becomes a follow-up for the human (see `decision.md`).

## Parallel jobs

Every job runs in its own git worktree on its own branch, so several jobs can run
at once, one per Claude session. To run three features, open three sessions in
this repo and start `/factory` in each. Your session orchestrates only the job it
started or was handed.

- Your session stays in the main checkout. Agents work in the job's worktree.
  The main checkout is the user's, and it may be dirty or on another branch.
  Only **Merge** touches it.
- Job state lives in `factory/jobs/<job-id>/` in the main checkout, never in a
  worktree. Each job has its own `job.json`, so sessions never write the same
  file.
- Each job gets its own block of ports and its own agent-browser sessions (see
  **Prompts**), so agents of different jobs never see each other's pages or
  servers.

## Files

`<repo>` is the main checkout, exactly as `git rev-parse --show-toplevel` prints
it from your session (on Windows that is `C:/...`, never `/c/...`), and `<name>`
is its folder name. `factory/jobs/` is gitignored runtime state that never lands
in a commit.

```
<repo>/factory/backlog.md                      queue for /factory next
<repo>/factory/dashboard.html                  the board; see /factory dashboard
<repo>/factory/job.py                          the only way to change a job.json
<repo>/factory/jobs/<job-id>/job.json          the job's state (you write it, the dashboard reads it)
<repo>/factory/jobs/<job-id>/spec.md
<repo>/factory/jobs/<job-id>/build.md
<repo>/factory/jobs/<job-id>/rework-<k>.md     written by you, from /factory rework
<repo>/factory/jobs/<job-id>/round-<N>/review-{security,ux,ui,code}.md
<repo>/factory/jobs/<job-id>/decision.md
<repo>/../<name>-factory/<job-id>/             the job's worktree, on factory/<job-id>
```

### job.json

```json
{ "id": "003-rate-limiting", "feature": "Rate limit the signup form",
  "branch": "factory/003-rate-limiting",
  "worktree": "C:/code/my-app-factory/003-rate-limiting",
  "stage": "review", "round": 2, "resumes": 1, "builder": "a9bdb377ace3b3adf",
  "reviews": { "security": "CHANGES", "ux": "PASS", "ui": "pending", "code": "PASS" },
  "heads": { "1": "4f1c2ab", "2": "9e0d7c1" },
  "created": "2026-10-12T09:14:03Z", "updated": "2026-10-12T10:02:41Z",
  "history": [
    { "stage": "spec", "at": "2026-10-12T09:14:03Z" },
    { "stage": "build", "at": "2026-10-12T09:21:40Z" },
    { "stage": "review", "round": 1, "at": "2026-10-12T09:48:12Z" },
    { "stage": "build", "at": "2026-10-12T09:55:30Z" },
    { "stage": "review", "round": 2, "at": "2026-10-12T10:01:02Z" } ] }
```

- `stage` is one of `spec`, `build`, `review`, `approve`, `merged`,
  `needs-human`. Worktree setup counts as `spec`.
- `round` is the current review round (0 until the first round starts).
- `resumes` is how many times the builder has been resumed since the last fresh
  build. It starts at 0 and goes back to 0 on `/factory rework`.
- `builder` is the agent id the Agent tool returned when the current builder
  was spawned. It is absent until the first build starts. It only works in the
  session that spawned the builder.
- `reviews` holds the current round only. Each value is `pending`, `PASS` or
  `CHANGES`. Earlier rounds live in their `round-<N>/` folders, which the
  dashboard reads directly.
- `heads` maps each review round to the short commit the reviewers saw
  (`git -C <worktree> rev-parse --short HEAD` when the round starts).
- `created`, `updated` and every `at` are UTC. `history` gets one entry each
  time `stage` changes, with `round` on review entries.
- `worktree` is always an absolute path in the same form as `<repo>`.

Never write `job.json` by hand. Change it only with `factory/job.py`, which
keeps `history` and `updated`, checks every value, and replaces the file in one
step so the dashboard never reads half of it. Run it from `<repo>`:

```bash
python factory/job.py new <job-id> --feature "<feature>" --worktree <worktree>
python factory/job.py set <job-id> stage=review round=2 reviews=pending heads.2=9e0d7c1
python factory/job.py set <job-id> reviews.ux=PASS
python factory/job.py show <job-id>
```

`set` takes `stage`, `round`, `resumes`, `builder`, `feature`,
`reviews.<lens>`, `heads.<round>`, and `reviews=pending` to reset all four.
Below, "set" means one `job.py set` call with every change of that step.

### Prompts

Every agent prompt starts with the job's absolute paths:
`Job folder: <repo>/factory/jobs/<job-id>/. Worktree: <worktree>. Branch: factory/<job-id>.`
Below, `<paths>` stands for that sentence. Every prompt ends with
` Write your file: it is the factory's input, and your final message is not read.`
so an agent never answers in its message instead of its file.

Agents that run the app get a range of three ports: the first is for the dev
server, the other two for anything else the page needs (an API, a database).
With `n` the job number (`003` → 3) and `B = 5100 + 10n`, the builder gets
`B`–`B+2`, `reviewer-ux` gets `B+3`–`B+5` and `reviewer-ui` gets `B+6`–`B+8`,
appended as ` Ports: <first>-<last>.` (job 003's builder: ` Ports: 5130-5132.`).
The spec-writer is told the builder's range, so the acceptance criteria can
name real ports.

## /factory <feature>

1. **Create the job.** `mkdir -p factory/jobs`, then pick the next 3-digit number
   after the highest numbered folder in `factory/jobs/` (`001` if there are
   none) and a 2–4 word kebab-case slug of the feature: `004-blog-reading-time`.
   Claim it with `mkdir factory/jobs/<job-id>` (no `-p`). If that fails because
   another session took the number, pick the next number and try again.
   The worktree path is `<parent of repo>/<name>-factory/<job-id>`. Create
   `job.json` straight away with `job.py new`.
2. **Worktree.** Create the branch and its worktree from `main`:
   ```bash
   git worktree add -b factory/<job-id> <worktree> main
   ```
   Then, in the worktree, run the setup AGENTS.md gives for a fresh checkout
   (installing dependencies, for example). If any of it fails, go to
   **Needs human** with what failed.
3. **Spec.** Spawn `spec-writer` with the prompt
   `<paths> Builder ports: <B>-<B+2>. Feature: <feature, verbatim>`.
   Wait for it to finish, then check that `spec.md` exists and starts with `# `.
   If it doesn't, go to **Needs human**.
4. **Build.** Set `stage=build`. Spawn `builder` with the prompt
   `<paths> Build round 1. Ports: <B>-<B+2>.` The Agent tool returns the
   builder's agent id: set `builder=<id>` straight away, since resuming the
   builder later needs it. Wait for it to finish, then go to **Build check**.

### Build check

Read the first line of `build.md`.

- `STATUS: READY`: check that the branch has commits
  (`git -C <worktree> log --oneline main..HEAD` prints something) and the
  worktree is clean (`git -C <worktree> status --porcelain` prints nothing).
  If both hold, start the next review round. If not, treat it as BLOCKED and
  say which check failed.
- `STATUS: BLOCKED`: go to **Needs human**.
- No file or any other first line: go to **Needs human**.

### Review round N

1. Set `stage=review round=N reviews=pending heads.N=<short HEAD>`, with the
   worktree's `git -C <worktree> rev-parse --short HEAD`. Create
   `factory/jobs/<job-id>/round-<N>/`.
2. Spawn all four reviewers **in one message**, so they run in parallel. Each
   gets the prompt `<paths> Review round N.`, plus, from round 2,
   ` Round N-1 reviewed <heads.N-1>.` so it can diff what changed since, plus its
   ports for `reviewer-ux` and `reviewer-ui`.
3. **WAIT FOR ALL.** Each time a reviewer finishes, read the first line of its
   file (`VERDICT: PASS` or `VERDICT: CHANGES`) and set
   `reviews.<lens>=<verdict>` straight away, so the dashboard chips fill in as
   they land. The round is done only when all four `review-*.md` files exist in
   `round-<N>/`.
   Never resume the builder mid-round. If a reviewer finishes without writing
   its file, or writes a first line that isn't a verdict, spawn it once more
   with the same prompt. If the second attempt fails too, go to **Needs human**.
4. Decide:
   - **All PASS**: go to **Approve**.
   - **Any CHANGES, `resumes` < `MAX_RESUMES`**: set `stage=build` and
     `resumes=<resumes + 1>`, then resume the same builder with `SendMessage`
     to the agent id in `job.json`'s `builder`:
     `Round N reviews are in <repo>/factory/jobs/<job-id>/round-<N>/. Address every review with VERDICT: CHANGES, commit, and update build.md for round N+1.`
     plus the usual closing sentence.
     If the builder can't be resumed (another session spawned it), spawn a fresh
     one as in **/factory resume** instead. Wait for it to finish, then go to
     **Build check**.
   - **Any CHANGES, no resumes left**: write `decision.md` yourself, reading
     `ESCALATE`, then a blank line, then
     `Review rounds exhausted. Outstanding CHANGES in round-<N>/ from: <reviewers>.`
     Go to **Needs human**.

### Approve

Set `stage=approve`. Spawn `approver` with the prompt `<paths> Final round: N.`
When it finishes, read the first line of `decision.md`:

- `APPROVE`: go to **Merge**.
- `ESCALATE`: go to **Needs human**.
- No file or any other first line: go to **Needs human**.

### Merge

The merge happens in the main checkout, so it must be clean and on `main`:
`git status --porcelain --untracked-files=no -- . ':!factory/backlog.md'`
prints nothing and `git branch --show-current` prints `main`. Untracked files
don't count: if the merge would overwrite one, git refuses and the failure path
below handles it. `factory/backlog.md` is exempt: every `/factory next` edits
it, and job branches never touch it, so a dirty backlog cannot conflict with the
merge. If not, set `stage=needs-human` and tell the user the job is approved
but the main checkout is busy; they run `/factory approve <job-id>` once it is
clean. Otherwise:

```bash
git merge --no-ff factory/<job-id> -m "Merge factory/<job-id>"
git worktree remove <worktree>
git branch -d factory/<job-id>
```

Set `stage=merged`. If the merge fails, run `git merge --abort` (when a merge
is in progress), set `stage=needs-human` and tell the user why. If removing the
worktree fails, the job is still merged: tell the user what `git worktree remove`
printed. Never push.

### Needs human

Set `stage=needs-human`. The branch and its worktree stay for the human to
inspect. Tell the user which job is waiting, give the worktree path, and quote
`decision.md` (or the BLOCKED reason from `build.md`, or what failed).

## /factory next

Read `factory/backlog.md` and take the first `- [ ]` item. Claim it with one
Edit that replaces its exact line with `- [x] <item> → claimed`. If the Edit
fails because the line changed, another session claimed it: re-read the backlog
and try the next `- [ ]` item. If none is left, say so and stop.

Run `/factory <item>`, and once step 1 has given it a job id, replace `claimed`
on its backlog line with the job id.

## /factory approve <job-id>

The job must be in `needs-human`. Run **Merge** with that job id.

## /factory rework <job-id> <note>

The job must be in `needs-human`.

1. Write the note, verbatim, to `factory/jobs/<job-id>/rework-<k>.md`, where `k`
   is the next unused number.
2. Set `stage=build resumes=0`. A rework run gets a fresh `MAX_RESUMES`.
3. Spawn a fresh `builder` with the prompt
   `<paths> Rework: read rework-<k>.md and address it, then update build.md for round N+1. Ports: <B>-<B+2>.`
   (`N` is the job's last round) and set `builder=<id>`.
4. Continue from **Build check**, exactly like a new job.

## /factory resume <job-id>

For a job left in `spec`, `build`, `review` or `approve` because the session
orchestrating it ended. Agent ids don't survive their session, so every agent
is spawned fresh. First ask the user to confirm no other session is still
running this job; two orchestrators on one job corrupt it.

Your session becomes the job's orchestrator. Pick up from the stage in
`job.json`:

- `spec`: if the worktree doesn't exist, run step 2 of **/factory <feature>**.
  If `spec.md` is missing or doesn't start with `# `, run step 3. Then step 4.
- `build`: spawn a fresh `builder` with
  `<paths> Resume: a previous builder of round <N+1> was interrupted. Read build.md if present, every round folder and rework file, and git log main..HEAD, then finish the round and update build.md for round <N+1>. Ports: <B>-<B+2>.`
  (`N` is `round`, 0 before the first review) and set `builder=<id>`.
  `resumes` stays as it is. Then **Build check**.
- `review`: spawn only the reviewers whose file is missing from `round-<N>/`,
  as in **Review round N** step 2, take the verdicts of the files already
  there, and continue from step 3.
- `approve`: run **Approve** again.

## /factory dashboard

The board is `factory/dashboard.html`, served from `factory/` by a plain static
server that lists directories:
`python -m http.server 5099 --bind 127.0.0.1 --directory factory`. Start it in
the background if nothing answers on that port yet, and give the user
`http://127.0.0.1:5099/dashboard.html`. Every job is on it, whichever session
runs it.

## Reporting

Keep the user posted with one short line per transition, for example
`003-rate-limiting: round 2, security CHANGES, ux/ui/code PASS → resuming builder`.
When the job ends, say whether it merged or needs a human, and why, and list
the `## Follow-ups` from `decision.md` when it has any.
