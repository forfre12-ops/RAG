from __future__ import annotations

import sys
from pathlib import Path

_POC = Path(__file__).resolve().parents[1]
if str(_POC / "scripts") not in sys.path:
    sys.path.insert(0, str(_POC / "scripts"))

import render_pilot_scorecard as scorecard  # noqa: E402


def test_render_pilot_scorecard_contains_core_sections():
    acceptance = {
        "verdict": "PASS",
        "n": 1,
        "veto_count": 0,
        "parse_fail": 0,
        "under_classified": 0,
        "needs_review": 0,
        "parser_by_format": {
            "txt": {"total": 1, "parse_fail": 0, "needs_review": 0, "veto": 0},
        },
        "results": [
            {
                "doc_id": "txt-sample",
                "format": "txt",
                "expected": "S1",
                "pred": "S1",
                "parse_ok": True,
                "numeric_recall": 1.0,
                "status": "staging",
                "veto_reasons": [],
            }
        ],
    }
    readiness = {
        "verdict": "CONDITIONALLY_READY",
        "gates": [{"name": "human_review", "status": "BLOCKED", "detail": "pending pilot"}],
    }

    html = scorecard.render(acceptance, readiness)

    assert "파일럿 스코어카드" in html
    assert "txt-sample" in html
    assert "human_review" in html
    assert "CONDITIONALLY_READY" in html


def test_main_writes_scorecard(tmp_path: Path, monkeypatch):
    acceptance = tmp_path / "acceptance.json"
    readiness = tmp_path / "readiness.json"
    out = tmp_path / "scorecard.html"
    acceptance.write_text(
        '{"verdict":"PASS","n":0,"parser_by_format":{},"results":[]}',
        encoding="utf-8",
    )
    readiness.write_text('{"verdict":"PASS","gates":[]}', encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "render_pilot_scorecard.py",
            "--acceptance",
            str(acceptance),
            "--readiness",
            str(readiness),
            "--out",
            str(out),
        ],
    )

    assert scorecard.main() == 0
    assert "파일럿 스코어카드" in out.read_text(encoding="utf-8")


def test_main_fail_on_bad_writes_then_returns_nonzero(tmp_path: Path, monkeypatch):
    acceptance = tmp_path / "acceptance.json"
    readiness = tmp_path / "readiness.json"
    out = tmp_path / "scorecard.html"
    acceptance.write_text(
        '{"verdict":"FAIL","n":1,"parser_by_format":{},"results":[]}',
        encoding="utf-8",
    )
    readiness.write_text('{"verdict":"CONDITIONALLY_READY","gates":[]}', encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "render_pilot_scorecard.py",
            "--acceptance",
            str(acceptance),
            "--readiness",
            str(readiness),
            "--out",
            str(out),
            "--fail-on-bad",
        ],
    )

    assert scorecard.main() == 1
    assert out.exists()
