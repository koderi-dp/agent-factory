Verified defects include lost settings edits, unintended backlog starts, and crashes while previewing invalid configuration, alongside development and build-spec gaps. The selected Python tests passed (91 passed, 1 skipped), as did all 65 Vitest tests, but they do not cover these failures. None of these findings concerns deferred Claude work.

Full review comments:

- [P1] Route sidebar navigation through the guarded router — C:\Source\projects\agent-factory\web\src\shell.tsx:168-170
  With dirty repository settings, clicking Board opens the discard dialog, but choosing Cancel still navigates away and loses the edits. I reproduced this in Chromium: the editor disappears after Cancel. These native hash links change browser history outside the router's controlled navigation. Replace them with router Links or guarded navigation, and test both cancellation and confirmation using the actual hash history. Session 3.5 explicitly requires the [dirty-state guard](docs/cli-orchestrator-prompts.md#L563-L564).

- [P1] Keep backlog actions disabled until their SSE update arrives — C:\Source\projects\agent-factory\web\src\features\backlog\Backlog.tsx:108-110
  Start becomes enabled as soon as its HTTP request finishes, while the displayed backlog still contains the old item until SSE arrives. Clicking that same button again sends the old positional index and starts the next item instead. I verified that two identical start requests return two different job IDs because the first request removes the selected item from the unchecked list. Keep actions disabled until the authoritative backlog update arrives, and preferably send and validate the displayed item's hash so stale clients cannot start or remove another item. This undermines the item-specific start guarantee in [§12.4](docs/cli-orchestrator-plan.md#124-backlog); the current E2E test only clicks Start once.

- [P2] Preserve step identity when previewing duplicate IDs — C:\Source\projects\agent-factory\web\src\features\job\pipeline\PipelineView.tsx:93-95
  While editing the Full pipeline JSON, assigning the parallel review group the preceding task's ID crashes the settings page with `Cannot read properties of undefined (reading 'profiles')`. I reproduced this in Chromium. This lookup selects the first duplicate step, whose executions do not contain the review node's execution ID, so PipelineNode dereferences an undefined entry. Carry an index or independent preview identity through layout instead of resolving unvalidated steps solely by ID. Invalid drafts must remain editable and highlight offending nodes as required by [§13.6](docs/cli-orchestrator-plan.md#L1074-L1076); add a duplicate-ID preview test.

- [P2] Authenticate mutations through the development proxy — C:\Source\projects\agent-factory\web\vite.config.ts:15-17
  The Vite-served index does not contain the factory-token meta tag, and this proxy does not supply authentication or normalize the browser's Origin. Consequently, development-mode mutations—including settings validation, saving, and Start—are rejected by the engine's Security middleware: the Origin is port 5173 rather than 5099, and the required token is absent. Add a development-only authenticated proxy/token bootstrap with same-origin handling, without weakening production security, and test a mutation through Vite. The specified [development workflow](docs/cli-orchestrator-plan.md#L946-L947) must work against real jobs.

- [P2] Generate third-party notices during the dashboard build — C:\Source\projects\agent-factory\web\package.json:7-7
  The build only runs TypeScript and Vite, with no notice-generation script, plugin, or copied notice artifact. Therefore it bundles the fonts and third-party JavaScript without producing `dashboard_dist/THIRD_PARTY_NOTICES.txt`. Add notice generation to the build and assert that the output exists and includes the bundled dependencies' required notices. This is an explicit Phase 3 requirement in [§13.7](docs/cli-orchestrator-plan.md#L1148-L1149), separate from the later wheel-packaging work.
## Resolution

All five findings were accepted and fixed.

- **Guarded sidebar navigation:** sidebar and brand links now use TanStack Router Links.
  Playwright exercises both Cancel (URL and edits preserved) and Discard (Board opens)
  using the application's actual hash history.
- **Backlog identity and pending actions:** controls remain disabled after HTTP success
  until the authoritative backlog changes. Start, remove, and reorder requests include
  the displayed item's hash; the engine checks it under the backlog file lock before
  acting. Positional CLI callers remain compatible. Three API regression cases reject
  stale start/remove/reorder requests without touching the next item. Playwright delays
  real SSE delivery and verifies controls stay disabled, then unlock after delivery,
  with only one job started.
- **Invalid pipeline preview identity:** layout nodes carry step and execution indices;
  group frames, flow edges, React keys, and rendering use these identities instead of
  resolving draft IDs with a first-match lookup. Duplicate step and execution IDs remain
  editable, retain their own profiles and columns, and show validation highlighting.
  A rendering/layout regression test covers a duplicate-ID task plus parallel group.
- **Development authentication:** a development-only Vite middleware checks the local Host
  and same-origin Origin before reading the service's token and forwarding mutations
  with the upstream Origin and Host. It binds to loopback and disables development CORS.
  Production security is unchanged. Playwright starts Vite against the real engine,
  adds an item from the browser, rejects foreign Origin/Host requests, and verifies
  direct unauthenticated engine mutations still fail.
- **Build notices:** the build generates THIRD_PARTY_NOTICES.txt with full license text
  for installed production dependencies, nested vendor notices, fonts, and copied UI.
  Missing license text fails the build. Version-specific supplemental upstream licenses
  cover npm archive omissions; their provenance is in web/licenses/README.md.
  Playwright fetches the served artifact and checks dependency names and license text.

## Validation

Rechecked the interrupted fixes on Windows on 2026-10-16 before committing.

- `uv sync --locked` completed successfully.
- `uv run --locked yapf --in-place src/agent_factory/engine/runtime.py tests/test_engine_api.py`
  followed by the matching `yapf --diff` check: exit 0.
- `uv run --locked pytest tests/test_engine_api.py tests/test_engine_streams.py`: 61 passed.
- From `web/`: `npm run typecheck`, `npm run lint`, and `npm run build`: exit 0.
- From `web/`: `npm test`: 66 passed.
- From `web/`: `npm run e2e`: 9 passed, including unchanged dark screenshot baselines
  and axe contrast checks.

## Not fixed

None. No finding was rejected. No deferred Claude work was invoked.
