# Benchmarks

## v1 baseline

Two sandbox passes completed all three features using haiku/low. Pass 2 is captured below.
The first pass's third job completed, but its sandbox was reset before its detailed timings
were saved; its results are shown as unknown rather than estimated.

### Pass 1 (captured jobs)

| Job | Feature | Wall time | Stage times | Agent runs | Tokens | Cost (USD) |
|---|---|---:|---|---:|---:|---:|
| 001-add-button-text | Change the Add button's text to "Add todo" | 341.0s | spec: 39.0s, build: 189.0s, review: 76.0s, approve: 34.0s | 7 | unknown | unknown |
| 002-title-open-count | Show the number of open todos in the page title, like "Todos (3)" | 203.0s | spec: 43.0s, build: 72.0s, review: 57.0s, approve: 28.0s | 7 | unknown | unknown |
| (third job; detail not retained) | Ignore a new todo whose title is already in the list | unknown | unknown | unknown | unknown | unknown |

### Latest capture

| Job | Feature | Wall time | Stage times | Agent runs | Tokens | Cost (USD) |
|---|---|---:|---|---:|---:|---:|
| 001-add-todo-button | Change the Add button's text to "Add todo" | 293.0s | spec: 35.0s, build: 172.0s, review: 60.0s, approve: 23.0s | 7 | unknown | unknown |
| 002-open-count-title | Show the number of open todos in the page title, like "Todos (3)" | 355.0s | spec: 43.0s, build: 191.0s, review: 85.0s, approve: 33.0s | 7 | unknown | unknown |
| 003-dedupe-titles | Ignore a new todo whose title is already in the list | 263.0s | spec: 45.0s, build: 84.0s, review: 91.0s, approve: 40.0s | 7 | unknown | unknown |

## Session 2.3 methodology

The v2 captures below read SQLite attempts directly in read-only mode. Each suite has
two fresh todo repos with the same three backlog features, automatic merge, bypass
permissions, and the packaged roles from sessions 2.1/2.2. All six jobs are queued before
scheduling. Full and Fast use separate suites and non-overlapping port ranges. The real
Codex adapter retains project instructions and disables user configuration/MCPs with
`--ignore-user-config`. Claude comparisons remain deferred to session 1.11.

The low-cost comparison uses `gpt-6-luna` / low. The production confirmation uses
`gpt-6.1-sol` / medium, the template's default Codex profile. Each profile gets one Fast
and one Full suite; no repeated paid runs to improve the numbers. Suites run concurrently
on this Windows host, alongside verification, so wall times include scheduling and host
contention. This is a small descriptive sample, not an isolated latency experiment.
The v1 baseline used haiku/low and earlier roles: timing differences across versions also
include provider/model and role changes; v1 token savings cannot be calculated because
its usage was not captured.

Reproduce each pair (use fresh paths to retain earlier evidence):

```powershell
uv run --locked python tools/sandbox.py new --dir "$env:TEMP/factory-bench-23-luna-full"
uv run --locked python tools/sandbox.py new --dir "$env:TEMP/factory-bench-23-luna-fast"
uv run --locked python tools/sandbox.py run --real --keep-going --pipeline full --ports 34000-34899 --dir "$env:TEMP/factory-bench-23-luna-full"
uv run --locked python tools/sandbox.py run --real --keep-going --pipeline fast --ports 35000-35899 --dir "$env:TEMP/factory-bench-23-luna-fast"
uv run --locked python tools/sandbox.py bench --dir "$env:TEMP/factory-bench-23-luna-full" --compare "$env:TEMP/factory-bench-23-luna-fast"
```

For production, create `factory-bench-23-sol-full` and `factory-bench-23-sol-fast` with
`new --model gpt-6.1-sol --effort medium`, run with port ranges 36000-36899 and
37000-37899 respectively, and pass those paths to `bench`. The benchmark appends a dated
capture without replacing this baseline or earlier results. Reports, event logs, packets,
and databases remain in the source suites printed with each capture. Use `bench --dir`
on an individual v2 repo or legacy v1 repo as well.

The initial Luna suites used the runner's default fail-fast behavior. Fast finished five
jobs before one escalation. Full stopped its other jobs when a builder reported BLOCKED
because browser interaction could not be verified. Full was then continued once with
`run --real --resume --keep-going --pipeline full` on its original directory, preserving
every stopped/resumed attempt. Its wall times include that interruption and inspection
gap. One resume failed because Codex's saved rollout file was empty. Missing usage from
interrupted attempts remains unknown, so the Luna capture cannot confirm the token target.
`--keep-going` now drains independent jobs without overriding blockers, and writes
`all_merged: false` plus a failure list when intervention remains; its zero exit status
means collection finished, not that all jobs passed. The default runner still fails fast.

Two Luna reviewers emitted `turn.completed` and final reports but their CLI processes did
not exit. After 291/277 seconds without further log output, their owned wrappers received
the normal `stop` flag. The affected job is paused, not counted as a successful Full run.
The production Full suite also needed one continuation with `--resume --keep-going` after
its first escalation stopped independent jobs. Completed pairs before that interruption
remain valid token observations; continued jobs retain all attempts and interruption time.

## Session 2.3 results

| Profile | Pipeline | Merged | Needs human | Paused | Attempts |
|---|---|---:|---:|---:|---:|
| Luna / low | Fast | 5/6 | 1 | 0 | 12 |
| Luna / low | Full | 2/6 | 2 | 2 | 41 |
| Sol / medium | Fast | 6/6 | 0 | 0 | 12 |
| Sol / medium | Full | 4/6 | 2 | 0 | 48 |

The single Luna pair with successful jobs and complete usage (repo 1, duplicate titles)
used 28.4% of Full's tokens. It is insufficient to establish the target across features.
Production's two successful pairs with complete usage are the button feature in repos
1 and 2: Fast used 53.2% and 34.1% of Full's tokens, respectively, or **43.6% pooled**
(1,099,059 / 2,518,649 tokens). Thus the expected <=40% ratio is **not confirmed** by
this sample. The other production Full jobs include interrupted attempts with missing
usage or an escalation; they are not silently dropped into a complete-suite token total.
Codex did not report cost, so every v2 cost remains unknown.

Production's matched successful jobs have Fast/Full wall ratios of 59.4% and 58.5% for
the button feature, 51.3% for repo 2's title feature, and 43.6% for repo 2's duplicate
feature. The latter two Full jobs were continued after the interruption. Luna's successful
duplicate pair is 8.6%, also including Full's interruption. These are observations of this
host/run, not isolated engine speedups. Compared with the v1 pass-2 timings (293/355/263s),
Luna Fast's merged features took 32.2-83.1s and production Fast's took 265.0-332.7s.
The provider/model and roles differ from v1, so no causal latency claim follows.

Fast uses two calls per completed job versus the baseline's seven. A Full job without
interruption still uses seven; stopped/resumed attempts increased the measured Full counts.
The <2s/execution engine-overhead expectation is not established: the specified metric
includes queue/inspection gaps and subtracts overlapping process time, and positive Fast
overheads exceed that threshold in this loaded run. Startup and validation observations
are retained per attempt below rather than replacing missing timings with estimates.

Failure evidence remains in the captured suites. Luna Fast's title gate requested browser
evidence; Luna Full had a browser-interaction blocker, review CHANGES, an empty-rollout
resume failure, and the two non-exiting reviewer processes described above. Production
Full's two escalations requested confirmation of spec assumptions explicitly labeled
product decisions (zero-count title and duplicate-title semantics); their technical checks
passed. These outcomes were preserved without changing role policies or forcing approval.
No Claude calls were made. This capture does not measure exploration/read or repeated-test
fractions and does not establish the Phase 5 trigger.

## v2 benchmark — 2026-10-16T12:28:17+02:00

Sources: `C:\Users\Administrator\AppData\Local\Temp\factory-bench-23-luna-full`, `C:\Users\Administrator\AppData\Local\Temp\factory-bench-23-luna-fast`

### todo-app-1 / full / 001-change-the-add-button-s-text-to-add-todo

State: paused; profiles: [('codex', 'gpt-6-luna', 'low')].

| Job | Feature | Wall time | Stage times | Agent runs | Tokens | Cost (USD) |
|---|---|---:|---|---:|---:|---:|
| 001-change-the-add-button-s-text-to-add-todo | Change the Add button's text to "Add todo" | unknown | spec: 20.5s, build: 132.4s, review: 340.0s | 8 | unknown | unknown |

| Input | Cached input | Output | Process | Engine overhead | Startup per attempt | Validation per attempt | Packet bytes |
|---:|---:|---:|---:|---:|---|---|---:|
| unknown | unknown | unknown | 492.9s | unknown | 4.4s, 1.8s, 1.2s, 110.4s, 78.5s, 4.3s, 2.8s, 1.5s | 0.2s, 3.2s, 1.5s, 0.0s, 0.0s, 1.0s, 2.1s, 0.6s | 26776 |

### todo-app-1 / full / 002-show-the-number-of-open-todos-in-the-page-title-like-todos-3

State: merged; profiles: [('codex', 'gpt-6-luna', 'low')].

| Job | Feature | Wall time | Stage times | Agent runs | Tokens | Cost (USD) |
|---|---|---:|---|---:|---:|---:|
| 002-show-the-number-of-open-todos-in-the-page-title-like-todos-3 | Show the number of open todos in the page title, like "Todos (3)" | 631.5s | spec: 19.2s, build: 68.4s, review: 643.5s, approve: 15.3s | 10 | 3025155 | unknown |

| Input | Cached input | Output | Process | Engine overhead | Startup per attempt | Validation per attempt | Packet bytes |
|---:|---:|---:|---:|---:|---|---|---:|
| 2997554 | 2694144 | 27601 | 746.4s | -114.9s | 3.3s, 1.2s, 1.6s, 3.4s, 8.1s, 0.8s, 0.9s, 2.1s, 3.0s, 1.8s | 0.6s, 1.6s, 1.2s, 0.0s, 0.0s, 0.9s, 0.3s, 1.1s, 1.5s, 0.6s | 43936 |

### todo-app-1 / full / 003-ignore-a-new-todo-whose-title-is-already-in-the-list

State: merged; profiles: [('codex', 'gpt-6-luna', 'low')].

| Job | Feature | Wall time | Stage times | Agent runs | Tokens | Cost (USD) |
|---|---|---:|---|---:|---:|---:|
| 003-ignore-a-new-todo-whose-title-is-already-in-the-list | Ignore a new todo whose title is already in the list | 587.5s | spec: 28.5s, build: 35.1s, review: 77.6s, approve: 21.3s | 7 | 633843 | unknown |

| Input | Cached input | Output | Process | Engine overhead | Startup per attempt | Validation per attempt | Packet bytes |
|---:|---:|---:|---:|---:|---|---|---:|
| 626048 | 489728 | 7795 | 162.6s | 424.9s | 1.8s, 1.5s, 2.4s, 1.3s, 1.8s, 0.9s, 2.2s | 0.7s, 0.6s, 1.2s, 6.6s, 1.7s, 2.5s, 1.0s | 38401 |

### todo-app-2 / full / 001-change-the-add-button-s-text-to-add-todo

State: needs_human; profiles: [('codex', 'gpt-6-luna', 'low')].

| Job | Feature | Wall time | Stage times | Agent runs | Tokens | Cost (USD) |
|---|---|---:|---|---:|---:|---:|
| 001-change-the-add-button-s-text-to-add-todo | Change the Add button's text to "Add todo" | 491.8s | spec: 15.2s, build: 100.9s, review: 440.2s | 8 | unknown | unknown |

| Input | Cached input | Output | Process | Engine overhead | Startup per attempt | Validation per attempt | Packet bytes |
|---:|---:|---:|---:|---:|---|---|---:|
| unknown | unknown | unknown | 556.2s | -64.5s | 4.0s, 1.1s, 7.9s, 2.2s, 4.0s, 87.2s, 3.6s, 2.3s | 0.3s, 9.3s, 2.3s, 3.3s, 0.0s, 49.7s, 5.6s, 2.3s | 22836 |

### todo-app-2 / full / 002-show-the-number-of-open-todos-in-the-page-title-like-todos-3

State: paused; profiles: [('codex', 'gpt-6-luna', 'low')].

| Job | Feature | Wall time | Stage times | Agent runs | Tokens | Cost (USD) |
|---|---|---:|---|---:|---:|---:|
| 002-show-the-number-of-open-todos-in-the-page-title-like-todos-3 | Show the number of open todos in the page title, like "Todos (3)" | unknown | spec: 21.0s, build: 105.5s, review: 821.3s | 6 | 1515909 | unknown |

| Input | Cached input | Output | Process | Engine overhead | Startup per attempt | Validation per attempt | Packet bytes |
|---:|---:|---:|---:|---:|---|---|---:|
| 1501202 | 1346944 | 14707 | 947.8s | unknown | 2.6s, 1.3s, 0.9s, 3.0s, 1.3s, 1.0s | 0.5s, 9.7s, 0.7s, 0.7s, 0.0s, 0.0s | 26453 |

### todo-app-2 / full / 003-ignore-a-new-todo-whose-title-is-already-in-the-list

State: needs_human; profiles: [('codex', 'gpt-6-luna', 'low')].

| Job | Feature | Wall time | Stage times | Agent runs | Tokens | Cost (USD) |
|---|---|---:|---|---:|---:|---:|
| 003-ignore-a-new-todo-whose-title-is-already-in-the-list | Ignore a new todo whose title is already in the list | 232.1s | spec: 28.3s, build: 196.3s | 2 | 534763 | unknown |

| Input | Cached input | Output | Process | Engine overhead | Startup per attempt | Validation per attempt | Packet bytes |
|---:|---:|---:|---:|---:|---|---|---:|
| 529465 | 443520 | 5298 | 224.6s | 7.5s | 1.1s, 1.0s | 0.6s, 1.7s | 4660 |

### todo-app-1 / fast / 001-change-the-add-button-s-text-to-add-todo

State: merged; profiles: [('codex', 'gpt-6-luna', 'low')].

| Job | Feature | Wall time | Stage times | Agent runs | Tokens | Cost (USD) |
|---|---|---:|---|---:|---:|---:|
| 001-change-the-add-button-s-text-to-add-todo | Change the Add button's text to "Add todo" | 53.0s | build: 24.5s, approve: 20.5s | 2 | 178624 | unknown |

| Input | Cached input | Output | Process | Engine overhead | Startup per attempt | Validation per attempt | Packet bytes |
|---:|---:|---:|---:|---:|---|---|---:|
| 176697 | 120192 | 1927 | 45.0s | 8.0s | 4.4s, 1.0s | 1.4s, 2.0s | 4666 |

### todo-app-1 / fast / 002-show-the-number-of-open-todos-in-the-page-title-like-todos-3

State: needs_human; profiles: [('codex', 'gpt-6-luna', 'low')].

| Job | Feature | Wall time | Stage times | Agent runs | Tokens | Cost (USD) |
|---|---|---:|---|---:|---:|---:|
| 002-show-the-number-of-open-todos-in-the-page-title-like-todos-3 | Show the number of open todos in the page title, like "Todos (3)" | 239.4s | build: 172.8s, approve: 53.1s | 2 | 450947 | unknown |

| Input | Cached input | Output | Process | Engine overhead | Startup per attempt | Validation per attempt | Packet bytes |
|---:|---:|---:|---:|---:|---|---|---:|
| 445706 | 348800 | 5241 | 226.0s | 13.5s | 3.4s, 4.4s | 2.1s, 3.8s | 5408 |

### todo-app-1 / fast / 003-ignore-a-new-todo-whose-title-is-already-in-the-list

State: merged; profiles: [('codex', 'gpt-6-luna', 'low')].

| Job | Feature | Wall time | Stage times | Agent runs | Tokens | Cost (USD) |
|---|---|---:|---|---:|---:|---:|
| 003-ignore-a-new-todo-whose-title-is-already-in-the-list | Ignore a new todo whose title is already in the list | 50.3s | build: 23.3s, approve: 17.5s | 2 | 179865 | unknown |

| Input | Cached input | Output | Process | Engine overhead | Startup per attempt | Validation per attempt | Packet bytes |
|---:|---:|---:|---:|---:|---|---|---:|
| 177755 | 138752 | 2110 | 40.8s | 9.6s | 1.8s, 0.9s | 1.6s, 0.6s | 5631 |

### todo-app-2 / fast / 001-change-the-add-button-s-text-to-add-todo

State: merged; profiles: [('codex', 'gpt-6-luna', 'low')].

| Job | Feature | Wall time | Stage times | Agent runs | Tokens | Cost (USD) |
|---|---|---:|---|---:|---:|---:|
| 001-change-the-add-button-s-text-to-add-todo | Change the Add button's text to "Add todo" | 32.2s | build: 15.2s, approve: 12.3s | 2 | 136792 | unknown |

| Input | Cached input | Output | Process | Engine overhead | Startup per attempt | Validation per attempt | Packet bytes |
|---:|---:|---:|---:|---:|---|---|---:|
| 135398 | 83200 | 1394 | 27.5s | 4.7s | 4.0s, 1.0s | 0.4s, 0.5s | 4809 |

### todo-app-2 / fast / 002-show-the-number-of-open-todos-in-the-page-title-like-todos-3

State: merged; profiles: [('codex', 'gpt-6-luna', 'low')].

| Job | Feature | Wall time | Stage times | Agent runs | Tokens | Cost (USD) |
|---|---|---:|---|---:|---:|---:|
| 002-show-the-number-of-open-todos-in-the-page-title-like-todos-3 | Show the number of open todos in the page title, like "Todos (3)" | 83.1s | build: 56.6s, approve: 18.4s | 2 | 195451 | unknown |

| Input | Cached input | Output | Process | Engine overhead | Startup per attempt | Validation per attempt | Packet bytes |
|---:|---:|---:|---:|---:|---|---|---:|
| 193209 | 154496 | 2242 | 75.1s | 8.0s | 2.6s, 1.7s | 0.4s, 0.7s | 5297 |

### todo-app-2 / fast / 003-ignore-a-new-todo-whose-title-is-already-in-the-list

State: merged; profiles: [('codex', 'gpt-6-luna', 'low')].

| Job | Feature | Wall time | Stage times | Agent runs | Tokens | Cost (USD) |
|---|---|---:|---|---:|---:|---:|
| 003-ignore-a-new-todo-whose-title-is-already-in-the-list | Ignore a new todo whose title is already in the list | 44.0s | build: 19.5s, approve: 16.6s | 2 | 143170 | unknown |

| Input | Cached input | Output | Process | Engine overhead | Startup per attempt | Validation per attempt | Packet bytes |
|---:|---:|---:|---:|---:|---|---|---:|
| 140962 | 126208 | 2208 | 36.1s | 7.8s | 1.1s, 1.5s | 0.8s, 0.6s | 5671 |

Stage times sum CLI process durations, including retries and superseded executions; parallel stages overlap.
Engine overhead is wall minus the sum of CLI durations (§14); it can be negative for parallel Full reviews.
Tokens = input + output; cached input is a subset of input and is shown separately.
Packet bytes are summed across attempts. Missing observations remain unknown.

### Fast vs Full

Pairs require the same repo, feature and provider/model/effort. Ratios require both jobs approved/merged; duplicates are ambiguous.

| Repo / feature / profiles | Fast wall | Full wall | Fast / Full wall | Fast tokens | Full tokens | Fast / Full tokens | Fast calls | Full calls |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| ('todo-app-1', 'Change the Add button\'s text to "Add todo"', (('codex', 'gpt-6-luna', 'low'),)) | 53.0s | unknown | unknown | 178624 | unknown | unknown | 2 | 8 |
| ('todo-app-1', 'Ignore a new todo whose title is already in the list', (('codex', 'gpt-6-luna', 'low'),)) | 50.3s | 587.5s | 8.6% | 179865 | 633843 | 28.4% | 2 | 7 |
| ('todo-app-1', 'Show the number of open todos in the page title, like "Todos (3)"', (('codex', 'gpt-6-luna', 'low'),)) | 239.4s | 631.5s | unknown | 450947 | 3025155 | unknown | 2 | 10 |
| ('todo-app-2', 'Change the Add button\'s text to "Add todo"', (('codex', 'gpt-6-luna', 'low'),)) | 32.2s | 491.8s | unknown | 136792 | unknown | unknown | 2 | 8 |
| ('todo-app-2', 'Ignore a new todo whose title is already in the list', (('codex', 'gpt-6-luna', 'low'),)) | 44.0s | 232.1s | unknown | 143170 | 534763 | unknown | 2 | 2 |
| ('todo-app-2', 'Show the number of open todos in the page title, like "Todos (3)"', (('codex', 'gpt-6-luna', 'low'),)) | 83.1s | unknown | unknown | 195451 | 1515909 | unknown | 2 | 6 |

## v2 benchmark — 2026-10-16T12:28:49+02:00

Sources: `C:\Users\Administrator\AppData\Local\Temp\factory-bench-23-sol-full`, `C:\Users\Administrator\AppData\Local\Temp\factory-bench-23-sol-fast`

### todo-app-1 / full / 001-change-the-add-button-s-text-to-add-todo

State: merged; profiles: [('codex', 'gpt-6.1-sol', 'medium')].

| Job | Feature | Wall time | Stage times | Agent runs | Tokens | Cost (USD) |
|---|---|---:|---|---:|---:|---:|
| 001-change-the-add-button-s-text-to-add-todo | Change the Add button's text to "Add todo" | 485.5s | spec: 42.8s, build: 194.8s, review: 272.4s, approve: 37.3s | 7 | 1258714 | unknown |

| Input | Cached input | Output | Process | Engine overhead | Startup per attempt | Validation per attempt | Packet bytes |
|---:|---:|---:|---:|---:|---|---|---:|
| 1245281 | 1100160 | 13433 | 547.3s | -61.9s | 19.6s, 10.0s, 1.5s, 0.8s, 0.8s, 0.8s, 2.4s | 1.8s, 0.7s, 0.7s, 1.4s, 1.5s, 0.6s, 0.6s | 45614 |

### todo-app-1 / full / 002-show-the-number-of-open-todos-in-the-page-title-like-todos-3

State: needs_human; profiles: [('codex', 'gpt-6.1-sol', 'medium')].

| Job | Feature | Wall time | Stage times | Agent runs | Tokens | Cost (USD) |
|---|---|---:|---|---:|---:|---:|
| 002-show-the-number-of-open-todos-in-the-page-title-like-todos-3 | Show the number of open todos in the page title, like "Todos (3)" | 557.6s | spec: 98.2s, build: 162.6s, review: 317.4s, approve: 42.1s | 7 | 1293536 | unknown |

| Input | Cached input | Output | Process | Engine overhead | Startup per attempt | Validation per attempt | Packet bytes |
|---:|---:|---:|---:|---:|---|---|---:|
| 1276777 | 1154944 | 16759 | 620.3s | -62.7s | 10.4s, 7.2s, 0.8s, 1.3s, 1.2s, 1.7s, 5.5s | 1.7s, 0.4s, 2.8s, 0.6s, 0.3s, 0.3s, 0.3s | 52076 |

### todo-app-1 / full / 003-ignore-a-new-todo-whose-title-is-already-in-the-list

State: needs_human; profiles: [('codex', 'gpt-6.1-sol', 'medium')].

| Job | Feature | Wall time | Stage times | Agent runs | Tokens | Cost (USD) |
|---|---|---:|---|---:|---:|---:|
| 003-ignore-a-new-todo-whose-title-is-already-in-the-list | Ignore a new todo whose title is already in the list | 765.3s | spec: 92.6s, build: 173.2s, review: 375.1s, approve: 58.8s | 10 | unknown | unknown |

| Input | Cached input | Output | Process | Engine overhead | Startup per attempt | Validation per attempt | Packet bytes |
|---:|---:|---:|---:|---:|---|---|---:|
| unknown | unknown | unknown | 699.8s | 65.5s | 5.0s, 27.2s, 2.7s, 1.0s, 0.9s, unknown, 3.9s, 2.8s, 1.5s, 0.7s | 2.4s, 0.5s, 0.7s, 0.0s, 0.0s, 0.0s, 0.3s, 0.5s, 0.8s, 0.7s | 79373 |

### todo-app-2 / full / 001-change-the-add-button-s-text-to-add-todo

State: merged; profiles: [('codex', 'gpt-6.1-sol', 'medium')].

| Job | Feature | Wall time | Stage times | Agent runs | Tokens | Cost (USD) |
|---|---|---:|---|---:|---:|---:|
| 001-change-the-add-button-s-text-to-add-todo | Change the Add button's text to "Add todo" | 453.0s | spec: 42.9s, build: 203.8s, review: 232.9s, approve: 47.6s | 7 | 1259935 | unknown |

| Input | Cached input | Output | Process | Engine overhead | Startup per attempt | Validation per attempt | Packet bytes |
|---:|---:|---:|---:|---:|---|---|---:|
| 1245669 | 1120384 | 14266 | 527.2s | -74.2s | 14.3s, 4.8s, 1.7s, 0.9s, 0.9s, 0.8s, 1.5s | 2.5s, 0.7s, 0.2s, 1.3s, 0.5s, 0.4s, 0.9s | 45476 |

### todo-app-2 / full / 002-show-the-number-of-open-todos-in-the-page-title-like-todos-3

State: merged; profiles: [('codex', 'gpt-6.1-sol', 'medium')].

| Job | Feature | Wall time | Stage times | Agent runs | Tokens | Cost (USD) |
|---|---|---:|---|---:|---:|---:|
| 002-show-the-number-of-open-todos-in-the-page-title-like-todos-3 | Show the number of open todos in the page title, like "Todos (3)" | 643.6s | spec: 102.3s, build: 160.3s, review: 288.7s, approve: 91.4s | 8 | unknown | unknown |

| Input | Cached input | Output | Process | Engine overhead | Startup per attempt | Validation per attempt | Packet bytes |
|---:|---:|---:|---:|---:|---|---|---:|
| unknown | unknown | unknown | 642.8s | 0.8s | 7.9s, 9.9s, 0.9s, 1.2s, 2.1s, 4.7s, 3.3s, 3.4s | 22.2s, 0.5s, 1.3s, 0.6s, 0.4s, 0.8s, 0.0s, 0.2s | 58987 |

### todo-app-2 / full / 003-ignore-a-new-todo-whose-title-is-already-in-the-list

State: merged; profiles: [('codex', 'gpt-6.1-sol', 'medium')].

| Job | Feature | Wall time | Stage times | Agent runs | Tokens | Cost (USD) |
|---|---|---:|---|---:|---:|---:|
| 003-ignore-a-new-todo-whose-title-is-already-in-the-list | Ignore a new todo whose title is already in the list | 740.1s | spec: 52.8s, build: 207.7s, review: 356.4s, approve: 46.8s | 9 | unknown | unknown |

| Input | Cached input | Output | Process | Engine overhead | Startup per attempt | Validation per attempt | Packet bytes |
|---:|---:|---:|---:|---:|---|---|---:|
| unknown | unknown | unknown | 663.7s | 76.4s | 2.5s, 2.7s, 0.9s, 1.6s, 1.1s, 2.2s, 0.8s, 0.8s, 1.3s | 3.9s, 0.5s, 0.3s, 0.0s, 0.0s, 0.6s, 0.2s, 0.7s, 0.5s | 67946 |

### todo-app-1 / fast / 001-change-the-add-button-s-text-to-add-todo

State: merged; profiles: [('codex', 'gpt-6.1-sol', 'medium')].

| Job | Feature | Wall time | Stage times | Agent runs | Tokens | Cost (USD) |
|---|---|---:|---|---:|---:|---:|
| 001-change-the-add-button-s-text-to-add-todo | Change the Add button's text to "Add todo" | 288.3s | build: 227.1s, approve: 39.1s | 2 | 669030 | unknown |

| Input | Cached input | Output | Process | Engine overhead | Startup per attempt | Validation per attempt | Packet bytes |
|---:|---:|---:|---:|---:|---|---|---:|
| 664446 | 604160 | 4584 | 266.2s | 22.1s | 18.8s, 1.6s | 0.6s, 0.4s | 6160 |

### todo-app-1 / fast / 002-show-the-number-of-open-todos-in-the-page-title-like-todos-3

State: merged; profiles: [('codex', 'gpt-6.1-sol', 'medium')].

| Job | Feature | Wall time | Stage times | Agent runs | Tokens | Cost (USD) |
|---|---|---:|---|---:|---:|---:|
| 002-show-the-number-of-open-todos-in-the-page-title-like-todos-3 | Show the number of open todos in the page title, like "Todos (3)" | 332.7s | build: 250.1s, approve: 50.8s | 2 | 722776 | unknown |

| Input | Cached input | Output | Process | Engine overhead | Startup per attempt | Validation per attempt | Packet bytes |
|---:|---:|---:|---:|---:|---|---|---:|
| 716024 | 658560 | 6752 | 300.9s | 31.8s | 10.2s, 0.8s | 3.9s, 0.2s | 7305 |

### todo-app-1 / fast / 003-ignore-a-new-todo-whose-title-is-already-in-the-list

State: merged; profiles: [('codex', 'gpt-6.1-sol', 'medium')].

| Job | Feature | Wall time | Stage times | Agent runs | Tokens | Cost (USD) |
|---|---|---:|---|---:|---:|---:|
| 003-ignore-a-new-todo-whose-title-is-already-in-the-list | Ignore a new todo whose title is already in the list | 317.3s | build: 243.7s, approve: 39.4s | 2 | 678265 | unknown |

| Input | Cached input | Output | Process | Engine overhead | Startup per attempt | Validation per attempt | Packet bytes |
|---:|---:|---:|---:|---:|---|---|---:|
| 672475 | 606208 | 5790 | 283.1s | 34.2s | 4.7s, 2.5s | 0.4s, 0.5s | 8592 |

### todo-app-2 / fast / 001-change-the-add-button-s-text-to-add-todo

State: merged; profiles: [('codex', 'gpt-6.1-sol', 'medium')].

| Job | Feature | Wall time | Stage times | Agent runs | Tokens | Cost (USD) |
|---|---|---:|---|---:|---:|---:|
| 001-change-the-add-button-s-text-to-add-todo | Change the Add button's text to "Add todo" | 265.0s | build: 190.2s, approve: 48.9s | 2 | 430029 | unknown |

| Input | Cached input | Output | Process | Engine overhead | Startup per attempt | Validation per attempt | Packet bytes |
|---:|---:|---:|---:|---:|---|---|---:|
| 425622 | 373504 | 4407 | 239.0s | 26.0s | 15.0s, 2.0s | 2.1s, 0.4s | 6654 |

### todo-app-2 / fast / 002-show-the-number-of-open-todos-in-the-page-title-like-todos-3

State: merged; profiles: [('codex', 'gpt-6.1-sol', 'medium')].

| Job | Feature | Wall time | Stage times | Agent runs | Tokens | Cost (USD) |
|---|---|---:|---|---:|---:|---:|
| 002-show-the-number-of-open-todos-in-the-page-title-like-todos-3 | Show the number of open todos in the page title, like "Todos (3)" | 330.1s | build: 260.1s, approve: 43.7s | 2 | 736463 | unknown |

| Input | Cached input | Output | Process | Engine overhead | Startup per attempt | Validation per attempt | Packet bytes |
|---:|---:|---:|---:|---:|---|---|---:|
| 730619 | 694144 | 5844 | 303.8s | 26.3s | 8.1s, 0.9s | 0.8s, 0.2s | 7260 |

### todo-app-2 / fast / 003-ignore-a-new-todo-whose-title-is-already-in-the-list

State: merged; profiles: [('codex', 'gpt-6.1-sol', 'medium')].

| Job | Feature | Wall time | Stage times | Agent runs | Tokens | Cost (USD) |
|---|---|---:|---|---:|---:|---:|
| 003-ignore-a-new-todo-whose-title-is-already-in-the-list | Ignore a new todo whose title is already in the list | 322.6s | build: 257.3s, approve: 35.6s | 2 | 666954 | unknown |

| Input | Cached input | Output | Process | Engine overhead | Startup per attempt | Validation per attempt | Packet bytes |
|---:|---:|---:|---:|---:|---|---|---:|
| 661075 | 593792 | 5879 | 292.9s | 29.7s | 2.5s, 0.9s | 0.7s, 0.7s | 8492 |

Stage times sum CLI process durations, including retries and superseded executions; parallel stages overlap.
Engine overhead is wall minus the sum of CLI durations (§14); it can be negative for parallel Full reviews.
Tokens = input + output; cached input is a subset of input and is shown separately.
Packet bytes are summed across attempts. Missing observations remain unknown.

### Fast vs Full

Pairs require the same repo, feature and provider/model/effort. Ratios require both jobs approved/merged; duplicates are ambiguous.

| Repo / feature / profiles | Fast wall | Full wall | Fast / Full wall | Fast tokens | Full tokens | Fast / Full tokens | Fast calls | Full calls |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| ('todo-app-1', 'Change the Add button\'s text to "Add todo"', (('codex', 'gpt-6.1-sol', 'medium'),)) | 288.3s | 485.5s | 59.4% | 669030 | 1258714 | 53.2% | 2 | 7 |
| ('todo-app-1', 'Ignore a new todo whose title is already in the list', (('codex', 'gpt-6.1-sol', 'medium'),)) | 317.3s | 765.3s | unknown | 678265 | unknown | unknown | 2 | 10 |
| ('todo-app-1', 'Show the number of open todos in the page title, like "Todos (3)"', (('codex', 'gpt-6.1-sol', 'medium'),)) | 332.7s | 557.6s | unknown | 722776 | 1293536 | unknown | 2 | 7 |
| ('todo-app-2', 'Change the Add button\'s text to "Add todo"', (('codex', 'gpt-6.1-sol', 'medium'),)) | 265.0s | 453.0s | 58.5% | 430029 | 1259935 | 34.1% | 2 | 7 |
| ('todo-app-2', 'Ignore a new todo whose title is already in the list', (('codex', 'gpt-6.1-sol', 'medium'),)) | 322.6s | 740.1s | 43.6% | 666954 | unknown | unknown | 2 | 9 |
| ('todo-app-2', 'Show the number of open todos in the page title, like "Todos (3)"', (('codex', 'gpt-6.1-sol', 'medium'),)) | 330.1s | 643.6s | 51.3% | 736463 | unknown | unknown | 2 | 8 |
