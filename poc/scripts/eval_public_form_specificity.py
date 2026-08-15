"""공개 서식으로 **특이도**(헛경보율)를 잰다 — 이건 정당한 평가면이다.

왜 이 셋은 다르다. 지금까지 우리 판정면은 전부 기계 라벨이었다. 그런데 이 코퍼스는
**정답을 안다** — 공개 배포되는 빈 서식(신청서·계약서 양식·대장)에는 영업비밀이 없다.
누구나 받을 수 있고 내용이 비어 있으므로 비공지성도 가치도 성립하지 않는다.

    정답      영업비밀 아님 (S2 또는 S3)
    측정      영업비밀(TS/S1)이라 답하면 **헛경보**

⚠ 한계를 먼저 적는다.
   · 등급 정확도는 못 잰다. S2 냐 S3 냐의 정답이 없다(그건 취급 규칙 문제다).
   · **잴 수 있는 것은 특이도 하나뿐**이고 그것으로 충분하다 — 우리가 약한 축이 정확히
     거기다(실문서 봉인면 특이도 0.292).
   · 폴더마다 성격이 다르다. '마케팅자료' 는 빈 서식이 아니라 실제 기획서·제안서일 수
     있어 정답이 다르다. **폴더별로 나눠 보고한다** — 합치면 수치가 어디서 왔는지 모른다.

⚠ 이 셋을 보고 학습을 고치면 그 순간 판정면이 아니게 된다. 절반을 봉인해 둔다.
"""
from __future__ import annotations

import argparse
import hashlib
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

# 폴더별 정답 성격. '빈 서식' 만 "영업비밀 아님" 이 확실하다.
FOLDER_KIND = {
    "100가지 회사 업무용 서식": "blank_form",
    "각종 법률 서식 모음": "blank_form",
    "계약서 모음": "blank_form",
    "문서양식총람": "blank_form",
    "XLS 엑셀 서식 자료": "blank_form",
    "마케팅자료": "mixed",       # 실제 기획서·제안서 섞임 — 정답 불확실
}


def wilson_upper(k: int, n: int, z: float = 1.96) -> float:
    if n == 0:
        return 1.0
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return min(1.0, (c + r) / d)


def _half(path: Path) -> str:
    """문서 해시로 work/sealed 고정 분할 — 회차마다 같은 문서가 같은 쪽에 남는다."""
    h = hashlib.sha256(str(path).encode("utf-8")).hexdigest()
    return "work" if int(h[:8], 16) % 2 == 0 else "sealed"


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="공개 서식 특이도 측정")
    ap.add_argument("--root", default="../서식모음")
    ap.add_argument("--per-folder", type=int, default=60)
    ap.add_argument("--unseal", action="store_true", help="봉인 절반까지 본다")
    ap.add_argument("--seed", type=int, default=20260815)
    ap.add_argument("--report", default="reports/PUBLIC_FORM_SPECIFICITY.json")
    args = ap.parse_args(argv)

    os.environ.setdefault("TESTING", "1")
    os.environ.setdefault("VECTOR_BACKEND", "inmemory")
    os.environ.setdefault("REQUIRE_REAL_EMBEDDER", "false")

    from koipa.modules.m2_preprocess.pipeline import PreprocessPipeline
    from koipa.schemas.classify import ClassifyRequest
    from koipa.services.classify_service import ClassifyService

    root = Path(args.root)
    by_folder: dict[str, list[Path]] = defaultdict(list)
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        try:
            top = p.relative_to(root).parts[0]
        except (ValueError, IndexError):
            continue
        by_folder[top].append(p)

    rng = random.Random(args.seed)
    sample: list[tuple[str, Path]] = []
    for folder, files in by_folder.items():
        pick = rng.sample(files, min(args.per_folder, len(files)))
        for p in pick:
            if args.unseal or _half(p) == "work":
                sample.append((folder, p))
    print(f"[sample] {len(sample)}건 "
          f"({'봉인 포함' if args.unseal else 'work 절반만'})\n")

    pipe, svc = PreprocessPipeline(), ClassifyService()
    rows = []
    t0 = time.perf_counter()
    for i, (folder, p) in enumerate(sample):
        try:
            pre = pipe.run_file(p)
            text = pre.text or ""
            if not text.strip():
                rows.append({"folder": folder, "name": p.name, "parsed": False})
                continue
            r = svc.classify(ClassifyRequest(doc_id=f"form-{i:05d}", content=text,
                                             return_evidence=False))
            rows.append({
                "folder": folder, "name": p.name, "parsed": True,
                "grade": r.label.value if hasattr(r.label, "value") else str(r.label),
                "status": r.status, "chars": len(text),
                "conf": round(float(r.confidence or 0), 4),
            })
        except Exception as exc:  # noqa: BLE001
            rows.append({"folder": folder, "name": p.name, "parsed": False,
                         "error": type(exc).__name__})
        if (i + 1) % 50 == 0:
            print(f"  {i + 1}/{len(sample)} · {time.perf_counter() - t0:.0f}s")

    print(f"\n[done] {len(rows)}건 · {time.perf_counter() - t0:.0f}s\n")
    graded = [r for r in rows if r.get("grade")]
    print("폴더별 — 빈 서식은 영업비밀이 아니어야 한다")
    print(f"  {'폴더':26s}{'성격':10s}{'분류':>5s}{'헛경보':>7s}{'율':>8s}{'95%상한':>9s}{'자동확정':>9s}")
    out: dict = {"n": len(rows), "folders": {}}
    for folder in sorted(by_folder):
        g = [r for r in graded if r["folder"] == folder]
        if not g:
            continue
        kind = FOLDER_KIND.get(folder, "unknown")
        fa = [r for r in g if r["grade"] in SECRET]
        auto = [r for r in g if r.get("status") != "needs_review"]
        up = wilson_upper(len(fa), len(g))
        out["folders"][folder] = {
            "kind": kind, "n": len(g), "false_alarm": len(fa),
            "rate": round(len(fa) / len(g), 4), "upper95": round(up, 4),
            "auto": len(auto), "dist": dict(Counter(r["grade"] for r in g)),
        }
        print(f"  {folder[:25]:26s}{kind:10s}{len(g):>5d}{len(fa):>7d}"
              f"{len(fa) / len(g):>8.1%}{up:>9.4f}{len(auto) / len(g):>9.1%}")

    blank = [r for r in graded if FOLDER_KIND.get(r["folder"]) == "blank_form"]
    if blank:
        fa = [r for r in blank if r["grade"] in SECRET]
        print(f"\n빈 서식만 (정답이 확실한 것) — {len(blank)}건")
        print(f"  헛경보 {len(fa)} = {len(fa) / len(blank):.1%} · "
              f"95% 상한 {wilson_upper(len(fa), len(blank)):.4f}")
        print(f"  등급 분포 {dict(Counter(r['grade'] for r in blank))}")
        print("\n  헛경보 예시")
        for r in fa[:8]:
            print(f"    [{r['grade']}] {r['name'][:56]}")
        out["blank_form_summary"] = {
            "n": len(blank), "false_alarm": len(fa),
            "rate": round(len(fa) / len(blank), 4),
            "upper95": round(wilson_upper(len(fa), len(blank)), 4),
        }

    Path(args.report).write_text(json.dumps({**out, "rows": rows},
                                            ensure_ascii=False, indent=2), "utf-8")
    print(f"\n[report] {args.report}")
    print("\n⚠ 잴 수 있는 것은 특이도 하나다. 등급 정확도(S2냐 S3냐)는 정답이 없다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
