# Release pipeline

CI runs the Python 3.11–3.13 suite on Windows, Linux, and macOS, web checks,
Windows/Linux Playwright tests, package validation, and installed-wheel smoke tests.
The release workflow calls this same CI workflow before publishing the tested artifacts.
The wheel and sdist both retain the built dashboard; SHA256SUMS covers both files.

Repository owners must configure the `pypi` and `testpypi` GitHub environments
and corresponding trusted publishers on PyPI and TestPyPI. Set the publisher's
workflow filename to `release.yml` and its environment to the matching name.
Configure environment protection rules as appropriate. No API tokens or other
stored secrets are used. See the [PyPI trusted publishing guide](https://docs.pypi.org/trusted-publishers/using-a-publisher/).

Before tagging, add an exact version section to CHANGELOG.md, including whether
repos need `factory upgrade`. Tags must use `vMAJOR.MINOR.PATCH` or
`vMAJOR.MINOR.PATCHrcN`. Candidate tags publish only to TestPyPI and create a
GitHub prerelease; stable tags publish to PyPI. Release assets include the wheel,
sdist, and SHA256SUMS, with GitHub build provenance attestations.

Local validation (after `uv sync --locked`):

```powershell
cd web
npm ci
npm run build
cd ..
uv build
uv run --locked python tools/release_artifacts.py dist
uv run --no-project --python 3.11 python tools/release_smoke.py dist
```

The smoke test uses isolated uv tool and machine directories, runs `factory init`
with both clients (dry run and idempotence included), starts the installed service,
checks health, overview, and dashboard HTML, runs doctor, and completes the built-in
Fast pipeline through the Codex adapter using the fake CLI. It never invokes Claude
or Codex and shuts down the service before removing its temporary files.

TODO (plan §16.5, session 4.2): extend the installed smoke test with v1 migration,
customised-role preservation, read-only legacy jobs, and previous-minor
upgrade/rollback persistence. There is no released v2 previous minor yet.
Do not tag a release until session 4.4's parity and migration prerequisites pass.
