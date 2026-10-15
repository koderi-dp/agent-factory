# Windows process captures — session 0.3

`tree.json` records real Windows Job Object experiments against `npm run dev`:
external termination, a wrapper stop file, and last-handle closure on wrapper
death. Each first verifies survival after the launcher exits, then waits on
every member process handle and rebinds the HTTP port.

`uv.json` records local dummy-wheel installs (0.0.1, then 0.0.2 with `--force`)
and heartbeats from the same copied-script/base-Python worker during replacement.
The tool directories are isolated inside OS temp; nothing was published.

Commands and output are complete; scratch, checkout and home paths are redacted,
including nested JSON and short/long Windows path aliases. Numeric PIDs, ports,
timings, versions and protocol values are retained. Raw capture locations are
recorded locally in `.git/windows-spikes/`. Do not overwrite these fixtures.
Run `python -m unittest discover -s tests -p test_windows_spikes.py -v` to repeat
the live checks without replacing this evidence. See `docs/spikes.md` for the
environment, conclusions and limitations.
