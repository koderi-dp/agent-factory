# Changelog

## [Unreleased]

- Add the packaged dashboard, engine, CLI, and CI/release pipeline.
- Existing v1 repositories will need `factory upgrade` when the migration ships.
  Migration and release readiness remain pending sessions 4.2–4.4 and Claude parity.

Before tagging, add a section named for the exact version (for example,
`## [1.0.0rc1]`). State whether `factory upgrade` is required. The release workflow
rejects tags without matching notes before publishing.
