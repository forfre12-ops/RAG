"""사람 판정 집계 도구(scripts/analyze_regulation_judgments.py) — 셈이 맞는가.

왜 있나. 이 도구의 숫자가 "쓸만한가"의 결론이 된다. 도움·오도 건수·다수결·κ·AUROC 가 틀리면 결론이 틀린다.
작은 손계산 사례로 각 셈을 잠근다.
"""

from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "analyze_regulation_judgments.py"
_spec = importlib.util.spec_from_file_location("analyze_regulation_judgments", _SCRIPT)
tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tool)


def _sig(dense: float, rank: int = 0) -> dict:
    return {"dense_shown": dense, "dense_max": 0.9, "dense_gap_to_second": 0.1, "dense_rank": rank,
            "lex_shown": dense, "lex_max": 1.0, "lex_rank": rank, "sentence_cos": dense}


def _dump(n: int = 6) -> list[dict]:
    return [{"doc_id": f"D{i}", "hit1": i % 2 == 0, "item": {"article_no": "제1조" if i < 4 else "제2조"},
             "signals": _sig(0.5 + i / 20)} for i in range(n)]


def _write(tmp_path: Path, name: str, verdicts: dict) -> Path:
    p = tmp_path / f"{name}.json"
    p.write_text(json.dumps(verdicts, ensure_ascii=False), encoding="utf-8")
    return p


def test_kappa_is_one_for_identical_zero_at_chance_and_negative_when_opposed() -> None:
    a = list("HHUUMM")
    assert tool.kappa(a, a) == 1.0
    # 겹친 것 2/6 = 우연히 겹칠 몫(3 × 1/3 × 1/3 = 1/3) → 0
    assert tool.kappa(a, list("HUMHUM")) == pytest.approx(0.0)
    # 겹친 것 3/6, 우연 1/3 → (1/2 − 1/3) / (1 − 1/3) = 0.25
    assert tool.kappa(a, list("HUUMMH")) == pytest.approx(0.25)
    assert tool.kappa(list("HHUU"), list("UUHH"), "HU") == -1.0


def test_auroc_counts_ties_as_half() -> None:
    assert tool.auroc([3.0, 4.0], [1.0, 2.0]) == 1.0
    assert tool.auroc([1.0], [1.0]) == 0.5
    assert tool.auroc([1.0, 3.0], [2.0]) == 0.5
    assert math.isnan(tool.auroc([], [1.0]))


def test_majority_returns_tie_marker_when_top_two_are_equal() -> None:
    assert tool.majority(["H", "H", "U"]) == "H"
    assert tool.majority(["H", "U", "M"]) == tool.TIE
    assert tool.majority(["U", "U"]) == "U"


def test_load_verdicts_reads_all_three_formats_and_rejects_bad_ones(tmp_path: Path) -> None:
    flat = {str(i): "Hc" for i in range(3)}
    assert tool.load_verdicts(_write(tmp_path, "a", flat), 3) == {0: "Hc", 1: "Hc", 2: "Hc"}
    rich = {str(i): {"v": "Ub", "why": "x"} for i in range(3)}
    assert tool.load_verdicts(_write(tmp_path, "b", rich), 3) == {0: "Ub", 1: "Ub", 2: "Ub"}
    wrapped = {"note": "메모", "verdicts": flat}
    assert tool.load_verdicts(_write(tmp_path, "c", wrapped), 3)[2] == "Hc"
    with pytest.raises(ValueError, match="2건"):
        tool.load_verdicts(_write(tmp_path, "d", {"0": "Hc", "1": "Xc", "2": "H"}), 3)         # 형식 오류 2건
    with pytest.raises(ValueError, match="1건"):
        tool.load_verdicts(_write(tmp_path, "e", {"0": "Hc", "1": "Hc"}), 3)                   # 누락 1건


def test_main_tallies_readers_majority_and_agreement(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    dump = tmp_path / "dump.json"
    dump.write_text(json.dumps(_dump()), encoding="utf-8")
    x = _write(tmp_path, "x", {"0": "Hc", "1": "Hb", "2": "Uc", "3": "Uc", "4": "Mb", "5": "Uc"})
    y = _write(tmp_path, "y", {"0": "Hc", "1": "Uc", "2": "Uc", "3": "Ub", "4": "Mb", "5": "Mb"})
    z = _write(tmp_path, "z", {"0": "Hb", "1": "Hc", "2": "Uc", "3": "Uc", "4": "Ub", "5": "Uc"})

    assert tool.main(["--dump", str(dump), "--readers", str(x), str(y), str(z)]) == 0
    out = capsys.readouterr().out

    assert "x: 도움 2(33%, 분명 1) · 무용 3(50%, 분명 3) · 오도 1(17%, 분명 0)" in out
    assert "y: 도움 1(17%, 분명 1) · 무용 3(50%, 분명 2) · 오도 2(33%, 분명 0)" in out
    # 다수결: 0=H(3표) 1=H(2표) 2=U 3=U 4=M(2표: x·y) 5=U(2표: x·z) → 도움 2 · 무용 3 · 오도 1
    assert "[다수결 3명] 도움 2(33%) · 무용 3(50%) · 오도 1(17%) · 동률 0" in out
    assert "x-y: 일치 4/6" in out                     # 0·2·3·4 가 같다
    assert "모두 같은 문서 3/6" in out                # 세 명이 같은 문서 = 0(H·H·H) · 2(U·U·U) · 3(U·U·U)
    assert "도움: 모두 1건 · 한 명이라도 2건" in out  # 모두 도움 = 0, 누구라도 도움 = 0·1
    assert "제1조: 4건 · 도움 2 · 무용 2 · 오도 0" in out  # 문서 0~3 — 0·1 도움, 2·3 무용
    assert "제2조: 2건 · 도움 0 · 무용 1 · 오도 1" in out  # 문서 4·5 — 4 오도, 5 무용
    assert "AUROC" in out and "문턱 모의" in out


def test_main_rejects_an_incomplete_verdict_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    dump = tmp_path / "dump.json"
    dump.write_text(json.dumps(_dump(4)), encoding="utf-8")
    short = _write(tmp_path, "short", {"0": "Hc", "1": "Uc"})
    assert tool.main(["--dump", str(dump), "--readers", str(short)]) == 2
    assert "⛔" in capsys.readouterr().out


def test_main_runs_with_a_dump_without_scores_or_hits(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """점수·정답 조항이 없는 dump 도 도움·무용·오도 집계는 낸다(파일럿에서 규정만 바꿔 쓸 수 있게)."""
    bare = [{"doc_id": f"D{i}", "item": {"article_no": "제3조"}} for i in range(3)]
    dump = tmp_path / "dump.json"
    dump.write_text(json.dumps(bare), encoding="utf-8")
    r = _write(tmp_path, "r", {"0": "Hc", "1": "Uc", "2": "Mb"})
    assert tool.main(["--dump", str(dump), "--readers", str(r)]) == 0
    out = capsys.readouterr().out
    assert "r: 도움 1(33%, 분명 1) · 무용 1(33%, 분명 1) · 오도 1(33%, 분명 0)" in out
    assert "AUROC" not in out
