Verified process-lifetime, rework, port-allocation, and spec-compliance defects remain; none depends on deferred Claude work. The full Windows suite returned 625 passed and one PermissionError failure in the legacy watcher concurrency test, which is not reported as an introduced regression.

Full review comments:

- [P1] Drain live attempts before applying a rework Ă˘â‚¬â€ť C:\Source\projects\agent-factory\src\agent_factory\engine\runtime.py:369-373
  If one parallel reviewer exhausts its contract retry while another is still running, the job becomes `needs_human` and this action immediately permits rework. The old execution is superseded without stopping its process, while the scheduler can launch the new builder against the same worktree. A SQLite reproduction produced a running round-1 reviewer alongside a launching round-2 task. Reject rework until all old attempts finish, or stop and drain them before rewinding; add a process-backed regression test. This violates the [one-active-step invariant](docs/cli-orchestrator-plan.md#L711-L713).

- [P1] Prevent Resume while a dead wrapper's CLI survives Ă˘â‚¬â€ť C:\Source\projects\agent-factory\src\agent_factory\engine\supervisor.py:399-404
  On POSIX, killing only the wrapper does not kill its process group. Verified in WSL: after killing the wrapper, the fake CLI remained alive and no `exit.json` existed. Recovery marks the attempt lost here, and Resume can launch another attempt while the original CLI still modifies the worktree. Existing recovery tests kill the entire group on POSIX, hiding this scenario. Provide crash-safe descendant containment or retain verifiable descendant ownership and block replacement until they exit; merely marking the wrapper lost does not provide the [specified containment](docs/cli-orchestrator-plan.md#L179-L181).

- [P2] Clean up descendants that leave the POSIX process group Ă˘â‚¬â€ť C:\Source\projects\agent-factory\src\agent_factory\run_cli.py:277-280
  On normal completion, a descendant started with `start_new_session=True` survives this `killpg`. Using the existing fake CLI with `server_new_session=True`, WSL returned CLI exit 0 and terminated the wrapper, but the grandchild server still accepted connections. Finalisation then loses the live wrapper needed to establish ownership, so later cleanup cannot reclaim that server. Track and terminate verified descendants outside the group before publishing completion, and test normal completionĂ˘â‚¬â€ťnot just StopĂ˘â‚¬â€ťwith an escaped server. The spec requires [owned ports to be freed after execution](docs/cli-orchestrator-plan.md#L721-L723).

- [P2] Allocate nonoverlapping ports to repeated reviewer roles Ă˘â‚¬â€ť C:\Source\projects\agent-factory\src\agent_factory\engine\ports.py:100-103
  Two `reviewer-ux` executions in one parallel group both receive ports 5103Ă˘â‚¬â€ś5105 for a 5100 lease; repeated UI reviewers likewise collide. This configuration is explicitly supported, including with the same provider, and `_check_ports` allows listeners belonging to another attempt in the job, so both executions launch. Allocate slots by execution identity and serialize port-holding executions when the block lacks capacity. Add a repeated-UX/UI test that actually binds the assigned ports, beyond the existing repeated-role validation tests ([spec](docs/cli-orchestrator-plan.md#L363-L364)).

- [P2] Select current spec evidence when assembling acceptance criteria Ă˘â‚¬â€ť C:\Source\projects\agent-factory\src\agent_factory\engine\packet.py:205-214
  When an allowed `onChanges` target reruns the spec task, earlier completed spec executions remain `done` as history. This loop appends their acceptance criteria alongside the replacement spec's criteria. A reproduction with two rounds included both conflicting requirements, giving downstream agents obsolete instructions; repeated specs can also exhaust the protected packet budget. Select surviving evidence using the pipeline's round-invalidation rules rather than every historical completed spec, and test a rewind to the spec task ([packet requirement](docs/cli-orchestrator-plan.md#L689)).

- [P2] Persist executable paths discovered by doctor Ă˘â‚¬â€ť C:\Source\projects\agent-factory\src\agent_factory\engine\scaffold.py:96-103
  `doctor` discovers executable paths only for display and never stores them in machine settings. Verified on Windows: it reported the absolute `codex.CMD` path successfully, but `cli.codex` remained null. Consequently, a service started from a different PATH can still fail to launch Codex after doctor succeeds, contrary to the [machine-settings requirement](docs/cli-orchestrator-plan.md#L374-L377). Persist validated discoveries without overwriting explicit overrides, update the running adapter's settings, and test launching from a service environment without Codex on PATH.

## Fixes

All six findings are accepted and fixed.

- **Rework and live peers:** `pipeline.rework_job` rejects the mutation inside its
  transaction while any attempt is launching or running. The regression exhausts one
  parallel reviewer's contract retry while its peer remains a live fake-CLI process,
  then checks that rework cannot supersede that peer or launch another round.
- **POSIX wrapper death:** the CLI and its descendants inherit a unique attempt owner
  token in their environment. Resume and rework check for surviving descendants of lost
  attempts, including processes that changed session, and reject replacement until those
  processes exit. This deliberately retains the paused/process state rather than
  automatically restarting lost work. The WSL regression kills only the wrapper, verifies
  that its CLI and escaped server survive, and checks that Resume remains blocked until
  they exit.
- **Escaped POSIX descendants on completion:** the standalone stdlib wrapper locates
  descendants by their inherited token and checks process creation times before signalling.
  It cleans them up before publishing `exit.json`, including orphaned servers in a new
  session. A process-backed normal-completion regression checks server death and port release.
- **Repeated UX/UI roles:** port slots are assigned by execution identity within the frozen
  step, preserving the usual task/UX/UI slots when available. The scheduler serializes
  executions sharing an overflow slot. Repeated UX, repeated UI, and mixed UX/UI regressions
  run four reviewers, bind every assigned port, and verify all attempts finish successfully.
- **Current spec criteria:** packet assembly applies the pipeline's upstream-round evidence
  invalidation before collecting acceptance criteria. Regressions cover a rerun spec with
  contradictory old criteria and preservation of earlier spec evidence after a build rewind.
- **Doctor discoveries:** validated provider paths are persisted atomically only when no
  explicit override exists. The running supervisor refreshes adapter settings before
  preparing a launch. Regressions verify discovery persistence, preservation of an explicit
  override, and a fake-CLI launch from an already running adapter with an empty PATH.

The pre-existing plan-runner changes (medium effort limits and credit-exhaustion retry),
their tests, and the associated plan/prompt edits are preserved in this commit.
Claude experiments and mixed-provider parity remain deferred to session 1.11. No provider
was invoked for this review fix session.

## Not fixed

None. All reported findings were accepted.


## Validation

- `uv sync --locked`: succeeded on Windows and in an isolated WSL checkout.
- YAPF formatted and checked all 16 changed Python files; the final
  `uv run --locked yapf --diff` check exited zero. Fixtures were left unchanged.
- WSL: `uv run --locked pytest tests/test_run_cli.py tests/test_engine_supervisor.py tests/test_engine_ports.py tests/test_engine_packet.py -q`
  â€” 100 passed, 1 skipped.
- After strengthening the PID-reuse check, Windows:
  `uv run --locked pytest tests/test_run_cli.py -q` â€” 23 passed, 1 skipped.
- After that same change, WSL:
  `uv run --locked pytest tests/test_run_cli.py tests/test_engine_supervisor.py -q -k "run_cli or resume_blocks_surviving"`
  â€” 24 passed, 1 skipped, 32 deselected.

The WSL checkout uses the locked environment with Python 3.12 and a build-version override
because it is a source copy without Git metadata. macOS was not available for execution.

Windows full suite: `uv run --locked pytest -q` — **634 passed, 2 skipped,
55 subtests passed**, with one FastAPI/Starlette HTTPX deprecation warning. The subsequent
wrapper-only run listed above also covers the two added PID-reuse cases.
