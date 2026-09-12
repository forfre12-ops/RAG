"""길이 균형 홀드아웃 추출기 — 길이가 등급을 알려주지 않게 뽑는가.

왜 이 도구가 있는가(2026-09-05). 제출 수치가 걸린 홀드아웃 넷이 전부 계보 검사에서
usable_for_comparison=false 였고, 오염을 걷어내도 풀리지 않았다. 막고 있던 것은
**길이가 등급을 알려주는 것**이었다(Theil's U 0.28~0.51).

    hardened42  S3 7건이 2,323~3,023자 · 나머지는 151~317자 → 임계 1건으로 갈린다

구조적인 이유가 있다 — 공개 판결문은 길고, 이 사업의 규칙상 정의로 S3 다. 그래서 문서를
몇 건 빼는 것으로는 안 되고 등급마다 길이 분포가 겹치도록 다시 뽑아야 한다.

실측(v6 셋): Theil's U 0.328 → 0.074 · 길이-only 0.502 → 0.242(무작위 0.25 보다 낮다).
"""
from __future__ import annotations

import json


from scripts.build_balanced_holdout import MIN_PER_CELL, _bucket, main, pick

EDGES = [300, 600, 1000, 2000]
GRADES = ("TS", "S1", "S2", "S3")


def _doc(grade: str, length: int, seed: int = 0) -> dict:
    body = "사업 검토 자료 %d 항목의 내용을 정리한다. " % seed
    text = (body * (length // len(body) + 2))[:length]
    return {"label": grade, "text": text, "doc_id": "%s-%d-%d" % (grade, length, seed)}


def test_bucket_edges():
    assert _bucket(0, EDGES) == 0
    assert _bucket(299, EDGES) == 0
    assert _bucket(300, EDGES) == 1
    assert _bucket(1999, EDGES) == 3
    # 마지막 경계 이상은 쓰지 않는다 — 그 구간은 한 등급뿐이라 균형이 성립하지 않는다.
    assert _bucket(2000, EDGES) is None
    assert _bucket(9000, EDGES) is None


def test_equal_counts_per_grade_when_cap_is_one():
    pool = []
    for g, n in zip(GRADES, (10, 8, 12, 5)):
        pool += [_doc(g, 200, i) for i in range(n)]
    picked, diag = pick(pool, EDGES, ratio_cap=1.0)
    counts = {g: sum(1 for r in picked if r["label"] == g) for g in GRADES}
    assert set(counts.values()) == {5}, counts       # 가장 적은 등급에 맞춘다
    assert diag[0]["used"] is True


def test_ratio_cap_allows_more_samples():
    pool = []
    for g, n in zip(GRADES, (20, 20, 20, 6)):
        pool += [_doc(g, 200, i) for i in range(n)]
    small = pick(pool, EDGES, ratio_cap=1.0)[0]
    big = pick(pool, EDGES, ratio_cap=2.0)[0]
    assert len(big) > len(small)
    # 배수를 열어도 가장 적은 등급을 넘겨 뽑지는 않는다.
    assert sum(1 for r in big if r["label"] == "S3") == 6


def test_bucket_missing_a_grade_is_dropped_and_reported():
    """네 등급이 다 차지 않은 구간은 버린다 — 그리고 **버렸다고 말한다.**

    2,000자 이상이 S3 뿐인 것이 정확히 이 경우다. 조용히 빼면 "전 구간을 평가했다"로 읽힌다.
    """
    pool = [_doc("TS", 200, i) for i in range(5)] + [_doc("S1", 200, i) for i in range(5)]
    pool += [_doc("S3", 1500, i) for i in range(9)]     # 1k-2k 는 S3 뿐
    picked, diag = pick(pool, EDGES, ratio_cap=1.0)
    by_range = {d["range"]: d for d in diag}
    assert by_range["1000-2000"]["used"] is False
    assert by_range["1000-2000"]["taken"] == 0
    assert all(len(r["text"]) < 600 for r in picked)


def test_cell_below_minimum_is_not_used():
    pool = []
    for g in GRADES:
        pool += [_doc(g, 200, i) for i in range(5)]
    pool = [r for r in pool if not (r["label"] == "S3" and r["doc_id"].endswith(("1", "2", "3", "4")))]
    # S3 는 1건만 남는다 → MIN_PER_CELL 미만
    assert sum(1 for r in pool if r["label"] == "S3") < MIN_PER_CELL
    _picked, diag = pick(pool, EDGES, ratio_cap=1.0)
    assert diag[0]["used"] is False


def test_selection_is_deterministic():
    """같은 입력이면 같은 셋 — 시각·난수를 쓰지 않는다."""
    pool = []
    for g in GRADES:
        pool += [_doc(g, 200 + i, i) for i in range(9)]
    a = [r["doc_id"] for r in pick(pool, EDGES, ratio_cap=1.0)[0]]
    b = [r["doc_id"] for r in pick(list(reversed(pool)), EDGES, ratio_cap=1.0)[0]]
    assert sorted(a) == sorted(b)


def test_length_stops_predicting_grade():
    """길이로 등급이 갈리던 풀에서 뽑으면 Theil's U 가 내려간다 — 이 도구의 존재 이유."""
    from koipa.holdout_independence import assess

    # 등급마다 길이대가 완전히 갈린 풀(현행 홀드아웃의 모양)
    skewed = []
    for g, (lo, n) in zip(GRADES, ((150, 12), (400, 12), (800, 12), (1500, 12))):
        skewed += [_doc(g, lo + i * 5, i) for i in range(n)]
    # 각 등급이 모든 길이대에 있는 풀
    spread = []
    for g in GRADES:
        for base in (150, 400, 800, 1500):
            spread += [_doc(g, base + i * 5, i) for i in range(6)]

    train = [_doc("S2", 500, 900 + i) for i in range(30)]
    u_skewed = assess(train, skewed)["holdout_leakage"]["length_theils_u"]
    picked, _ = pick(spread, EDGES, ratio_cap=1.0)
    u_picked = assess(train, picked)["holdout_leakage"]["length_theils_u"]
    assert u_skewed > u_picked, (u_skewed, u_picked)
    assert u_picked <= 0.25, u_picked


def test_cli_dry_run_writes_nothing(tmp_path, capsys):
    train = tmp_path / "train.jsonl"
    pool = tmp_path / "pool.jsonl"
    out = tmp_path / "out.jsonl"
    train.write_text("".join(json.dumps(_doc("S2", 500, i), ensure_ascii=False) + "\n"
                             for i in range(20)), encoding="utf-8")
    rows = []
    for g in GRADES:
        rows += [_doc(g, 200 + i, 100 + i) for i in range(6)]
    pool.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")

    code = main(["--train", str(train), "--pool", str(pool), "--out", str(out), "--dry-run"])
    assert code == 0
    assert not out.exists(), "dry-run 이 파일을 썼다"
    text = capsys.readouterr().out
    assert "Theil's U" in text and "주장 한계" in text


def test_cli_writes_selected_rows(tmp_path):
    train = tmp_path / "train.jsonl"
    pool = tmp_path / "pool.jsonl"
    out = tmp_path / "out.jsonl"
    train.write_text("".join(json.dumps(_doc("S2", 500, i), ensure_ascii=False) + "\n"
                             for i in range(20)), encoding="utf-8")
    rows = []
    for g in GRADES:
        rows += [_doc(g, 200 + i, 100 + i) for i in range(6)]
    pool.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")

    assert main(["--train", str(train), "--pool", str(pool), "--out", str(out)]) == 0
    got = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(got) == 4 * 6
    assert {r["label"] for r in got} == set(GRADES)
