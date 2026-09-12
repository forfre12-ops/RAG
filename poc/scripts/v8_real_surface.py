"""실문서 판정면 — 출처별로 갈라 만든다.

왜. 지금까지 실문서 판정을 경화42(n=42)로 했다. 그중 7건이 판례이고 고등급은 26건뿐이라
"과소분류 34.6%" 같은 수치의 신뢰구간이 너무 넓다. 회차를 비교할 수 없다.

gold_real 을 중복 제거하고 출처별로 세면 성격이 완전히 갈린다:

    판례 계열          409건  S3 396 · 길이 1,700~5,000자   공개문서. 정의상 S3
    금융보고서          229건  S3 178 · S2 48                공시자료. 대체로 공개
    public_scenario   117건  S2 41 · S1 40 · TS 36         업무문서 · 고등급 포함
    synthetic_grounded  19건  TS 19                         합성

**섞으면 안 된다.** 판례는 우리 루브릭으로 비공지성 proven_absent -> S3 이므로, 모델이
S3 라 해도 맞다. 그 셋에서 "정확도 90%" 가 나와도 업무문서 성능의 근거가 아니다.
반대로 판례를 섞은 채 "과소분류 34.6%" 를 말하면 그 34.6% 가 어디서 왔는지 알 수 없다.

그래서 세 면으로 나눈다:

    business   public_scenario + synthetic_grounded   업무문서. **주 판정면**
    finance    금융보고서                              공시자료
    court      판례 계열                               공개문서. S3 이 정답인지 확인용

⚠ 이 셋들은 요소 라벨이 없다. 등급만 있으므로 등급 일치와 미탐 방향만 본다.
⚠ 한 번 쓰면 소비된다. 결과를 보고 학습 데이터를 고치면 판정면이 아니게 된다.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT / "src"), str(_ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

GRADES = ("TS", "S1", "S2", "S3")

SOURCES = ("classification_gold", "holdout_business", "holdout_eval.hardened")

# 출처 -> 면. 이름이 여럿이라 접두로 묶는다.
def surface_of(src: str) -> str:
    if src.startswith("판례"):
        return "court"
    if src in ("금융보고서",):
        return "finance"
    if src in ("public_scenario", "synthetic_grounded"):
        return "business"
    return "other"


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="실문서 판정면 구성")
    ap.add_argument("--out-dir", default="datasets/v8_real")
    args = ap.parse_args(argv)

    rows: list[dict] = []
    for name in SOURCES:
        p = Path(f"datasets/gold_real/{name}.jsonl")
        if not p.exists():
            print(f"[skip] {p}")
            continue
        for line in p.read_text("utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                r["_file"] = name
                rows.append(r)

    # 본문 앞 200자로 중복 제거 — 같은 문서가 여러 파일에 들어 있다
    seen: set[str] = set()
    uniq: list[dict] = []
    for r in rows:
        t = r.get("text") or r.get("content") or ""
        k = t[:200]
        if not t or r.get("label") not in GRADES or k in seen:
            continue
        seen.add(k)
        uniq.append(r)
    print(f"중복 제거 {len(rows)} -> {len(uniq)}건")

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    buckets: dict[str, list[dict]] = {}
    for r in uniq:
        s = surface_of(r.get("source") or r.get("label_source") or "?")
        buckets.setdefault(s, []).append(r)

    print(f"\n{'면':10s}{'건수':>6s}{'고등급':>7s}   등급 분포")
    for s, v in sorted(buckets.items(), key=lambda x: -len(x[1])):
        hi = sum(1 for r in v if r["label"] in ("TS", "S1"))
        print(f"{s:10s}{len(v):>6d}{hi:>7d}   {dict(Counter(r['label'] for r in v))}")
        (out / f"{s}.jsonl").write_text(
            chr(10).join(json.dumps(x, ensure_ascii=False) for x in v) + chr(10), "utf-8")

    print(f"\n[saved] {out}")
    print("\n주 판정면은 business 다 — 판례·공시가 섞이지 않은 업무문서다.")
    print("court 는 '모델이 공개문서를 S3 라 하는가' 를 확인하는 용도이지 성능 지표가 아니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
