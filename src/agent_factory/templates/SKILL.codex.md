---
name: factory
description: Start and manage agent-factory jobs in this repository.
---

Use the installed `agent-factory` command.
If it is missing, tell the user to run `uv tool install agent-factory-engine` and stop.

Map the user's request to one command:
- A feature description: `agent-factory start "<feature>"`.
- next: `agent-factory next`.
- status: `agent-factory status` (optionally a job id).
- approve: `agent-factory approve <id>`.
- rework: `agent-factory rework <id> "<note>"`.
- stop: `agent-factory stop <id>`.
- resume: `agent-factory resume <id>`.
- dashboard: `agent-factory dashboard`.

Pass `--pipeline fast|full` when the user chooses a pipeline.
Run the command and relay its job id, dashboard link, or error.
The service runs the job; it keeps running when this session closes.
The dashboard Start button also runs jobs directly.
Do not orchestrate steps or launch agents yourself.
