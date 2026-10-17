# Changelog

## [Unreleased]

- Adds OpenCode V2 (`opencode run`) as a third provider alongside Codex and deferred
  Claude: `factory init --provider opencode` writes the client skill to
  `.opencode/skills/factory/SKILL.md`, `factory doctor` probes and records `cli.opencode`,
  `maxBudgetUsd` is ignored for it like Codex, and the adapter drives
  `opencode run --format json --standalone --auto` with `--session` on resume. A profile's
  `model` must be the CLI's `provider/model` form and its `effort` becomes the `#variant`.
  OpenCode is verified end-to-end: a greenfield `factory init --provider opencode` project
  ran the Fast pipeline on 2026-10-17 (build `STATUS: READY`, approve `APPROVE`, both
  executions on `opencode/deepseek-v4.1-flash-free`), and `factory approve` merged the
  branch. That run was local — the release smoke still drives Codex with the fake CLI, so
  OpenCode has no CI coverage yet.

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
