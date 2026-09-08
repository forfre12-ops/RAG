"""골든 후보 본문이 **정답을 알려주지 않는가.**

왜 이 파일이 있는가(2026-09-08). 콘솔이 서빙하던 후보 988건 중 **964건(97.6%)** 의 본문
맨 끝에 이 절이 있었다:

    ## 등급 제안 사유: TS

그 표기는 실제 등급과 **100% 일치**했다. 문서를 전혀 안 읽고 그 한 줄만 봐도 97.6% 로
분류된다. 언급된 등급코드 집합만 봐도 **99.3%** 가 갈렸다:

    {TS,S1,S3} → TS    {S1,S3} → S1    {S2,S3} → S2    {S3} → S3

설계단계 감리(2026-08-31~09-04)가 바로 이 풀에서 28건을 뽑아 분류기를 시험했다.
정답이 적힌 문서로 잰 것이다.

⚠ 이 문제는 2026-08 에 한 번 풀렸다 — `build_kl_review_pool.py` 가 같은 스캐폴딩을
  걷어내 223 의 검수 배치(kl-ff5a822c)를 만들었다. 그런데 **콘솔이 서빙하는 풀은 정리되지
  않았다.** 깨끗한 것과 안 깨끗한 것이 두 벌 있었고, 감리에게 안 깨끗한 쪽이 갔다.
  그래서 "고쳤다"로 끝내지 않고 시험으로 못박는다.

이 시험은 파일이 없는 환경(CI·클론 직후)에서는 건너뛴다 — 후보 풀은 gitignore 다.
"""
from __future__ import annotations

import json
import pathlib
import re
from collections import Counter, defaultdict

import pytest

_POC = pathlib.Path(__file__).resolve().parents[1]
_ROOT = _POC / "datasets" / "proxy_gold" / "single_document_candidates"

_ANSWER_HEAD = re.compile(r"##\s*등급\s*제안\s*사유\s*:\s*(TS|S1|S2|S3)")
_GRADE_IN_ID = re.compile(r"-(TS|S1|S2|S3)-")
_TOK = re.compile(r"\b(TS|S1|S2|S3)\b")

pytestmark = pytest.mark.skipif(
    not _ROOT.is_dir() or not any(_ROOT.glob("*.metadata.json")),
    reason="후보 풀이 없는 환경 — datasets/ 는 gitignore 다",
)


def _served_bodies() -> list[tuple[str, str]]:
    """콘솔이 실제로 내보내는 본문 — `content_revision_path` 가 있으면 그쪽이다.

    서비스와 같은 우선순위를 쓴다(proxy_gold_candidate_service). 원본 .md 만 보면
    정리본이 붙었는지 알 수 없다.
    """
    out: list[tuple[str, str]] = []
    for mp in sorted(_ROOT.glob("*.metadata.json")):
        try:
            meta = json.loads(mp.read_text("utf-8"))
        except Exception:  # noqa: BLE001
            continue
        doc_id = str(meta.get("doc_id") or "")
        gm = _GRADE_IN_ID.search(doc_id)
        if not gm:
            continue
        rev = str(meta.get("content_revision_path") or "").strip()
        src = (_ROOT / rev) if rev else None
        if src is None or not src.is_file():
            stem = mp.name.replace(".metadata.json", "")
            cands = [p for p in sorted(_ROOT.glob(stem + "*.md"))
                     if not p.name.endswith(".cleaned.md")]
            if len(cands) != 1:
                continue
            src = cands[0]
        try:
            out.append((gm.group(1), src.read_text("utf-8")))
        except OSError:
            continue
    return out


def test_no_candidate_prints_its_own_grade():
    """'## 등급 제안 사유: <등급>' 이 본문에 있으면 검수자도 모델도 정답을 먼저 본다."""
    bad = [g for g, body in _served_bodies() if _ANSWER_HEAD.search(body)]
    assert not bad, (
        "본문에 정답이 적힌 후보 %d건 — scripts/clean_candidate_answer_leak.py 를 돌릴 것"
        % len(bad)
    )


def test_grade_mentions_do_not_determine_the_grade():
    """본문에 언급된 등급코드 **집합**만으로 등급을 맞힐 수 있으면 안 된다.

    2026-09-08 실측: 정리 전 99.3% · 정리 후 30.3%(4등급 무작위 25%).
    상한을 넉넉히 45% 로 둔다 — 다수 등급 쏠림만으로 그 위로 가기는 어렵다.
    """
    pairs = _served_bodies()
    assert pairs, "후보를 하나도 못 읽었다 — 이 시험이 아무것도 안 지키고 있다"
    sig: dict[tuple, Counter] = defaultdict(Counter)
    for grade, body in pairs:
        key = tuple(c for c in ("TS", "S1", "S2", "S3") if re.search(r"\b%s\b" % c, body))
        sig[key][grade] += 1
    ceiling = sum(c.most_common(1)[0][1] for c in sig.values()) / len(pairs) * 100
    assert ceiling < 45.0, (
        "등급코드 언급만으로 %.1f%% 를 맞힐 수 있다 — 본문이 정답을 흘린다" % ceiling
    )


def test_length_does_not_tell_the_grade():
    """정리하다가 길이가 새 단서가 되면 안 된다.

    2026-09-08 실측(정리 후 중앙값): TS 2,214 · S1 2,173 · S2 2,144 · S3 2,155.
    """
    import statistics as st

    by = defaultdict(list)
    for grade, body in _served_bodies():
        by[grade].append(len(body))
    med = {g: st.median(v) for g, v in by.items() if v}
    assert len(med) == 4, "등급 4종이 다 있어야 한다: %s" % sorted(med)
    lo, hi = min(med.values()), max(med.values())
    assert hi / max(lo, 1) < 1.6, (
        "등급별 길이 중앙값이 %.1f배 벌어졌다 — 길이가 등급 단서가 된다: %s"
        % (hi / max(lo, 1), {k: int(v) for k, v in med.items()})
    )


def test_generator_does_not_concatenate_the_rationale_into_the_body():
    """생성기가 등급 사유를 **본문에 이어 붙이면** 안 된다 — metadata 가 그 자리다.

    [2026-09-08] 종전 코드:

        document = _case_specific_appendix(case) + _grade_rationale(case)
                                                   ^^^^^^^^^^^^^^^^^^^^^ 정답이 여기서 붙었다

    이 계열로 만든 964건 전부가 본문에 정답을 달고 있었고, 그 표기는 실제 등급과 100%
    일치했다. 감리는 **골든셋 1,000건 확대**를 권고했다 — 고치지 않으면 1,000건이 된다.

    사유 자체는 필요하다. 없애는 것이 아니라 metadata 로 자리를 옮기는 것이 이 시험의 취지다.
    """
    gen = _POC / "scripts" / "build_proxy_gold_pilot_100.py"
    if not gen.is_file():
        pytest.skip("생성기가 없는 환경")
    src = gen.read_text("utf-8")

    body_asm = re.search(r"document\s*=\s*_repair_mojibake\((.*?)\)\)", src, re.S)
    assert body_asm, "본문 조립 지점을 못 찾았다 — 이 시험이 아무것도 안 지키고 있다"
    assert "_grade_rationale" not in body_asm.group(1), (
        "생성기가 등급 사유를 본문에 이어 붙인다 — metadata 로 옮길 것"
    )
    assert '"grade_rationale": rationale' in src, (
        "사유를 metadata 에 안 남기면 검수자가 근거를 잃는다"
    )
