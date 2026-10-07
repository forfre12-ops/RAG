#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""이미 만들어진 골든 후보 본문의 **어긋난 조사만** 고친다 — 비파괴.

무엇이 잘못돼 있었나(2026-09-12). 생성기 틀에 조사가 한 형태로 박혀 있었고, 채워 넣는 말의
받침은 문서마다 달랐다. 그래서 검수자와 감리가 읽는 본문에 이런 문장이 실려 나갔다:

    "…남아 있는지 확인가 확정되지 않은 상태에서 넓게 공유되면"      ← "확인이" 가 맞다
    "…조치 이력와 대조 결과와 분리해서 판단하지 않는다"             ← "이력과" 가 맞다

생성기는 커밋 `05a2728a` 로 고쳤지만 **이미 만든 1,055건은 그대로**다. 이 도구가 그것을 고친다.

■ 받침 규칙으로 일괄 교정하지 않는다 — 그러면 멀쩡한 말을 망친다

`scripts/scan_particle_mismatch.py` 전수 실측(후보 1,055건):

    어긋난 조사 3,498회 · 990건 · 형태 112개
    그중 3,312회(94.7%)가 **다섯 낱말 뒤**다 — 확인 2,122 · 이력 474 · 기록 427 · 승인 146 · 일정 143
    꼬리 186회에는 `재평가`·`릴레이`·`작용효과`·`주식회사`·`걸려있는` 처럼
    **조사가 아닌 글자**가 섞여 있다. 규칙만 믿고 바꾸면 "재평이"·"걸려있은"이 된다.

그래서 **그 다섯 낱말로 끝나는 자리만** 고친다. 그 다섯은 생성기가 문장에 끼워 넣은 말의
끝 낱말이고, 한국어에 `확인가`·`이력와` 로 끝나는 낱말은 없다. 나머지 꼬리는 손대지 않는다 —
공개 실문서(79건)에서 온 것이 섞여 있어 우리가 고칠 대상이 아니다.

■ 비파괴 — 지금 쓰이는 본문을 덮어쓰지 않는다

    지금       revisions/GOLD-….v4.md      (정답 노출 세척본)  그대로 둔다
    새로       revisions/GOLD-….v5.md      조사 교정본
    연결       metadata.content_revision_path 한 줄만 v5 로

콘솔·평가 코드는 `content_revision_path` 를 우선해서 읽는다. `--revert` 는 그 한 줄을 v4 로
되돌린다(v5 파일은 남는다). 원본 `.md` 는 어느 쪽으로도 손대지 않는다.

사용:
    python scripts/fix_candidate_particles.py --dry      # 무엇이 바뀌는지만
    python scripts/fix_candidate_particles.py            # 적용
    python scripts/fix_candidate_particles.py --revert   # 연결을 v4 로 되돌림
"""
from __future__ import annotations

import argparse
import collections
import io
import json
import re
import sys
from pathlib import Path

_POC = Path(__file__).resolve().parents[1]
ROOT = _POC / "datasets" / "proxy_gold" / "single_document_candidates"
REV = ROOT / "revisions"
SUFFIX = ".v5.md"

# 생성기가 문장에 끼워 넣은 말의 끝 낱말. 이 낱말로 끝나는 자리에서만 조사를 고친다.
# 늘릴 때는 scan_particle_mismatch.py 로 먼저 세고, **그 낱말+조사 가 다른 뜻의 한국어 낱말이
# 되지 않는지** 하나씩 확인할 것. 아래는 그렇게 걸러 남긴 것이다.
#
# ⛔ 일부러 뺀 것 — 넣으면 멀쩡한 말을 망친다:
#     재평(재평가) · 작용효(작용효과) · 급증(급증가) · 관리(관리과=부서명) · 기준(기준가=기준가격)
#     새로(새로이) · 가까(가까이) · 릴레(릴레이) · 것인(것인가) · 피티(피티이) · 푸로듀(푸로듀이)
#     그리고 `-는`으로 끝나는 **동사 어미** 전부(걸려있는 · 제출받는 · 관계없는 …) — 조사가 아니다.
STEM_TAILS = (
    # 1차(2026-09-12 적용, 3,313곳)
    "확인", "이력", "기록", "승인", "일정",
    # 2차 — 남은 꼬리에서 안전한 것만
    "충돌", "상충", "않음", "불명확", "누락", "요청", "의견", "불분명", "중첩", "다름",
    "적용됨", "있음", "개선안", "연결", "못함", "가능성", "요약", "역량", "전망",
    "수익배분안", "비용", "제한", "변동", "상승", "서명", "지속", "경합", "혼선",
    "질문", "도움말", "원시값", "늘림", "계획", "검사성적", "겹침", "파이프라인",
    "회수기간", "현황", "훼손", "밀어냄", "메일", "사용됨", "달랐음", "처리됨",
    "목록", "달라짐", "이동", "사업개발", "제조기술",
    "범위", "시점", "가정", "차이", "정보", "순서",
    # `기준가`(=기준가격)라는 말이 있어 한 번 뺐다가, 코퍼스 전체 2곳의 앞뒤를 직접 읽고 넣었다
    # ("완료 기준가 원문에서" · "완료 기준와 결합되어" — 둘 다 명사 뒤 조사다).
    "기준",
)

# (받침 있는 말에 붙는 형태, 받침 없는 말에 붙는 형태)
PAIRS = (("이", "가"), ("은", "는"), ("을", "를"), ("과", "와"))
_TO_BATCHIM = {vowel: batchim for batchim, vowel in PAIRS}
_TO_VOWEL = {batchim: vowel for batchim, vowel in PAIRS}
_ALL_PARTICLES = "".join(b + v for b, v in PAIRS)

# ⚠ 긴 낱말을 먼저 시도해야 한다 — `개선안`이 `수익배분안` 안에서 먼저 잡히지 않게.
_TAIL_RE = re.compile("(%s)([%s])(?=\\s)" % (
    "|".join(sorted(STEM_TAILS, key=len, reverse=True)), _ALL_PARTICLES))


def _has_batchim(ch: str) -> bool | None:
    code = ord(ch) - 0xAC00
    if not 0 <= code <= 11171:
        return None
    return code % 28 != 0


def fix_text(text: str) -> tuple[str, collections.Counter]:
    """허용 목록에 있는 낱말 뒤의 조사만 그 낱말의 받침에 맞춘다.

    ⚠ 목록의 낱말이 전부 받침을 가진 것은 아니다(`정보`·`차이`·`범위`·`순서`).
      한 방향으로만 바꾸면 그 넷이 오히려 망가진다 — 받침을 매번 본다.
    """
    hits: collections.Counter = collections.Counter()

    def repl(m: re.Match[str]) -> str:
        stem, particle = m.group(1), m.group(2)
        batchim = _has_batchim(stem[-1])
        if batchim is None:
            return m.group(0)
        want = _TO_BATCHIM.get(particle) if batchim else _TO_VOWEL.get(particle)
        if want is None:  # 이미 맞는 형태다
            return m.group(0)
        hits[f"{stem}{particle}→{stem}{want}"] += 1
        return stem + want

    return _TAIL_RE.sub(repl, text), hits


def _served_body(meta: dict, meta_path: Path) -> tuple[Path, str] | None:
    """지금 실제로 읽히는 본문 — content_revision_path 가 있으면 그쪽."""
    rev = str(meta.get("content_revision_path") or "").strip()
    if rev:
        path = ROOT / rev
        if path.is_file():
            return path, path.read_text(encoding="utf-8")
    stem = meta_path.name.replace(".metadata.json", "")
    cands = [p for p in sorted(ROOT.glob(stem + "*.md")) if not p.name.endswith(".cleaned.md")]
    if len(cands) != 1:
        return None
    return cands[0], cands[0].read_text(encoding="utf-8")


def main(argv=None) -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="골든 후보 조사 교정 (비파괴)")
    ap.add_argument("--dry", action="store_true", help="쓰지 않고 결과만 보여준다")
    ap.add_argument("--revert", action="store_true", help="content_revision_path 를 v5 이전으로 되돌림")
    a = ap.parse_args(argv)

    metas = sorted(ROOT.glob("*.metadata.json"))
    print("후보 metadata %d개" % len(metas))

    if a.revert:
        n = 0
        for mp in metas:
            meta = json.loads(mp.read_text("utf-8"))
            prev = str(meta.get("particle_fix_previous_revision") or "")
            if not str(meta.get("content_revision_path") or "").endswith(SUFFIX):
                continue
            if prev:
                meta["content_revision_path"] = prev
            else:
                meta.pop("content_revision_path", None)
            meta.pop("particle_fix_previous_revision", None)
            meta.pop("particle_fix_count", None)
            mp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            n += 1
        print("  원복: metadata %d건의 연결을 되돌렸다. v5 파일은 revisions/ 에 남는다." % n)
        return 0

    plan = []
    total: collections.Counter = collections.Counter()
    by_origin: collections.Counter = collections.Counter()
    for mp in metas:
        meta = json.loads(mp.read_text("utf-8"))
        found = _served_body(meta, mp)
        if found is None:
            continue
        src, body = found
        fixed, hits = fix_text(body)
        if not hits:
            continue
        total.update(hits)
        by_origin[str(meta.get("document_origin") or "?")] += 1
        plan.append((mp, meta, str(meta.get("doc_id") or mp.stem), src, fixed, sum(hits.values())))

    print("  고칠 후보 %d건 · 고칠 자리 %d곳 · 형태 %d개"
          % (len(plan), sum(total.values()), len(total)))
    print("  출처별 후보수:", dict(by_origin))
    for form, cnt in total.most_common():
        print("     %6d회  %s" % (cnt, form))

    if a.dry:
        print("\n  --dry — 아무것도 쓰지 않았다.")
        return 0

    REV.mkdir(parents=True, exist_ok=True)
    for mp, meta, doc_id, src, fixed, n_fix in plan:
        out = REV / (doc_id + SUFFIX)
        out.write_text(fixed, encoding="utf-8")
        prev = str(meta.get("content_revision_path") or "")
        if prev:
            meta["particle_fix_previous_revision"] = prev
        meta["content_revision_path"] = out.relative_to(ROOT).as_posix()
        meta["particle_fix_count"] = n_fix
        mp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n  적용: 교정본 %d건 → %s" % (len(plan), REV.relative_to(_POC).as_posix()))
    print("  원본 .md 와 v4 세척본은 손대지 않았다. --revert 로 연결만 되돌린다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
