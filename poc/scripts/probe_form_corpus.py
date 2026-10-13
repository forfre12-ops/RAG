"""서식 코퍼스로 **파싱 견고성과 과분류**를 잰다 — 정확도 평가가 아니다.

왜. `서식모음/` 에 실제 한국 업무 문서 포맷 3,844건이 있다(hwp 2,920 · ppt 482 ·
doc 343 · gul 27 · pdf 16 · xls 7 …). 우리 인수 팩은 포맷당 1~2건뿐이었다.

이 코퍼스로 답할 수 있는 것과 없는 것을 먼저 가른다.

    답할 수 있다  포맷별 파싱 성공률 · 미지원 포맷의 실제 비중 · 추출 품질 분포
                 **빈 서식을 영업비밀로 과분류하는가**(오늘 mundane 실측의 확장)
    답할 수 없다  분류 정확도. 라벨이 없고 애초에 서식(빈 양식)이라 정답 등급이 없다

과분류 축이 특히 값지다. 서식은 내용이 비어 있으므로 **거의 전부 S3/S2 여야 한다.**
여기서 TS/S1 이 많이 나오면 검수 부담이 실무에서 그대로 터진다.

⚠ 이것은 판정면이 아니다. 결과를 보고 학습을 고쳐도 되지만, 그렇게 하면 이 코퍼스도
   소비된다. 지금은 **진단 목적**으로만 쓴다.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

SECRET = ("TS", "S1")


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="서식 코퍼스 파싱·과분류 진단")
    ap.add_argument("--root", default="../서식모음")
    ap.add_argument("--per-ext", type=int, default=12, help="확장자당 표본 수")
    ap.add_argument("--classify", action="store_true", help="분류까지 태운다(느리다)")
    ap.add_argument("--seed", type=int, default=20260815)
    ap.add_argument("--report", default="reports/FORM_CORPUS_PROBE.json")
    args = ap.parse_args(argv)

    os.environ.setdefault("TESTING", "1")
    os.environ.setdefault("VECTOR_BACKEND", "inmemory")
    os.environ.setdefault("REQUIRE_REAL_EMBEDDER", "false")

    root = Path(args.root)
    if not root.exists():
        print(f"[error] 없는 경로: {root}")
        return 2

    by_ext: dict[str, list[Path]] = defaultdict(list)
    for p in root.rglob("*"):
        if p.is_file():
            by_ext[p.suffix.lower().lstrip(".") or "(none)"].append(p)
    print(f"[corpus] {root} — 총 {sum(len(v) for v in by_ext.values())}건")
    for e, v in sorted(by_ext.items(), key=lambda x: -len(x[1]))[:12]:
        print(f"    .{e:<8s} {len(v):5d}")

    rng = random.Random(args.seed)
    sample: list[Path] = []
    for e, v in by_ext.items():
        sample.extend(rng.sample(v, min(args.per_ext, len(v))))
    print(f"\n[sample] 확장자당 최대 {args.per_ext}건 → {len(sample)}건\n")

    from koipa.modules.m2_preprocess.extractor import extract

    svc = None
    if args.classify:
        from koipa.schemas.classify import ClassifyRequest
        from koipa.services.classify_service import ClassifyService

        svc = ClassifyService()

    rows = []
    t0 = time.perf_counter()
    for i, p in enumerate(sample):
        ext = p.suffix.lower().lstrip(".")
        rec: dict = {"ext": ext, "name": p.name, "bytes": p.stat().st_size}
        try:
            ex = extract(p)
            text = ex.text or ""
            subs = "".join(c for c in text
                           if not c.isspace() and c not in ("￼", "﻿", "​"))
            warns = list(getattr(ex, "warnings", None) or [])
            rec.update(ok=bool(text.strip()) and not ex.error and ex.quality > 0,
                       chars=len(text), substantive=len(subs), method=ex.method,
                       quality=round(float(ex.quality or 0), 3),
                       # [무음 실패] 추출은 성공했다는데 판정할 본문이 없는 경우.
                       # 이것이 가장 위험하다 - 오류가 없어 그대로 자동확정된다.
                       thin=any("body_below_classifiable" in w for w in warns),
                       error=(ex.error or "")[:110])
        except Exception as exc:  # noqa: BLE001
            rec.update(ok=False, chars=0, method="EXCEPTION",
                       error=f"{type(exc).__name__}: {exc}"[:110])
            text = ""
        if svc is not None and rec["ok"]:
            try:
                r = svc.classify(ClassifyRequest(doc_id=f"form-{i:04d}", content=text,
                                                 return_evidence=False))
                rec["grade"] = r.label.value if hasattr(r.label, "value") else str(r.label)
                rec["status"] = r.status
            except Exception as exc:  # noqa: BLE001
                rec["grade"] = f"ERR:{type(exc).__name__}"
        rows.append(rec)
        if (i + 1) % 25 == 0:
            print(f"  {i + 1}/{len(sample)} · {time.perf_counter() - t0:.0f}s")

    print(f"\n[done] {len(rows)}건 · {time.perf_counter() - t0:.0f}s\n")
    print("0) 무음 실패 — 추출 성공인데 판정할 본문이 없다")
    thin = [r for r in rows if r.get("thin")]
    okk = [r for r in rows if r["ok"]]
    print(f"   {len(thin)}/{len(okk)} = {len(thin) / max(1, len(okk)):.1%} (성공 판정분 기준)")
    if thin:
        te = Counter(r["ext"] for r in thin)
        print(f"   확장자별 {dict(te)}")

    print("")
    print("1) 포맷별 파싱")
    print(f"   {'확장자':<10s}{'표본':>5s}{'성공':>5s}{'성공률':>8s}{'중앙자수':>9s}{'무음':>7s}  주요 실패")
    for e in sorted({r["ext"] for r in rows}):
        g = [r for r in rows if r["ext"] == e]
        ok = [r for r in g if r["ok"]]
        chars = sorted(r["chars"] for r in ok)
        med = chars[len(chars) // 2] if chars else 0
        errs = Counter(r.get("error", "").split(":")[0] for r in g if not r["ok"])
        top = errs.most_common(1)[0][0][:34] if errs else ""
        nthin = sum(1 for r in g if r.get("thin"))
        print(f"   .{e:<9s}{len(g):>5d}{len(ok):>5d}{len(ok) / len(g):>8.0%}{med:>9d}"
              f"{nthin:>7d}  {top}")

    if svc is not None:
        graded = [r for r in rows if r.get("grade") and not str(r["grade"]).startswith("ERR")]
        if graded:
            print("\n2) 과분류 — 서식은 내용이 비어 있으므로 고등급이 나오면 안 된다")
            dist = Counter(r["grade"] for r in graded)
            print(f"   등급 {dict(dist)}")
            hi = [r for r in graded if r["grade"] in SECRET]
            print(f"   영업비밀(TS/S1) 판정 {len(hi)}/{len(graded)} = {len(hi) / len(graded):.1%}")
            auto = [r for r in graded if r.get("status") != "needs_review"]
            print(f"   자동확정 {len(auto)}/{len(graded)} = {len(auto) / len(graded):.1%}")
            for r in hi[:6]:
                print(f"     [{r['grade']}] {r['name'][:60]}")

    Path(args.report).write_text(json.dumps({"n": len(rows), "rows": rows},
                                            ensure_ascii=False, indent=2), "utf-8")
    print(f"\n[report] {args.report}")
    print("\n⚠ 이것은 파싱·과분류 진단이지 분류 정확도 평가가 아니다(라벨 없음·빈 서식).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
