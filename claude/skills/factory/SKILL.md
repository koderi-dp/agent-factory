---
name: factory
argument-hint: "<feature> | next | watch | approve <job-id> | rework <job-id> <note> | resume <job-id> | dashboard"
description: Run the configured agent factory pipeline in Claude Code, including feature, backlog, watch, approve, rework, resume and dashboard requests.
---

# Factory

Read [the workflow](references/workflow.md) before starting or resuming a job.
Steps come from factory/config.json; job.py freezes and drives each job's pipeline.
Delegate its roles and report progress. Do not substitute a fixed spec/build/review loop.

Every role resolves by name to `.claude/agents/<uses>.md`, including built-ins
and user-created agents. Read the task's Markdown instructions and spawn a
general-purpose agent with those instructions and the dispatch prompt. Agent
frontmatter declares type and output; it does not replace the pipeline's settings.
Use each task's explicit model and effort from the frozen dispatch plan.
Use SendMessage to follow up with a retained
builder when supported; otherwise spawn a fresh builder with prior reports and
the rework note. Record its ID with job.py set; let Python calculate transitions.
