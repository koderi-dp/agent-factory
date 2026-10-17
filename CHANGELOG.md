# Changelog

## [1.0.0rc1]

- First release candidate of the v2 engine: packaged dashboard, engine, CLI, and
  CI/release pipeline.
- Codex is the verified provider: a greenfield `factory init --provider codex`
  project runs the full pipeline end-to-end. Claude is not yet verified and
  remains deferred (plan §18 item 1); do not claim Claude support.
- Existing v1 repositories require `factory upgrade`; migrated repos keep their
  provider (Claude by default) and are not yet verified against a live v1 repo.
- `factory init` and `factory upgrade` record an engine range that includes the
  running release candidate (for example `>=1.0.0rc1,<2`), so a prerelease install
  can initialise and then operate its own repository.
