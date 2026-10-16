# Dashboard browser tests

Run from the repository root:

```powershell
uv sync --locked
cd web
npm ci
npx playwright install --with-deps chromium
npm run build
npm run e2e
```

Each test starts a real FastAPI engine on a free loopback port, with two temporary
git repositories, a private FACTORY_HOME, the stub adapter, and tests/fake_cli.py.
No real provider is invoked. Cleanup stops outstanding attempts through the API
and waits for process ownership cleanup before removing the temporary directories.
The reconnect case kills and restarts the engine without replacing the browser page.

The four scenarios cover all Playwright cases in plan 16.4. Vitest runs separately
with `npm test`; its source directory selection excludes the Playwright suite.
CI runs both suites on Windows and Ubuntu 24.04 and uploads traces and screenshots
on failure. It never updates baselines.

The dark Chromium baselines are separated by OS because font rasterization differs.
To intentionally update them, run `npm run e2e:update` on each platform, inspect
all eight images, then run `npm run e2e` to check comparisons. The Linux baselines
were generated with mcr.microsoft.com/playwright:v1.64.1-noble on Ubuntu 24.04.
Keep the Playwright/browser version in package-lock.json aligned when updating.

Screenshots hide temporary repository paths and pipeline status rows containing
variable execution durations; board stage timers are masked. State, activity and
transitions are asserted in the functional scenario. Axe checks the unmodified
page before screenshot-only styles are applied, including the pipeline nodes.
