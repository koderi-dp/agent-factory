import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import sandbox
from test_engine_gitops import repo, add_job


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
                '"total_cost_usd":0.0123}\n',
                encoding="utf-8")

            row = sandbox.benchmark_rows(root)[0]

        self.assertEqual(row["id"], "001-feature")
        self.assertAlmostEqual(row["wall"], 180)
        self.assertEqual(row["stages"], {"spec": 30, "build": 90, "review": 60})
        self.assertEqual(row["agents"], 4)
        self.assertEqual(row["tokens"], 200)
        self.assertAlmostEqual(row["cost"], 0.0123)
        self.assertIn("| 001-feature | A small fixture feature | 180.0s |",
                      sandbox._benchmark_markdown([row]))


def engine_evidence(repo):
    root, connection, _, _ = repo
    add_job(repo, change=False)
    connection.execute("UPDATE jobs SET created_at='2026-01-01T00:00:00Z', "
                       "updated_at='2026-01-01T00:01:40Z'")
    execution = connection.execute("SELECT id FROM executions LIMIT 1").fetchone()[0]
    for n, start, end, first, validated, tokens in [(1, 10, 30, 12, 32, 10),
                                                    (2, 40, 70, 43, 74, 20)]:
        stamp = lambda seconds: f"2026-01-01T00:{seconds // 60:02}:{seconds % 60:02}Z"
        connection.execute(
            "INSERT INTO attempts (execution_id,n,kind,state,owner_token,dir,t_launch,t_exit,"
            "t_first_event,t_validated,packet_bytes,usage) VALUES (?,?, 'fresh','valid',?,?, ?,?,?,?,?,?)",
            (execution, n, str(n), str(n), stamp(start), stamp(end), stamp(first), stamp(validated),
             n * 100,
             json.dumps({
                 "input": tokens,
                 "cached_input": n * 4,
                 "output": n + 1,
                 "cost_usd": None,
                 "raw": [{
                     "input_tokens": tokens
                 }]
             })))
    return root, connection


def test_engine_metrics_and_read_only_evidence(repo):
    root, connection = engine_evidence(repo)
    before = list(connection.iterdump())
    row = sandbox.benchmark_rows(root)[0]
    assert list(connection.iterdump()) == before
    assert row["wall"] == 100
    assert row["stages"] == {"build": 50}
    assert row["agents"] == 2
    assert row["process"] == 50 and row["overhead"] == 50
    assert row["startup"] == [2, 3] and row["validation"] == [2, 4]
    assert row["tokens"] == 35  # cached input is not counted twice
    assert row["cached_input"] == 12 and row["cost"] is None
    assert row["packet_bytes"] == 300
    assert row["profiles"] == [("codex", "fake", "low")]
    assert sandbox.benchmark_rows(root.parent) == [row]


def test_missing_metrics_are_unknown_and_parallel_overhead_is_not_clamped(repo):
    root, connection = engine_evidence(repo)
    connection.execute(
        "UPDATE attempts SET usage=NULL,packet_bytes=NULL,t_first_event=NULL WHERE n=2")
    row = sandbox.benchmark_rows(root)[0]
    assert row["tokens"] is row["cached_input"] is row["packet_bytes"] is None
    assert row["startup"] == [2, None]
    connection.execute("UPDATE jobs SET updated_at='2026-01-01T00:00:40Z'")
    assert sandbox.benchmark_rows(root)[0]["overhead"] == -10
    connection.execute("UPDATE jobs SET state='running'")
    connection.execute("UPDATE attempts SET t_exit=NULL WHERE n=2")
    row = sandbox.benchmark_rows(root)[0]
    assert row["wall"] is row["overhead"] is row["process"] is None
    assert row["stages"] == {"build": None}
    assert "unknown" in sandbox._engine_markdown([row])


def test_comparison_pairs_and_append_preserves_baseline(repo, tmp_path, capsys):
    root, _ = engine_evidence(repo)
    fast = sandbox.benchmark_rows(root)[0]
    full = {**fast, "pipeline": "full", "tokens": 100, "wall": 200}
    table = sandbox._comparison_markdown([fast, full])
    assert "50.0%" in table and "35.0%" in table
    assert "35.0%" not in sandbox._comparison_markdown([fast, {**full, "state": "paused"}])
    assert "35.0%" not in sandbox._comparison_markdown([fast, full, full])
    assert "35.0%" not in sandbox._comparison_markdown(
        [fast, {
            **full, "profiles": [("codex", "other", "low")]
        }])
    target = tmp_path / "docs/benchmarks.md"
    target.parent.mkdir()
    baseline = "# Benchmarks\n\n## v1 baseline\n\nEvidence preserved.\n"
    target.write_text(baseline, encoding="utf-8")
    with patch.object(sandbox, "ROOT", tmp_path):
        sandbox.bench(root)
        first = target.read_text(encoding="utf-8")
        sandbox.bench(root, compare=[root])
    assert target.read_text(encoding="utf-8").startswith(first)
    assert first.startswith(baseline)
    assert first.count("### " + root.name) == 1
    assert "Fast vs Full" in capsys.readouterr().out
