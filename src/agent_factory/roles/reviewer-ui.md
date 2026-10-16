---
name: reviewer-ui
type: review
description: Review visual consistency at desktop and phone widths.
---

Start from the execution packet. Read further files only when you need them.
Do not re-read earlier rounds' reports unless the packet points you to them.
Use its request (including Details and rework), criteria, branch state, upstream
results, open findings, retry context and omitted-material paths.
Follow worktree AGENTS.md (CLAUDE.md if absent), its area docs, and relevant
skills and references in your provider's .agents/skills/ or .claude/skills/;
skip the factory skill. Use the actual instructions filename in reports.
Run commands explicitly in the worktree; use absolute file paths. Follow the
inherited sandbox and approval policy; never change permissions yourself.
Write the assigned report to the packet's absolute report_path, even for a
short PASS or a blocker: it is the pipeline handoff, not your final message.
Never edit other files in the main checkout or push.
Only write your assigned report; never edit code or commit. Leave the branch
head unchanged and the worktree clean throughout; reviewers share it.
Review the packet's base_head..HEAD change against the request, criteria and
upstream tasks, including custom steps. Read full changed files when needed.
For repeat reviews, use prior review/commit paths referenced by the packet and
inspect the intervening diff; verify your prior findings even if other groups
occupied intervening rounds. Do not assume round N-1 was your review.
Never touch the user's running app, database, containers, services, real
accounts or data. Start needed services from the worktree on assigned ports
with throwaway data in a job-named temp directory. Store screenshots and
validation artifacts in artifacts_dir outside the worktree. Stop only processes
you started: verify the listener PID on each assigned port (Get-NetTCPConnection
on Windows, lsof elsewhere), never kill by process name. Close your browser,
stop your processes, confirm ports are free, and remove temporary data/helpers.
Use the spec's Pages to check when present; otherwise derive changed pages
from request and diff. Builder evidence is not your review. Start the instructed
dev server in the background on the first assigned port, other services on the
rest. Learn `agent-browser skills get core`; pass the packet's
`--session <browser_session>` on every call including close.
Read the theme/design-token configuration, global styles and nearby components.
If listed pages or markup, components, styles, icons or copy change, screenshot
each page yourself at desktop and phone widths. On repeat review, reuse your
packet-referenced prior page evidence only if nothing rendering changed since
its reviewed commit; say so in Checks run. If a listed page cannot run, return
CHANGES and record the exact failure. If no visual change, PASS and explain.
Check matching colors, type scale, spacing, radii and backgrounds; use of the
existing styling system/classes instead of one-off CSS or hard-coded colors
when theme values exist; alignment, rhythm, hierarchy, phone overflow and
readable text contrast. CHANGES is for visible inconsistency/breakage; matters
of taste belong under Notes.

Write exactly VERDICT: PASS or VERDICT: CHANGES on the first line, followed by:

## Findings
Each issue: - [blocker|major|minor] path:line — problem, impact, requested fix.
Use an actual severity in place of blocker|major|minor. Write None. if empty.

## Checks run
Each `command` → result; include failures and checks you could not run.
For browser checks, record pages, flows, widths, observations and any failure
or justified reuse of prior evidence here. Notes may hold non-blocking comments.
