"""Non-git report contracts ported from v1 and extended by plan §7.2."""

import os
from datetime import datetime, timezone

import pytest

from agent_factory.engine.reports import (ReportError, extract_sections, parse_report,
                                          read_result, verdict)


@pytest.mark.parametrize("contract,text,expected", [
    ("spec", "# Feature\n", "READY"), ("status", "STATUS: READY\n", "READY"),
    ("status", "STATUS: BLOCKED\n", "BLOCKED"), ("verdict", "VERDICT: PASS\n", "PASS"),
    ("verdict", "VERDICT: CHANGES\n", "CHANGES"), ("decision", "APPROVE\n", "APPROVE"),
    ("decision", "ESCALATE\n", "ESCALATE"), ("status", " STATUS: READY \r\n", "READY"),
])
def test_first_line_contracts(contract, text, expected):
    assert verdict(text, contract) == expected


@pytest.mark.parametrize("contract,text", [
    ("status", ""), ("status", "\nSTATUS: READY"), ("status", "STATUS: READY later"),
    ("status", "READY"), ("status", "VERDICT: PASS"), ("verdict", "VERDICT: MAYBE"),
    ("verdict", "STATUS: READY"), ("decision", "APPROVED"), ("decision", "CHANGES"),
    ("spec", "## Feature"), ("spec", "#"), ("unknown", "APPROVE"),
])
def test_invalid_first_line_does_not_advance(contract, text):
    with pytest.raises(ReportError, match="first line"):
        verdict(text, contract)


def test_approval_changes_requires_on_changes():
    assert verdict("CHANGES\n", "decision", on_changes=True) == "CHANGES"
    with pytest.raises(ReportError, match="first line"):
        verdict("CHANGES\n", "decision")


@pytest.mark.parametrize("launch", [1000.0, "1970-01-01T00:16:40Z",
                                     datetime.fromtimestamp(1000, timezone.utc)])
def test_fresh_report_and_missing_report(tmp_path, launch):
    path = tmp_path / "report.md"
    with pytest.raises(ReportError, match="cannot read"):
        read_result(path, "status", t_launch=launch)
    path.write_text("STATUS: READY\n\n## Changes\n- app.py\n", encoding="utf-8-sig")
    os.utime(path, ns=(1001_000_000_000, 1001_000_000_000))
    report = read_result(path, "status", t_launch=launch)
    assert report.verdict == "READY"
    assert report.sections == {"Changes": "- app.py"}


@pytest.mark.parametrize("mtime", [999_000_000_000, 1000_000_000_000])
def test_stale_or_equal_mtime_is_rejected(tmp_path, mtime):
    path = tmp_path / "report.md"
    path.write_text("STATUS: READY\n", encoding="utf-8")
    os.utime(path, ns=(mtime, mtime))
    with pytest.raises(ReportError, match="fresh report"):
        read_result(path, "status", t_launch=1000.0)


def test_report_must_be_utf8_and_launch_timezone_explicit(tmp_path):
    path = tmp_path / "report.md"
    path.write_bytes(b"\xff\xfe")
    with pytest.raises(ReportError, match="cannot read"):
        read_result(path, "status", t_launch=0.0)
    with pytest.raises(ReportError, match="timezone"):
        read_result(path, "status", t_launch=datetime(2026, 1, 1))


def test_extract_sections_for_all_builtin_handoffs():
    names = ["Acceptance criteria", "Pages to check", "Changes", "Decisions", "Acceptance",
             "Checks run", "Findings", "Reasons", "Follow-ups"]
    text = "STATUS: READY\n\n" + "\n".join(f"## {name}\nContent for {name}.\n" for name in names)
    assert extract_sections(text) == {name: f"Content for {name}." for name in names}


def test_sections_ignore_fenced_headings_and_preserve_subsections():
    text = ("APPROVE\r\n## Reasons ##\r\nWorks.\r\n### Evidence\r\n"
            "```markdown\r\n## Fake\r\n```\r\n~~~\r\n## Also fake\r\n~~~\r\n"
            "## Follow-ups\r\nNone.\r\n# Appendix\r\nOutside sections.\r\n")
    sections = extract_sections(text)
    assert set(sections) == {"Reasons", "Follow-ups"}
    assert "### Evidence" in sections["Reasons"] and "## Fake" in sections["Reasons"]
    assert sections["Follow-ups"] == "None."


def test_custom_roles_can_omit_sections_and_repeat_sections():
    assert parse_report("VERDICT: PASS\n", "verdict").sections == {}
    assert extract_sections("## Findings\nOne.\n## Findings\nTwo.\n") == {"Findings": "One.\nTwo."}
