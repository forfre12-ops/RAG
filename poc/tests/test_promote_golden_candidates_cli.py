"""[G4 do_now rank12] promote_golden_candidates CLI 글루 검증.

코어 게이트(promote_candidates)는 test_golden_promote 가 커버. 여기서는 CLI 의
로드·dry-run·백업·정본 쓰기 글루를 검증한다(운영자 진입점).
"""
from __future__ import annotations

import json

from scripts import promote_golden_candidates as pgc


def _write_jsonl(path, records):
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )


def _cand(doc_id, label, text):
    return {"doc_id": doc_id, "label": label, "text": text, "label_source": "consensus"}


def test_dry_run_does_not_write(tmp_path, monkeypatch, capsys):
    gold = tmp_path / "classification_gold.jsonl"
    _write_jsonl(gold, [_cand("g1", "S2", "기존 문서")])
    cand = tmp_path / "build_x.jsonl"
    _write_jsonl(cand, [_cand("c1", "S1", "신규 후보 문서")])

    monkeypatch.setattr(
        pgc.sys, "argv",
        ["prog", str(cand), "--gold", str(gold), "--dry-run"],
    )
    assert pgc.main() == 0
    # dry-run → 정본 미변경(여전히 1건)
    rows = [json.loads(l) for l in gold.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(rows) == 1
    assert "DRY-RUN" in capsys.readouterr().out


def test_promote_writes_merged_with_backup(tmp_path, monkeypatch):
    gold = tmp_path / "classification_gold.jsonl"
    _write_jsonl(gold, [_cand("g1", "S2", "기존 문서")])
    cand = tmp_path / "build_x.jsonl"
    _write_jsonl(cand, [
        _cand("c1", "S1", "신규 후보 문서"),   # 추가됨
        _cand("c2", "S2", "기존 문서"),         # 본문 중복 → skip
        {"doc_id": "c3", "label": None, "text": "검수대상"},  # 비-gold → skip
    ])

    monkeypatch.setattr(pgc.sys, "argv", ["prog", str(cand), "--gold", str(gold)])
    assert pgc.main() == 0

    rows = [json.loads(l) for l in gold.read_text(encoding="utf-8").splitlines() if l.strip()]
    ids = {r["doc_id"] for r in rows}
    assert ids == {"g1", "c1"}                      # 1건만 추가(중복·비-gold 제외)
    # 백업 생성됨
    assert list(tmp_path.glob("classification_gold.jsonl.bak_*"))


def test_holdout_leak_skipped(tmp_path, monkeypatch):
    gold = tmp_path / "classification_gold.jsonl"
    _write_jsonl(gold, [])
    cand = tmp_path / "build_x.jsonl"
    _write_jsonl(cand, [_cand("c1", "S1", "누출 본문")])
    holdout = tmp_path / "holdout.jsonl"
    _write_jsonl(holdout, [{"text": "누출 본문"}])

    monkeypatch.setattr(
        pgc.sys, "argv",
        ["prog", str(cand), "--gold", str(gold), "--holdout", str(holdout)],
    )
    assert pgc.main() == 0
    rows = [l for l in gold.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert rows == []                               # holdout 누출 → 미병합
