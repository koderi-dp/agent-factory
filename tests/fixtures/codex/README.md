# Real Codex CLI captures

Session 0.2: full redacted stdout (`.jsonl`), full redacted stderr (`.stderr`)
and command, stdin, exit code and timing (`.meta.json`). See
[`docs/spikes.md`](../../../docs/spikes.md) for commands and conclusions.

`success` is preserved from the interrupted session. `auth`, `long`, `developer`,
`system`, the denial probes and the cache probes completed in session 0.2.
Empty stdout is intentional for rejected configuration or launcher failures.
`denial`, `denial-trusted` and `denial-escalated` allowed the command;
**`denial-home` is the confirmed denial**, recorded only in stderr, followed by
successful continuation. `cache1`/`cache2` failed at the Windows launcher;
`cache-short1`/`cache-short2` are the completed consecutive pair.

Do not substitute these failures for successful behavior evidence. Do not rerun
paid probes just to improve cache numbers. Existing fixtures are protected from
overwriting by the script. Claude's separate partial captures are unchanged.
