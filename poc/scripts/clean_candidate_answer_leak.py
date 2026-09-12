#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""골든 후보 본문에 **정답이 적혀 있던 것**을 걷어낸다 — 비파괴.

무엇이 있었나(2026-09-08 실측). 콘솔이 서빙하는 후보 988건 중 **964건(97.6%)** 의 본문
맨 끝에 이 절이 있다:

    ## 등급 제안 사유: TS
    ## 등급 제안 사유: S1   …

그 표기는 실제 등급과 **100% 일치**한다. 즉 문서를 전혀 안 읽고 이 한 줄만 봐도
97.6% 로 분류된다. 언급된 등급코드 집합만으로도 **99.3%** 가 갈린다:

    {TS,S1,S3} → TS    {S1,S3} → S1    {S2,S3} → S2    {S3} → S3

왜 문제인가. ① 이 풀로 학습하면 모델이 그 줄을 읽는 법만 배운다. ② 검수자가 본문을
읽기 전에 정답을 본다. ③ 설계단계 감리(2026-08-31~09-04)가 이 풀에서 28건을 뽑아
분류기를 시험했다 — 정답이 적힌 문서로 잰 것이다.

⚠ 계열별로 갈린다. 배치 생성기가 만든 B1·B2·B3·PILOT 은 **전건 100% 노출**,
  손으로 만든 CAND 24건은 0%.

■ 이미 있던 도구를 쓴다

`scripts/build_kl_review_pool.py` 가 2026-08 에 같은 문제를 풀었다("어제 저작한 1,000건에서
검수 스캐폴딩(등급 제안 사유·검수 지시)을 제거해 업무문서 본체만 남김"). 그 결과물이 223 의
검수 배치 kl-ff5a822c 다 — 그건 깨끗하다. **콘솔이 서빙하는 후보 풀만 정리가 안 됐다.**
깨끗한 것과 안 깨끗한 것이 두 벌 있었고, 감리에게 안 깨끗한 쪽이 갔다.

여기서는 그 로직(strip_scaffolding · drop_grade_sentences)을 그대로 재사용한다.

■ 비파괴 — 원본을 손대지 않는다

    원본  GOLD-B1-TS-001_….md              그대로 둔다
    신규  revisions/GOLD-B1-TS-001.v4.md   정리본
    연결  metadata.content_revision_path    여기만 한 줄

콘솔은 `content_revision_path` 를 이미 우선해서 읽는다(proxy_gold_candidate_service.py).
289건이 이미 이 방식이라 **코드 변경이 필요 없다.** metadata 한 줄을 지우면 원복된다.

잘라낸 사유는 버리지 않고 `metadata.grade_rationale` 로 옮긴다 — 없애는 것이 아니라
**본문에서 검수 메타데이터로 자리를 옮기는 것**이다. 원래 거기 있어야 할 내용이다.

사용:
    python scripts/clean_candidate_answer_leak.py --dry     # 무엇이 바뀌는지만
    python scripts/clean_candidate_answer_leak.py           # 적용
    python scripts/clean_candidate_answer_leak.py --revert  # 원복
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_POC = _HERE.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# 2026-08 에 만든 정리 로직을 그대로 쓴다 — 같은 문제를 두 번 풀지 않는다.
# ⚠ build_kl_review_pool 은 임포트할 수 없다(모듈 수준에서 sys.argv 를 읽고 파이프라인
#   전체를 실행해 파일까지 쓴다). 그래서 순수 함수는 golden_scaffolding 으로 옮겨 뒀다.
from golden_scaffolding import clean_body  # noqa: E402

ROOT = _POC / "datasets" / "proxy_gold" / "single_document_candidates"
REV = ROOT / "revisions"
SUFFIX = ".v4.md"

_HEAD = re.compile(r"##\s*등급\s*제안\s*사유\s*:\s*(TS|S1|S2|S3)")
_GRADE_IN_ID = re.compile(r"-(TS|S1|S2|S3)-")
_TOK = re.compile(r"\b(TS|S1|S2|S3)\b")


def _body_path(meta_path: Path, doc_id: str) -> Path | None:
    """metadata 짝인 본문 파일. cleaned 는 파생본이라 원본으로 보지 않는다."""
    stem = meta_path.name.replace(".metadata.json", "")
    cands = [p for p in sorted(ROOT.glob(stem + "*.md")) if not p.name.endswith(".cleaned.md")]
    return cands[0] if len(cands) == 1 else None


def _rationale_of(text: str) -> str:
    """'## 등급 제안 사유' 절 전문 — 문서 맨 끝 절이다."""
    m = _HEAD.search(text)
    if not m:
        return ""
    nxt = re.search(r"^##\s", text[m.end():], re.M)
    end = m.end() + (nxt.start() if nxt else len(text) - m.end())
    return text[m.start():end].strip()


def _leak_ceiling(pairs: list[tuple[str, str]]) -> float:
    """언급된 등급코드 집합만으로 맞힐 수 있는 상한 — 지름길의 크기."""
    sig: dict[tuple, Counter] = defaultdict(Counter)
    for grade, body in pairs:
        key = tuple(c for c in ("TS", "S1", "S2", "S3") if re.search(r"\b%s\b" % c, body))
        sig[key][grade] += 1
    best = sum(c.most_common(1)[0][1] for c in sig.values())
    return best / max(len(pairs), 1) * 100


def main(argv=None) -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="골든 후보 정답 노출 제거 (비파괴)")
    ap.add_argument("--dry", action="store_true", help="쓰지 않고 결과만 보여준다")
    ap.add_argument("--revert", action="store_true", help="content_revision_path 를 걷어 원복")
    a = ap.parse_args(argv)

    metas = sorted(ROOT.glob("*.metadata.json"))
    print("=" * 74)
    print(" 골든 후보 정답 노출 제거   (metadata %d개)" % len(metas))
    print("=" * 74)

    if a.revert:
        n = 0
        for mp in metas:
            try:
                meta = json.loads(mp.read_text("utf-8"))
            except Exception:  # noqa: BLE001
                continue
            rev = str(meta.get("content_revision_path") or "")
            if not rev.endswith(SUFFIX):
                continue
            meta.pop("content_revision_path", None)
            meta.pop("grade_rationale", None)
            mp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            n += 1
        print("  원복: metadata %d건에서 연결을 걷었다. 정리본 파일은 revisions/ 에 남는다." % n)
        return 0

    before: list[tuple[str, str]] = []
    after: list[tuple[str, str]] = []
    plan = []
    fam = Counter()

    for mp in metas:
        try:
            meta = json.loads(mp.read_text("utf-8"))
        except Exception:  # noqa: BLE001
            continue
        doc_id = str(meta.get("doc_id") or "")
        gm = _GRADE_IN_ID.search(doc_id)
        if not gm:
            continue
        bp = _body_path(mp, doc_id)
        if bp is None:
            continue
        grade = gm.group(1)
        body = bp.read_text("utf-8")
        before.append((grade, body))
        if not _HEAD.search(body):
            after.append((grade, body))
            continue
        cleaned = clean_body(body)
        after.append((grade, cleaned))
        plan.append((mp, meta, doc_id, bp, body, cleaned))
        fam[doc_id.split("-")[1]] += 1

    print("  검사 %d건 · 정리 대상 %d건" % (len(before), len(plan)))
    print("  계열별:", dict(fam.most_common()))
    print()
    print("  지름길 상한 (등급코드 언급 집합만으로 맞힐 수 있는 비율)")
    print("     처리 전  %.1f%%" % _leak_ceiling(before))
    print("     처리 후  %.1f%%   (4등급 무작위 = 25%%)" % _leak_ceiling(after))
    if plan:
        lost = sum(len(b) - len(c) for _, _, _, _, b, c in plan) / len(plan)
        print("\n  본문 평균 %d자 줄어든다 (검수 스캐폴딩 · 등급 언급 문장)" % lost)

    if a.dry:
        print("\n  --dry — 아무것도 쓰지 않았다.")
        return 0

    REV.mkdir(parents=True, exist_ok=True)
    for mp, meta, doc_id, bp, body, cleaned in plan:
        out = REV / (doc_id + SUFFIX)
        out.write_text(cleaned, encoding="utf-8")
        meta["content_revision_path"] = out.relative_to(ROOT).as_posix()
        rat = _rationale_of(body)
        if rat:
            # 버리지 않는다 — 본문에서 검수 메타데이터로 자리를 옮긴다.
            meta["grade_rationale"] = rat
        mp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n  적용: 정리본 %d건 → %s" % (len(plan), REV.relative_to(_POC).as_posix()))
    print("  원본 .md 는 손대지 않았다. --revert 로 되돌린다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
