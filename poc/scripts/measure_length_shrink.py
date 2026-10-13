"""**짧아지면 등급이 내려가는가** — 미탐 방향의 길이 효과를 잰다.

왜. 앞선 측정(`docs/LENGTH_EFFECT_2026-08-16.md`)은 길이를 **늘리는** 방향만 봤고
결론은 "93.3% 는 4배로 늘려도 판정 불변, 변한 6.7% 도 안전한 쪽(고등급)" 이었다.
그런데 **진짜 위험은 반대다.**

    실문서 gold 777건의 TS 중앙값 = 237자
    실제 영업비밀 문서가 짧다.

짧다는 이유로 낮게 판정되면 그것이 미탐이다. 그 방향은 아직 확인하지 않았다.

조작을 둘로 나눈다. 잘라내기만 하면 내용도 함께 줄어 길이 효과와 분리되지 않는다.

    (A) 잘라내기       앞에서부터 60% · 30% 만 남긴다. 내용도 준다 - 참고
    (B) 압축           공백·줄바꿈을 접어 **길이만** 줄인다. 내용은 보존 - 주 판정

⚠ (B)도 완전하지 않다. 서식이 무너지면 모델이 다르게 볼 수 있다. 다만 (A)보다는
  길이를 훨씬 깨끗하게 분리한다.

⚠ 판정면은 골든 후보 120건이다. 등급별 길이를 맞춰 만든 셋이라(중앙 2,178~2,214자)
  길이 신호가 없는 상태에서 재는 것이다.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

for _s in ("stdout", "stderr"):
    _f = getattr(sys, _s)
    if getattr(_f, "encoding", "") and _f.encoding.lower() not in ("utf-8", "utf-8-sig"):
        import io as _io
        setattr(sys, _s, _io.TextIOWrapper(_f.buffer, encoding="utf-8", errors="replace"))

ORDER = ["TS", "S1", "S2", "S3"]
RANK = {g: i for i, g in enumerate(ORDER)}
SECRET = ("TS", "S1")
_WS = re.compile(r"[ \t]+")
_NL = re.compile(r"\n{2,}")


def compress(text: str) -> str:
    """공백·빈 줄을 접어 길이만 줄인다. 어휘는 하나도 안 지운다."""
    t = _WS.sub(" ", text)
    t = _NL.sub("\n", t)
    return t.strip()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="짧아지면 등급이 내려가는가")
    ap.add_argument("--gold", default="datasets/golden_review/ff5a822c/candidates.jsonl")
    ap.add_argument("--model-dir", default="artifacts/classifier_p1_v5_clean/v-fe4b386b")
    ap.add_argument("--report", default="reports/LENGTH_SHRINK.json")
    args = ap.parse_args(argv)

    os.environ.setdefault("TESTING", "1")
    os.environ.setdefault("VECTOR_BACKEND", "inmemory")
    os.environ.setdefault("REQUIRE_REAL_EMBEDDER", "false")
    os.environ.setdefault("METADATA_FLOOR_ENABLED", "true")
    os.environ["CLASSIFIER_MODEL_DIR"] = args.model_dir

    from koipa.schemas.classify import ClassifyRequest  # noqa: PLC0415
    from koipa.services.classify_service import ClassifyService  # noqa: PLC0415

    rows = [json.loads(l) for l in Path(args.gold).read_text(encoding="utf-8").splitlines()
            if l.strip()]
    rows = [r for r in rows if (r.get("text") or "").strip() and r.get("label") in RANK]
    print(f"판정면 {len(rows)}건 · 모델 {Path(args.model_dir).name}\n")

    svc = ClassifyService()
    out, t0 = [], time.perf_counter()

    def grade(text: str, tag: str):
        try:
            r = svc.classify(ClassifyRequest(doc_id=tag, content=text, return_evidence=False))
            g = r.label.value if hasattr(r.label, "value") else str(r.label)
            return g, r.status
        except Exception as exc:  # noqa: BLE001
            return None, f"error:{type(exc).__name__}"

    for i, r in enumerate(rows):
        t = (r.get("text") or "").strip()
        rec = {"doc_id": r.get("doc_id"), "truth": r.get("label"), "chars": len(t)}
        rec["g_full"], rec["s_full"] = grade(t, f"{i}_full")
        comp = compress(t)
        rec["chars_comp"] = len(comp)
        rec["g_comp"], rec["s_comp"] = grade(comp, f"{i}_comp")
        for frac in (0.6, 0.3):
            key = f"cut{int(frac * 100)}"
            rec[f"g_{key}"], rec[f"s_{key}"] = grade(t[: int(len(t) * frac)], f"{i}_{key}")
        out.append(rec)
        if (i + 1) % 20 == 0:
            print(f"  {i + 1}/{len(rows)} · {time.perf_counter() - t0:.0f}s")

    ok = [r for r in out if r.get("g_full")]
    print(f"\n[결과] n={len(ok)} · {time.perf_counter() - t0:.0f}s")

    comp_shrink = sum(r["chars_comp"] for r in ok) / max(1, sum(r["chars"] for r in ok))
    print(f"  압축으로 줄어든 비율: {1 - comp_shrink:.1%} (내용 보존)\n")

    def report(key: str, label: str) -> dict:
        gk, sk = f"g_{key}", f"s_{key}"
        changed = [r for r in ok if r.get(gk) and r[gk] != r["g_full"]]
        down = [r for r in changed if RANK[r[gk]] > RANK[r["g_full"]]]
        up = [r for r in changed if RANK[r[gk]] < RANK[r["g_full"]]]
        # 무음 미탐: 정답이 고등급인데 낮게 보고 자동확정
        miss_full = [r for r in ok if r["truth"] in SECRET
                     and RANK[r["g_full"]] > RANK[r["truth"]] and r["s_full"] != "needs_review"]
        miss_k = [r for r in ok if r.get(gk) and r["truth"] in SECRET
                  and RANK[r[gk]] > RANK[r["truth"]] and r[sk] != "needs_review"]
        exact_f = sum(1 for r in ok if r["g_full"] == r["truth"])
        exact_k = sum(1 for r in ok if r.get(gk) == r["truth"])
        print(f"[{label}]")
        print(f"  등급 변화     {len(changed)}/{len(ok)} = {len(changed)/len(ok):.1%}"
              f"   낮아짐 {len(down)} · 높아짐 {len(up)}")
        print(f"  정답 일치     {exact_f/len(ok):.1%} -> {exact_k/len(ok):.1%}")
        print(f"  무음 미탐     {len(miss_full)}건 -> {len(miss_k)}건")
        return {"changed": len(changed), "down": len(down), "up": len(up),
                "exact_full": exact_f, "exact": exact_k,
                "silent_full": len(miss_full), "silent": len(miss_k)}

    res = {"compress": report("comp", "압축 — 길이만 줄임 (주 판정)"),
           "cut60": report("cut60", "잘라내기 60% — 내용도 줄어듦 (참고)"),
           "cut30": report("cut30", "잘라내기 30% — 내용도 줄어듦 (참고)")}

    print("\n등급별 변화 (압축 기준)")
    byg = Counter(r["truth"] for r in ok if r.get("g_comp") and r["g_comp"] != r["g_full"])
    tot = Counter(r["truth"] for r in ok)
    for g in ORDER:
        print(f"  {g}  {byg.get(g, 0)}/{tot.get(g, 0)}")

    Path(args.report).write_text(
        json.dumps({"n": len(ok), "summary": res, "rows": out}, ensure_ascii=False, indent=2),
        "utf-8")
    print(f"\n[report] {args.report}")
    print("\n⚠ 압축은 어휘를 지우지 않지만 서식이 무너진다. 잘라내기는 내용도 준다.")
    print("  둘 다 길이 효과를 완전히 분리하지는 못한다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
