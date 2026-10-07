"""홀드아웃 누출 청소기 — 해시가 못 잡는 것을 잡고, 상투어는 지우지 않는가.

왜 이 시험이 있는가(2026-09-05). 청소기는 본문 **해시**로만 걸렀다. 해시는 본문 전체를
잡으므로 같은 문서가 길이를 달리해 들어가면 갈린다. 실측으로 걸린 것:

    holdout_eval.jsonl #56  vs  labeled_p1_v5_clean/train.jsonl #1933
      둘 다 「두산중공업 경업금지가처분 사건」 · 문자 유사도 0.855
      6,000자(절단) 대 8,026자 → 해시 다름 → 옛 게이트 통과
      정규화 문장 51/51 일치 · 라벨은 S1 대 TS

반대 방향도 똑같이 중요하다 — **상투어까지 지우면 안 된다.** 판결문은 맺음말이 정형이라
무관한 사건끼리도 문장을 공유하고, 인용 서식은 숫자가 # 로 뭉개지면서 거의 같아진다.
초판 문턱(비율 0.30 또는 희소문장 3개)으로는 실측에서 오검출 4건이 나왔다.

문턱은 눈으로 확인한 참 6건·거짓 4건으로 잡았다:
    참(같은 원본)  공유 8~51개 · 비율 80~100%
    거짓(상투어)   공유  1~2개 · 비율 17~ 33%
"""
from __future__ import annotations

import json

from scripts.clean_holdout_leakage import TrainIndex, leak_reason, sha, text_of

# 실제 판결문 맺음말 — 수많은 사건에 그대로 나온다. 이것만 겹치는 것은 누출이 아니다.
_BOILERPLATE = (
    "그러므로 상고를 기각하고, 상고 소송비용은 패소자의 부담으로 하여 "
    "관여법관의 일치된 의견으로 주문과 같이 판결한다."
)
_CITATION = "선고 #도# 판결(공#상, #), 대법원 #."


def _doc(doc_id: str, sentences: list[str], label: str = "S3") -> dict:
    return {"doc_id": doc_id, "label": label, "text": " ".join(sentences)}


def _sentence(i: int) -> str:
    """정규화가 살려 두는 길이(25자 이상)의 고유 문장."""
    return "이 사건 기술자료 %s 항목의 검토 결과와 산정 근거를 정리한 내용이다." % chr(ord("가") + i)


def _index(rows):
    return TrainIndex(rows), {sha(text_of(r)) for r in rows}


def _judge(rec, idx, hashes, *, min_shared=3, min_ratio=0.60):
    return leak_reason(rec, hashes, idx, min_shared=min_shared, min_ratio=min_ratio)


# ── 해시 축 — 옛 동작이 그대로 살아 있는가 ──────────────────────────────────
def test_identical_text_still_caught_by_hash():
    train = [_doc("t1", [_sentence(0), _sentence(1)])]
    idx, hashes = _index(train)
    same = _doc("h1", [_sentence(0), _sentence(1)])
    hit = _judge(same, idx, hashes)
    assert hit is not None and hit[0] == "text_hash"


def test_hash_only_mode_skips_sentence_axis():
    """--hash-only 는 index=None 으로 부른다 — 문장 축이 꺼진다."""
    train = [_doc("t1", [_sentence(i) for i in range(10)])]
    _idx, hashes = _index(train)
    truncated = _doc("h1", [_sentence(i) for i in range(8)])
    assert _judge(truncated, None, hashes) is None


# ── 문장 축 — 해시가 놓치던 것 ──────────────────────────────────────────────
def test_truncated_copy_is_caught():
    """길이만 달라 해시가 갈린 같은 문서 — 두산중공업 사건이 이 모양이었다."""
    train = [_doc("t1", [_sentence(i) for i in range(12)])]
    idx, hashes = _index(train)
    truncated = _doc("h1", [_sentence(i) for i in range(9)])   # 9/9 공유 · 비율 100%
    hit = _judge(truncated, idx, hashes)
    assert hit is not None and hit[0] == "same_source"
    assert "t1" in hit[1]          # 어느 학습 문서와 겹치는지 사유에 남는다


def test_reverse_direction_is_caught():
    """홀드아웃이 더 길어도 잡는다 — 비율을 양방향의 큰 쪽으로 본다."""
    train = [_doc("t1", [_sentence(i) for i in range(6)])]
    idx, hashes = _index(train)
    longer = _doc("h1", [_sentence(i) for i in range(6)] + ["평가용 꼬리 문장 %d 입니다 여기에 덧붙인 내용." % i for i in range(20)])
    hit = _judge(longer, idx, hashes)
    assert hit is not None and hit[0] == "same_source"


# ── 지우면 안 되는 것 ───────────────────────────────────────────────────────
def test_boilerplate_only_is_not_a_leak():
    """맺음말만 겹치는 무관한 사건은 남긴다. 지우면 홀드아웃이 반토막 난다."""
    train = [_doc("t%d" % i, [_sentence(i), _BOILERPLATE]) for i in range(5)]
    idx, hashes = _index(train)
    other_case = _doc("h1", [_sentence(90), _sentence(91), _sentence(92),
                             _sentence(93), _BOILERPLATE])
    assert _judge(other_case, idx, hashes) is None


def test_citation_format_only_is_not_a_leak():
    """숫자가 # 로 뭉개진 인용 서식만 겹치는 것도 누출이 아니다."""
    train = [_doc("t%d" % i, [_sentence(i), _CITATION]) for i in range(5)]
    idx, hashes = _index(train)
    other = _doc("h1", [_sentence(80), _sentence(81), _sentence(82), _CITATION])
    assert _judge(other, idx, hashes) is None


def test_short_document_needs_enough_shared_sentences():
    """문장 3개짜리에서 1개 겹친 것(33%)으로 지우지 않는다 — 비율이 불안정하다."""
    train = [_doc("t1", [_sentence(0), _sentence(1), _sentence(2), _sentence(3)])]
    idx, hashes = _index(train)
    short = _doc("h1", [_sentence(0), _sentence(50), _sentence(51)])   # 공유 1 < 3
    assert _judge(short, idx, hashes) is None


def test_shared_below_ratio_is_not_a_leak():
    """공유 수는 넘어도 **양쪽 다** 비율이 낮으면 남긴다.

    ⚠ 비율은 양방향의 큰 쪽이라, 학습 문서가 짧으면 그쪽 비율이 높아 정당하게 걸린다
      (4문장 중 3문장이 홀드아웃에 있으면 그 학습 문서는 75% 가 새어 나간 것이다).
      "비율이 낮다"를 시험하려면 **양쪽 다 길어야** 한다.
    """
    train = [_doc("t1", [_sentence(i) for i in range(30)])]
    idx, hashes = _index(train)
    long_doc = _doc("h1", [_sentence(0), _sentence(1), _sentence(2)]
                    + [_sentence(60 + i) for i in range(20)])   # 3/23 · 3/30 둘 다 낮다
    assert _judge(long_doc, idx, hashes) is None


# ── 문턱 조정이 실제로 먹는가 ───────────────────────────────────────────────
def test_thresholds_are_adjustable():
    train = [_doc("t1", [_sentence(i) for i in range(10)])]
    idx, hashes = _index(train)
    partial = _doc("h1", [_sentence(0), _sentence(1), _sentence(2)]
                   + [_sentence(70 + i) for i in range(4)])      # 3/7 = 43%
    assert _judge(partial, idx, hashes) is None                  # 기본 0.60 에는 안 걸린다
    assert _judge(partial, idx, hashes, min_ratio=0.40) is not None


# ── CLI ─────────────────────────────────────────────────────────────────────
def test_cli_dry_run_writes_nothing(tmp_path, monkeypatch, capsys):
    import scripts.clean_holdout_leakage as mod

    monkeypatch.setattr(mod, "ROOT", tmp_path)
    train = tmp_path / "train.jsonl"
    hold = tmp_path / "hold.jsonl"
    train.write_text(json.dumps(_doc("t1", [_sentence(i) for i in range(12)]),
                                ensure_ascii=False) + "\n", encoding="utf-8")
    hold.write_text(json.dumps(_doc("h1", [_sentence(i) for i in range(9)]),
                               ensure_ascii=False) + "\n", encoding="utf-8")

    code = mod.main(["--train", "train.jsonl", "--holdout", "hold.jsonl",
                     "--dry-run", "--strict"])
    out = capsys.readouterr().out
    assert "누출 1 제거" in out
    assert "dry-run" in out
    assert not (tmp_path / "hold.clean.jsonl").exists(), "dry-run 이 파일을 썼다"
    assert code == 1, "--strict 는 누출이 있으면 exit 1"


def test_cli_writes_clean_and_reason_file(tmp_path, monkeypatch):
    import scripts.clean_holdout_leakage as mod

    monkeypatch.setattr(mod, "ROOT", tmp_path)
    (tmp_path / "train.jsonl").write_text(
        json.dumps(_doc("t1", [_sentence(i) for i in range(12)]), ensure_ascii=False) + "\n",
        encoding="utf-8")
    (tmp_path / "hold.jsonl").write_text(
        json.dumps(_doc("h1", [_sentence(i) for i in range(9)]), ensure_ascii=False) + "\n"
        + json.dumps(_doc("h2", [_sentence(90 + i) for i in range(9)]), ensure_ascii=False) + "\n",
        encoding="utf-8")

    assert mod.main(["--train", "train.jsonl", "--holdout", "hold.jsonl"]) == 0
    clean = [json.loads(x) for x in (tmp_path / "hold.clean.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    assert [r["doc_id"] for r in clean] == ["h2"]
    # 사유가 함께 남는다 — 목록만 남기면 왜 뺐는지를 잃는다.
    reasons = (tmp_path / "hold.leaked_ids.txt").read_text(encoding="utf-8")
    assert "h1" in reasons and "same_source" in reasons


def test_cli_reports_no_leak_when_independent(tmp_path, monkeypatch, capsys):
    import scripts.clean_holdout_leakage as mod

    monkeypatch.setattr(mod, "ROOT", tmp_path)
    (tmp_path / "train.jsonl").write_text(
        json.dumps(_doc("t1", [_sentence(i) for i in range(8)]), ensure_ascii=False) + "\n",
        encoding="utf-8")
    (tmp_path / "hold.jsonl").write_text(
        json.dumps(_doc("h1", [_sentence(60 + i) for i in range(8)]), ensure_ascii=False) + "\n",
        encoding="utf-8")

    assert mod.main(["--train", "train.jsonl", "--holdout", "hold.jsonl", "--strict"]) == 0
    assert "누출 없음" in capsys.readouterr().out
