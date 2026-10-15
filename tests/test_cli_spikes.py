"""Offline checks on real session 0.2 captures; never launch a paid CLI."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from spikes import cli_behaviour


FIXTURES = Path(__file__).parent / "fixtures" / "codex"


def events(case: str) -> list[dict]:
    return [json.loads(line) for line in (FIXTURES / f"{case}.jsonl").read_text(
        encoding="utf-8").splitlines()]


def metadata(case: str) -> dict:
    return json.loads((FIXTURES / f"{case}.meta.json").read_text(encoding="utf-8"))


class CliSpikeTests(unittest.TestCase):
    def test_all_captures_are_complete_and_have_companion_files(self):
        for path in FIXTURES.glob("*.meta.json"):
            case = path.name.removesuffix(".meta.json")
            with self.subTest(case=case):
                meta = metadata(case)
                self.assertFalse(meta["killed"])
                self.assertIsInstance(meta["exit_code"], int)
                self.assertTrue((FIXTURES / f"{case}.stderr").exists())
                stream = events(case)
                if stream:
                    self.assertEqual(stream[0]["type"], "thread.started")
                    terminal = "turn.completed" if meta["exit_code"] == 0 else "turn.failed"
                    self.assertEqual(stream[-1]["type"], terminal)
                else:
                    self.assertEqual(meta["exit_code"], 1)
                    self.assertTrue((FIXTURES / f"{case}.stderr").read_text(encoding="utf-8"))

    def test_success_and_auth_failure_shapes(self):
        self.assertEqual(events("success")[2]["item"]["text"], "SPIKE_OK")
        stream = events("auth")
        self.assertTrue(any(event["type"] == "error" for event in stream))
        self.assertIn("401 Unauthorized", stream[-1]["error"]["message"])
        self.assertIn("codex-empty-home", metadata("auth")["env_overrides"]["CODEX_HOME"])

    def test_long_command_pairs_started_and_completed(self):
        commands = [event for event in events("long")
                    if event.get("item", {}).get("type") == "command_execution"]
        self.assertEqual([event["type"] for event in commands], ["item.started", "item.completed"])
        self.assertEqual(commands[0]["item"]["id"], commands[1]["item"]["id"])
        self.assertIsNone(commands[0]["item"]["exit_code"])
        self.assertEqual(commands[1]["item"]["exit_code"], 0)
        self.assertIn("LONG_START", commands[1]["item"]["aggregated_output"])
        self.assertIn("LONG_END", commands[1]["item"]["aggregated_output"])
        self.assertGreaterEqual(metadata("long")["elapsed_seconds"], 12)

    def test_denial_is_in_stderr_and_turn_continues_successfully(self):
        stream = events("denial-home")
        self.assertEqual(metadata("denial-home")["exit_code"], 0)
        self.assertEqual(stream[2]["item"]["text"], "DENIAL_CONTINUED")
        self.assertFalse(any(event.get("item", {}).get("type") == "command_execution"
                             for event in stream))
        self.assertIn("policy forbids commands starting with", (FIXTURES /
                      "denial-home.stderr").read_text(encoding="utf-8"))

    def test_instruction_and_cache_observations(self):
        self.assertEqual(events("developer")[2]["item"]["text"], "DEVELOPER_WINS")
        self.assertEqual(events("prepend")[2]["item"]["text"], "PREPEND_WINS")
        self.assertIn("unknown configuration field `system_instructions`", (FIXTURES /
                      "system.stderr").read_text(encoding="utf-8"))
        self.assertEqual(metadata("cache-short1")["argv"], metadata("cache-short2")["argv"])
        for case in ("cache-short1", "cache-short2"):
            self.assertEqual(events(case)[-1]["usage"]["cached_input_tokens"], 1792)
            self.assertEqual(events(case)[-1]["usage"]["input_tokens"], 14357)

    def test_redaction_preserves_json_including_nested_config_strings(self):
        with tempfile.TemporaryDirectory() as temp:
            scratch = Path(temp)
            for depth in range(4):
                value = str(scratch / "probe.txt")
                for _ in range(depth):
                    value = json.dumps(value)
                redacted = cli_behaviour.redact(value, scratch)
                self.assertNotIn(scratch.name, redacted)
                self.assertIn("<SCRATCH>", redacted)
                if depth:
                    json.loads(redacted)
        self.assertEqual(cli_behaviour.redact("sk-secret123", Path("unused")), "<REDACTED_KEY>")

    def test_existing_evidence_cannot_be_overwritten(self):
        with patch.object(cli_behaviour, "workspace") as workspace:
            with self.assertRaises(FileExistsError):
                cli_behaviour.run("codex", "success")
            workspace.assert_not_called()


if __name__ == "__main__":
    unittest.main()
