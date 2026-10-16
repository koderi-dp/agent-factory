# Working on agent-factory

Use Python 3.11+ and the development environment from `uv sync --locked`.

## Python formatting

YAPF is the Python formatter for this repository. Its pinned version and style live in
`pyproject.toml` (PEP 8, four spaces, 100 columns).

Before testing and committing Python changes, format each Python file you created or
modified, then check those same files:

```powershell
uv run --locked yapf --in-place path/to/file.py path/to/other.py
uv run --locked yapf --diff path/to/file.py path/to/other.py
```

The check must exit zero. Run the relevant tests after formatting. Preserve fixture
contents used as test evidence; do not reformat files under `tests/fixtures/`.

For an explicitly requested repository-wide formatting pass:

```powershell
uv run --locked yapf --in-place --recursive .
uv run --locked yapf --diff --recursive .
```

For the CLI rewrite, also follow the Shared preamble and the requested session in
`docs/cli-orchestrator-prompts.md`. Session 1.10 includes the initial repository-wide
formatting pass; later sessions should format their own changed Python files.
