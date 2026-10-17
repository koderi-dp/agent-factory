# Real OpenCode v2 CLI captures

Measured on Windows on 2026-10-17 with `opencode` 2.0.30 (OpenCode V2), using the
free route `opencode/deepseek-v4.1-flash-free`. Every case mirrors the engine's
own contract: the prompt arrives on stdin from a file, stdout and stderr go to
separate files (the wrapper merges stderr into `events.jsonl`, so the adapter
must tolerate both), and the cwd is a scratch directory standing in for the job
worktree. No factory runtime code changed. These observations describe this CLI
version, not a permanent API.

Base command (cwd = scratch, prompt on stdin), where `M` is
`opencode/deepseek-v4.1-flash-free`:

```text
opencode run --format json --standalone --auto --model M
```

`--standalone` gives each run a private server instead of the user's shared
background service, so a killed wrapper kills the agent with it. `--auto`
approves anything a rule did not deny, which is the only non-interactive
approval mode OpenCode offers.

| case           | prompt                                                                    | exit | what it pins                                                             |
| -------------- | ------------------------------------------------------------------------- | ---- | ------------------------------------------------------------------------ |
| `text`         | Reply with exactly: ok                                                     | 0    | a single `text` part event; a short run may emit no usage line            |
| `tool`         | Read notes.txt with a tool, then reply with exactly the number of lines... | 0    | `step_start`, `tool_use` (`read`), `step_finish` with tokens, `text`      |
| `resume`       | What number did you just reply with? (with `--session` of the `tool` run)  | 0    | `--session` continues real conversation state; every line echoes the id    |
| `shell`        | Run the shell command `git status --porcelain` with a tool, then reply...  | 0    | the tool is named `shell`; `part.state.input.command` carries the command  |
| `deny`         | Run the shell command `git push --dry-run origin main` with the shell...   | 0    | a denial is `tool_use` with `part.state.status == "error"`                 |
| `write`        | Create the file `<REPORT_DIR>/report.md` with three lines, then reply...   | 0    | writing the report outside the worktree needs no extra permission          |
| `error-model`  | Reply with exactly: ok (with `--model invalid/none`)                       | 1    | `error` line `provider.no-route`: "Model unavailable: ..."                 |
| `error-variant`| Reply with exactly: ok (with `--model M#medium`)                           | 1    | `error` line `provider.no-route`: "Variant unavailable ...: medium"        |

Findings worth keeping:

- Every line is one JSON object with a top-level `sessionID`, `type` and
  `timestamp`. Types seen: `step_start`, `text`, `tool_use`, `step_finish`,
  `error`. `step_finish` carries `part.tokens` (input/output/reasoning/cache
  read/write) and `part.cost` in USD.
- stderr was empty in every case: with `--format json` the CLI reports failures
  as JSON `error` lines on stdout. The empty `.stderr` files are evidence, not
  omissions.
- The `deny` capture uses a narrow rule
  (`shell` allowed, `git push *` denied). A blanket
  `{"action": "shell", "resource": "*", "effect": "deny"}` removes the shell
  tool entirely and the agent improvises with other tools instead of observing a
  denial, so the blanket probe was discarded.
- The two failing cases fail before any model call, so they cost nothing to
  reproduce.
- The `write` capture proves the report contract: an absolute path outside the
  worktree is writable with the default permission policy.

Redaction masks `<HOME>`, `<WORKTREE>` (the scratch cwd) and `<REPORT_DIR>` (the
directory outside the worktree). Session ids are retained, like Codex thread
ids. Each `.meta.json` holds the argv, prompt, exit code and timing.
