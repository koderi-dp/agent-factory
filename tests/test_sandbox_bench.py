import json
import tempfile
import unittest
from pathlib import Path

from tools import sandbox


class SandboxBenchTests(unittest.TestCase):
    def test_benchmark_parses_job_history_and_headless_usage(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            jobs = root / "factory" / "jobs"
            job_dir = jobs / "001-feature"
            job_dir.mkdir(parents=True)
            fixture = Path(__file__).parent / "fixtures" / "bench-job.json"
            (job_dir / "job.json").write_text(fixture.read_text(encoding="utf-8"), encoding="utf-8")
            (jobs / "headless-20260101.log").write_text(
                '{"usage":{"input_tokens":120,"output_tokens":30,"cache_read_input_tokens":50},'
                '"total_cost_usd":0.0123}\n', encoding="utf-8")

            row = sandbox.benchmark_rows(root)[0]

        self.assertEqual(row["id"], "001-feature")
        self.assertAlmostEqual(row["wall"], 180)
        self.assertEqual(row["stages"], {"spec": 30, "build": 90, "review": 60})
        self.assertEqual(row["agents"], 4)
        self.assertEqual(row["tokens"], 200)
        self.assertAlmostEqual(row["cost"], 0.0123)
        self.assertIn("| 001-feature | A small fixture feature | 180.0s |", sandbox._benchmark_markdown([row]))


if __name__ == "__main__":
    unittest.main()
