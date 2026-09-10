#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""정리된 골든 후보로 모델을 **공정 비교**한다 — 학습이 아니라 평가다.

왜 이 도구가 있는가(2026-09-08). "어느 모델이 나은가"를 오늘 하루 여러 번 물었는데
매번 같은 이유로 답을 못 했다 — **평가면이 전부 오염돼 있었다.**

    경화42 · clean42 · holdout109 · v5test   길이가 등급을 알려준다(--strict 전부 실패)
    적대셋 golden_100                         입력 중앙값 19자 · 두 모델 다 찍기 수준
    골든 후보 988건                            **정답이 본문에 적혀 있었다**(2026-09-08 발견)

마지막 것을 `clean_candidate_answer_leak.py` 로 걷어내니 처음으로 쓸 만한 면이 생겼다:

    지름길 상한  99.3% → 30.3%   (4등급 무작위 25%)
    길이 중앙값  TS 2,214 · S1 2,173 · S2 2,144 · S3 2,155
    템플릿       9종 × 4등급 균형 (어느 것도 한 등급 60% 미만)

⚠ 이것은 **정확도가 아니라 공정 비교**다. 정답은 생성 시 의도 등급이고 사람 확정은
  0건이다(콘솔 fixed=0). "두 모델을 같은 자로 잰다"까지가 이 도구가 하는 말이다.

⛔ **이 문서들로 학습하지 말 것.** 감리 개선방향이 명시했다 — "학습데이터셋과는 별도의
  검증 목적의 골든셋을 검증 목적으로만 사용". 학습에 쓰면 지금 가진 유일한 깨끗한
  평가면이 사라진다.

사용:
    python scripts/eval_on_clean_candidates.py                       # 배포본만
    python scripts/eval_on_clean_candidates.py --model <dir> ...     # 여러 모델 비교
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
if str(_POC / "src") not in sys.path:
    sys.path.insert(0, str(_POC / "src"))

ROOT = _POC / "datasets" / "proxy_gold" / "single_document_candidates"
GRADES = ("TS", "S1", "S2", "S3")
_SEV = {"TS": 3, "S1": 2, "S2": 1, "S3": 0}
_GRADE_IN_ID = re.compile(r"-(TS|S1|S2|S3)-")
_ANSWER = re.compile(r"##\s*등급\s*제안\s*사유\s*:\s*(TS|S1|S2|S3)")

DEFAULT_MODELS = ["artifacts/classifier_p1_v5_clean/v-fe4b386b"]


def load_candidates() -> list[dict]:
    """콘솔과 **같은 우선순위**로 본문을 고른다 — content_revision_path 가 있으면 그쪽."""
    rows = []
    for mp in sorted(ROOT.glob("*.metadata.json")):
        try:
            meta = json.loads(mp.read_text("utf-8"))
        except Exception:  # noqa: BLE001
            continue
        doc_id = str(meta.get("doc_id") or "")
        gm = _GRADE_IN_ID.search(doc_id)
        label = gm.group(1) if gm else str(meta.get("intended_label") or "")
        if label not in GRADES:
            continue
        rev = str(meta.get("content_revision_path") or "").strip()
        src = (ROOT / rev) if rev else None
        if src is None or not src.is_file():
            stem = mp.name.replace(".metadata.json", "")
            c = [p for p in sorted(ROOT.glob(stem + "*.md")) if not p.name.endswith(".cleaned.md")]
            if len(c) != 1:
                continue
            src = c[0]
        try:
            text = src.read_text("utf-8")
        except OSError:
            continue
        rows.append({
            "doc_id": doc_id, "text": text, "label": label,
            "origin": str(meta.get("document_origin") or "unknown"),
        })
    return rows


def report(name: str, rows: list[dict], preds: list[str]) -> dict:
    hit = sum(1 for r, p in zip(rows, preds) if r["label"] == p)
    per = {g: [0, 0] for g in GRADES}
    conf: dict[str, Counter] = defaultdict(Counter)
    hi_s3 = under = over = 0
    for r, p in zip(rows, preds):
        g = r["label"]
        per[g][1] += 1
        if p == g:
            per[g][0] += 1
        conf[g][p] += 1
        if g in ("TS", "S1") and p == "S3":
            hi_s3 += 1
        if _SEV.get(p, 0) < _SEV[g]:
            under += 1
        elif _SEV.get(p, 0) > _SEV[g]:
            over += 1
    real = [(r, p) for r, p in zip(rows, preds) if r["origin"] == "public_real"]
    real_hit = sum(1 for r, p in real if r["label"] == p)

    print("\n[%s]  일치 %d/%d = %.2f%%" % (name, hit, len(rows), hit / len(rows) * 100))
    print("   등급별: " + " · ".join("%s %d/%d(%.0f%%)" % (g, per[g][0], per[g][1],
          per[g][0] / max(per[g][1], 1) * 100) for g in GRADES))
    print("   방향  : 과분류 %d · 과소분류 %d" % (over, under))
    print("   ⚠ 고등급→S3(무음 미탐): %d건" % hi_s3)
    if real:
        print("   공개 실문서 %d건 중 일치 %d (%.0f%%)" % (len(real), real_hit,
              real_hit / len(real) * 100))
    print("   혼동행렬(행=정답):")
    print("        " + "".join("%6s" % g for g in GRADES))
    for g in GRADES:
        print("     %-3s " % g + "".join("%6d" % conf[g][p] for p in GRADES))
    # [2026-09-10] 건별 결과를 함께 돌려준다.
    #
    # 왜 필요한가. 종전에는 합계만 남았다(`high_to_s3: 16`). 그런데 "2차의견 게이트가 그
    # 16건을 잡는가" 를 재려면 **그 16건이 무엇인지** 알아야 한다. 합계만 있으면 도구를
    # 고치기 전에는 그 질문에 답을 못 한다(2026-09-10 에 실제로 막혔다).
    #
    # 본문(text)은 담지 않는다 — 30MB 가 되고, 이 파일은 커밋되는 리포트다.
    cases = [
        {
            "doc_id": r["doc_id"],
            "origin": r["origin"],
            "gold": r["label"],
            "pred": p,
            "hit": r["label"] == p,
            # 고등급(TS/S1)을 S3 으로 자동확정할 소지 — 무음 미탐 후보다.
            "high_to_s3": r["label"] in ("TS", "S1") and p == "S3",
            "direction": ("under" if _SEV.get(p, 0) < _SEV[r["label"]]
                          else "over" if _SEV.get(p, 0) > _SEV[r["label"]] else "same"),
        }
        for r, p in zip(rows, preds)
    ]
    return {"name": name, "n": len(rows), "hit": hit, "high_to_s3": hi_s3,
            "over": over, "under": under, "per_grade": per, "cases": cases}


def main(argv=None) -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="정리된 골든 후보로 모델 공정 비교")
    ap.add_argument("--model", action="append", default=None, help="모델 디렉터리(여러 번)")
    ap.add_argument("--origin", choices=["all", "synthetic", "public_real"], default="all")
    ap.add_argument("--report", default="", help="요약 JSON 경로(건별 제외 — 커밋되는 파일이다)")
    ap.add_argument("--cases", default="",
                    help="건별 JSON 경로(doc_id·정답·예측·방향). 무음 미탐이 어느 문서인지 "
                         "알아야 게이트 효과를 잴 수 있다")
    a = ap.parse_args(argv)

    rows = load_candidates()
    if a.origin != "all":
        rows = [r for r in rows if r["origin"] == a.origin]

    leaked = sum(1 for r in rows if _ANSWER.search(r["text"]))
    print("=" * 74)
    print(" 정리된 골든 후보 평가   %d건 (%s)" % (len(rows), a.origin))
    print("=" * 74)
    print("  출처:", dict(Counter(r["origin"] for r in rows)))
    print("  정답 분포:", dict(Counter(r["label"] for r in rows)))
    if leaked:
        print("\n  ⛔ 본문에 정답이 남은 문서 %d건 — 먼저 정리할 것:" % leaked)
        print("     python scripts/clean_candidate_answer_leak.py")
        return 2
    print("  본문 정답 노출: 없음 ✓")

    from gate_p1_candidate import _pred_grade, predict_direct  # noqa: PLC0415

    out = []
    for md in (a.model or DEFAULT_MODELS):
        preds = [_pred_grade(p) for p in predict_direct(Path(md), rows)]
        out.append(report(Path(md).name or md, rows, preds))

    # 무음 미탐 후보(고등급→S3)의 doc_id 를 화면에 바로 찍는다 — 합계만 보고는
    # "그 16건이 무엇이냐"에 답할 수 없어 실제로 한 번 막혔다(2026-09-10).
    for r in out:
        ids = [c["doc_id"] for c in r["cases"] if c["high_to_s3"]]
        if ids:
            print("\n  [%s] 고등급→S3 %d건 doc_id:" % (r["name"], len(ids)))
            for i in range(0, len(ids), 3):
                print("     " + "  ".join(ids[i:i + 3]))

    if a.cases:
        Path(a.cases).parent.mkdir(parents=True, exist_ok=True)
        Path(a.cases).write_text(
            json.dumps([{"name": r["name"], "cases": r["cases"]} for r in out],
                       ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print("\n[cases] %s  (건별 %d행)" % (a.cases, sum(len(r["cases"]) for r in out)))

    if a.report:
        # 요약 리포트에는 건별을 담지 않는다 — 이 파일은 커밋되므로 1,055건 x 모델수 만큼
        # 불어나면 diff 를 읽을 수 없다. 건별은 --cases 로 따로 뺀다.
        summary = [{k: v for k, v in r.items() if k != "cases"} for r in out]
        Path(a.report).parent.mkdir(parents=True, exist_ok=True)
        Path(a.report).write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
        print("\n[report] %s" % a.report)
    print("\n  ⚠ 이 수치는 **정확도가 아니라 공정 비교**다 — 정답은 생성 시 의도 등급이고")
    print("     사람 확정은 0건이다. 두 모델을 같은 자로 쟀다는 것까지가 이 도구의 주장이다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
