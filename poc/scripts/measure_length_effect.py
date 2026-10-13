"""배포 모델이 길이에 얼마나 반응하는지 골든 후보 120건 전수로 잰다.

조작: 같은 본문을 2배·4배로 반복한다.
    길이      2배·4배
    내용 밀도  불변 (비밀 요소 비율이 그대로다)
그래서 등급이 움직이면 길이 때문이다.

⚠ 한계를 먼저 적는다.
   · 같은 문장이 4번 나오는 문서는 실제로 없다. 모델이 처음 보는 형태라 흔들리는 것과
     '길이에 반응하는 것' 을 이 시험은 완전히 분리하지 못한다.
   · 그래도 **0 이 아니라는 것**은 확정할 수 있다. 길이가 전혀 무관하다면 전건 불변이어야 한다.
   · 골든 후보 120건은 등급별 길이를 맞춰 만든 셋이다(중앙값 2,178~2,214자).
     길이 신호가 없는 셋에서 재는 것이라 조작 효과가 과대평가되지 않는다.

정답 대비 방향도 함께 본다 - 길이를 늘렸을 때 **맞아지는지 틀려지는지**가 실무 영향이다.
"""
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("VECTOR_BACKEND", "inmemory")
os.environ.setdefault("REQUIRE_REAL_EMBEDDER", "false")
os.environ.setdefault("METADATA_FLOOR_ENABLED", "true")
os.environ["CLASSIFIER_MODEL_DIR"] = "artifacts/classifier_p1_v5_clean/v-fe4b386b"

sys.path.insert(0, "src")
sys.stdout.reconfigure(encoding="utf-8")

from koipa.schemas.classify import ClassifyRequest  # noqa: E402
from koipa.services.classify_service import ClassifyService  # noqa: E402

ORDER = ["TS", "S1", "S2", "S3"]
RANK = {g: i for i, g in enumerate(ORDER)}
SECRET = ("TS", "S1")


def main():
    src = Path("datasets/golden_review/ff5a822c/candidates.jsonl")
    rows = [json.loads(l) for l in src.read_text(encoding="utf-8").splitlines() if l.strip()]
    rows = [r for r in rows if (r.get("text") or "").strip() and r.get("label") in RANK]
    print(f"판정면: 골든 후보 {len(rows)}건 · 모델 v-fe4b386b\n")

    svc = ClassifyService()
    out, t0 = [], time.perf_counter()

    for i, r in enumerate(rows):
        t = (r.get("text") or "").strip()
        rec = {"doc_id": r.get("doc_id"), "truth": r.get("label"), "chars": len(t)}
        for mult in (1, 2, 4):
            body = (t + "\n\n") * mult if mult > 1 else t
            try:
                res = svc.classify(ClassifyRequest(doc_id=f"{i}_{mult}", content=body,
                                                   return_evidence=False))
                rec[f"g{mult}"] = res.label.value if hasattr(res.label, "value") else str(res.label)
                rec[f"s{mult}"] = res.status
            except Exception as exc:  # noqa: BLE001
                rec[f"g{mult}"] = None
                rec[f"s{mult}"] = f"error:{type(exc).__name__}"
        out.append(rec)
        if (i + 1) % 20 == 0:
            print(f"  {i + 1}/{len(rows)} · {time.perf_counter() - t0:.0f}s")

    ok = [r for r in out if r.get("g1") and r.get("g2") and r.get("g4")]
    print(f"\n[결과] n={len(ok)} · {time.perf_counter() - t0:.0f}s\n")

    for mult in (2, 4):
        changed = [r for r in ok if r[f"g{mult}"] != r["g1"]]
        up = [r for r in changed if RANK[r[f"g{mult}"]] < RANK[r["g1"]]]
        down = [r for r in changed if RANK[r[f"g{mult}"]] > RANK[r["g1"]]]
        print(f"길이 {mult}배")
        print(f"  등급 변화        {len(changed)}/{len(ok)} = {len(changed)/len(ok):.1%}")
        print(f"    고등급 방향     {len(up)}건")
        print(f"    S3 방향        {len(down)}건")
        base_hit = sum(1 for r in ok if r["g1"] == r["truth"])
        mult_hit = sum(1 for r in ok if r[f"g{mult}"] == r["truth"])
        print(f"  정답 일치        1배 {base_hit}/{len(ok)} = {base_hit/len(ok):.1%}"
              f"  ->  {mult}배 {mult_hit}/{len(ok)} = {mult_hit/len(ok):.1%}")
        # 비밀 여부(2분류)로도 본다 - 실무에서 중요한 것은 이쪽이다
        b1 = sum(1 for r in ok if (r["g1"] in SECRET) == (r["truth"] in SECRET))
        bm = sum(1 for r in ok if (r[f"g{mult}"] in SECRET) == (r["truth"] in SECRET))
        print(f"  비밀/비비밀 일치  1배 {b1/len(ok):.1%}  ->  {mult}배 {bm/len(ok):.1%}")
        # 무음 미탐: 정답이 고등급인데 낮게 보고 자동확정
        miss1 = [r for r in ok if r["truth"] in SECRET
                 and RANK[r["g1"]] > RANK[r["truth"]] and r["s1"] != "needs_review"]
        missm = [r for r in ok if r["truth"] in SECRET
                 and RANK[r[f"g{mult}"]] > RANK[r["truth"]] and r[f"s{mult}"] != "needs_review"]
        print(f"  무음 미탐        1배 {len(miss1)}건  ->  {mult}배 {len(missm)}건")
        print()

    print("등급별 변화 (4배 기준)")
    bygrade = Counter(r["truth"] for r in ok if r["g4"] != r["g1"])
    tot = Counter(r["truth"] for r in ok)
    for g in ORDER:
        print(f"  {g}  {bygrade.get(g,0)}/{tot.get(g,0)}")

    Path("reports/LENGTH_EFFECT.json").write_text(
        json.dumps({"n": len(ok), "rows": out}, ensure_ascii=False, indent=2), "utf-8")
    print("\n[report] reports/LENGTH_EFFECT.json")


if __name__ == "__main__":
    main()
