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
