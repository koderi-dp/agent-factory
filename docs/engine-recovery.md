# Session 1.5: claims and recovery

`Scheduler` owns a dedicated SQLite writer thread for the machine DB and each
available registered repo. A machine DB `BEGIN IMMEDIATE` serialises the global
capacity check and the short repo claim transaction. The repo transaction checks
the frozen per-job limit, claims a pending execution in pipeline order, and inserts
a `launching` attempt protected by `one_live_attempt`. Each successful claim moves
the round-robin cursor to the next repo. Capacity includes both launching and
running attempts and is recomputed from all available repo DBs.

`Supervisor.start()` recovers every repo before enabling claims. `tick()` polls
attempts and fills available slots. The eventual service supplies its wake-ups
and two-second timer in session 1.9. Closing the scheduler closes DB connections
without stopping detached wrappers.

Preparation writes prompt, system, command and packet artifacts atomically. Spawn
uses session 1.4's immutable base-Python wrapper. Only verified PID, creation time
and owner token permit adoption. The wrapper's process creation time supplies
`t_launch`, including when a fast CLI finishes before the record transaction.
An exit without remaining process metadata uses the durable queue time as the
remaining lower bound for report freshness.

| Recovery evidence | Implementation/test behavior |
|---|---|
| Live `proc.json` identity matches | Adopt and keep the persisted byte offset. |
| `exit.json` exists | Drain the log, classify through the injected adapter, atomically finalise and advance. Repeating recovery does not duplicate completion. |
| `proc.json` exists but wrapper is dead | Mark lost, fail the execution and pause the job with `process`. Never auto-relaunch. |
| No `proc.json`, token process found | Wait up to 10 seconds for publication, then verify the complete identity. Timeout pauses rather than relaunches. |
| No process evidence and unset launch time | Delete only the unspawned launching attempt and restore the claimed execution to pending. |

A recorded launch without process evidence also becomes lost. A failed process
inspection is retried for up to 250 ms with the same strict identity checks before
deciding that adoption is impossible. A transient read never authorizes a weaker
ownership check or a replacement launch.

Complete log lines, parser effects, session IDs, usage and `log_offset` commit
together. A compare-and-set on the previous offset prevents stale tail readers
from moving it backwards. UTF-8, CRLF, large events and the final unterminated
fragment are covered. Recovery also reads session evidence before marking a
wrapper lost, so explicit Resume can reuse the exact known session. Unknown
sessions start fresh; retry context exposes the partial-work warning to the
future packet builder.

Exit classification is injected through the neutral `Adapter` protocol in
`supervisor.py`. Tests use `tests/engine_harness.py` and `tests/fake_cli.py`; no
real provider runs. A valid result and its pipeline transition commit together.
Invalid reports permit one fresh-session contract retry, then need human action.
Other outcomes preserve the adapter's pause reason. A successful parallel peer's
result is retained if another peer has already paused the job.

## Validation

Run on Windows with Python 3.11. Tests use real SQLite databases, detached wrappers,
fake CLI processes and subprocess service restarts. The recovery-table cases,
concurrent claims from competing schedulers, strict ownership retry, explicit
Resume, report retry, log offsets and 2 repos × 3 jobs × 4 parallel reviews are
covered by:

```powershell
uv run pytest tests/test_engine_supervisor.py -q
```

The six crash points are exactly `after_claim`, `after_prepare`, `after_spawn`,
`before_record`, `during_tail`, and `before_advance_commit`. Each crash test
requires exit status 1, restarts the service, checks the final state against the
crash-free path, and checks exactly one fake CLI invocation and one committed
completion. Before restart, after-claim/after-prepare require zero invocations.

The required 20-iteration loop additionally repeats the real concurrent-workload
test every iteration:

```powershell
$stressLog = Join-Path (git rev-parse --git-dir) 'session-1.5-stress-final.log'
foreach ($iteration in 1..20) {
    "Iteration $iteration/20" | Tee-Object -FilePath $stressLog -Append
    uv run pytest tests/test_engine_supervisor.py -q -k 'crash_injection or live_limits' 2>&1 |
        Tee-Object -FilePath $stressLog -Append
    if ($LASTEXITCODE -ne 0) {
        throw "Session 1.5 stress failed in iteration $iteration; see $stressLog"
    }
}
```

Final focused run: **29 passed**. The replacement stress loop completed all
**20 iterations × 7 cases = 140 passed** (120 injected crashes and 20 complete
concurrent workloads), with **zero failures, duplicate launches or remaining
flakiness**. Every iteration reached all six named crash markers.

Full repository check: `uv run pytest` → **427 passed in 185.58 s** on Windows,
Python 3.11.15. This includes the existing live Windows Job Object and dummy-tool
`uv tool install --force` spike tests. `git diff --check` also passed.

During development, tests were corrected to wait for fake-CLI readiness before
hiding `proc.json` and to inspect claim timestamp order rather than thread-pool
submission order. A concurrent run also exposed a premature lost decision after
an unsuccessful ownership read; recovery now retries strict identity inspection,
with a dedicated regression test.

The first stress loop failed in iteration 13: a valid `exit.json` was classified
as invalid after an unsuccessful artifact read. Artifact readers now retry
`PermissionError` for up to two seconds outside any transaction; exhausted I/O
errors propagate while leaving the live row intact. A regression test injects a
sharing violation on a completed attempt's exit file. The failed run's artifacts
are retained locally in `.git/session-1.5-failure-13-artifacts.zip`, alongside
`.git/session-1.5-stress.log`. Crash injection now emits its exact point on stderr,
which the tests assert, so an unrelated service error cannot count as reaching a
requested crash point.

Provider parsing/classification moves into `adapters/` in session 1.6. Ports,
engine Stop/timeout handling, real execution packets and git checks remain in
sessions 1.7–1.8. API/service wiring remains in 1.9. Replacing an installed
agent-factory wheel and Linux execution were not tested in this session; the
dummy-tool upgrade-under-load evidence remains in `docs/spikes.md`, and full
upgrade integration belongs to the later upgrade work. No schema or v1 runtime
changes were needed. There are no open questions or spec changes.
