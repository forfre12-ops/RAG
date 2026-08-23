"""ICD 메타데이터가 들어올 때 무엇이 열리는지 잰다 — 커버리지·오류율을 바꿔 가며.

왜. 관리성을 문서 밖에서 받기로 했고(ICD §3.2·§3.3) 매핑은 배포본에 구현했다. 그런데
**실문서 판정면에는 메타데이터가 없다** — gold_real 어느 파일에도 security_marking /
access_scope 가 없다. 그래서 실문서로는 효과를 잴 수 없다.

합성 판정면은 참 관리성을 안다. 고객사 시스템이 접근권한을 정확히 준다면 그 값이
곧 참값이므로, 참 M 에서 ICD 값을 역산해 주입하면 **"메타데이터가 올 때" 를 충실히
모사**한다. 다만 그것은 이상적인 경우이므로 두 축을 함께 흔든다:

    coverage   메타데이터가 오는 문서의 비율 (KL 이 전건을 줄지 모른다)
    error      틀린 값이 오는 비율 (등록정보가 실제와 어긋날 수 있다)

⚠ 이 수치는 **배선의 효과**이지 모델 성능이 아니다. 메타데이터가 참값이면 M 이 맞는 것은
   당연하다. 여기서 답하려는 질문은 하나다 — 그 배선이 등급을 얼마나 열어 주는가,
   그리고 **틀린 메타데이터가 오면 미탐이 생기는가**.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT / "src"), str(_ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

GRADES = ("TS", "S1", "S2", "S3")
ORDER = {g: i for i, g in enumerate(GRADES)}
CLS_ABSENT, CLS_LV1, CLS_LV2, CLS_UNKNOWN = 0, 1, 2, 3
DOWNGRADE = (CLS_ABSENT, CLS_LV1)

# 참 M -> ICD 값. 실제 고객사 시스템이 보낼 법한 값으로 되돌린다.
_M_TO_ICD = {
    2: {"security_marking": "secret", "access_scope": "approved_only"},
    1: {"security_marking": "confidential", "access_scope": "department"},
    0: {"security_marking": "none", "access_scope": "all_employees"},
}
# 틀린 값이 올 때 — 한 칸 어긋난 값을 준다(전혀 무관한 값보다 현실적이다).
_M_WRONG = {2: 1, 1: 0, 0: 1}


def wilson_upper(k: int, n: int, z: float = 1.96) -> float:
    if n == 0:
        return 1.0
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return min(1.0, (c + r) / d)


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="ICD 메타데이터 주입 모사")
    ap.add_argument("--records", default="reports/V8_REC_caus.jsonl")
    ap.add_argument("--tau", type=float, default=0.99)
    ap.add_argument("--kappa", type=float, default=0.99)
    ap.add_argument("--report", default="reports/V8_ICD_SIM.json")
    args = ap.parse_args(argv)

    from koipa.modules.m3_labeling.rule_engine import (
        grade_from_svm,
        management_from_metadata_dict,
    )
    from v8_judge import cls_to_worst

    recs = [json.loads(l) for l in Path(args.records).read_text("utf-8").splitlines() if l.strip()]
    print(f"[records] {args.records} — {len(recs)}건\n")

    def sim(coverage: float, error: float) -> dict:
        grade: list[str] = []
        auto: list[int] = []
        applied = 0
        for i, r in enumerate(recs):
            c3 = list(r["pred_codes"])
            probs = r["head_dist"]
            for k in range(3):
                if c3[k] in DOWNGRADE and probs[k][c3[k]] < args.kappa:
                    c3[k] = CLS_UNKNOWN
            # 결정적 주입 — doc_id 해시로 커버리지·오류를 가른다(재실행 시 동일)
            h = int(hashlib.sha256(r["doc_id"].encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
            he = int(hashlib.sha256(("e" + r["doc_id"]).encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
            if h < coverage:
                true_m = cls_to_worst(r["truth_codes"][2]) if r["truth_codes"][2] != CLS_UNKNOWN else None
                if r["truth_codes"][2] == CLS_ABSENT:
                    true_m = 0
                if true_m is not None:
                    send = _M_WRONG[true_m] if he < error else true_m
                    st, lv, _ = management_from_metadata_dict(_M_TO_ICD[send])
                    if st == "present":
                        c3[2] = lv
                    elif st == "proven_absent":
                        c3[2] = CLS_ABSENT
                    applied += 1
            g = grade_from_svm(*[cls_to_worst(c) for c in c3])
            grade.append(g)
            settled = all(c != CLS_UNKNOWN for c in c3)
            if g == "TS" or (settled and min(max(d) for d in probs) >= args.tau):
                auto.append(i)
        truth = [r["truth"] for r in recs]
        miss = [i for i in auto if ORDER[grade[i]] > ORDER[truth[i]]]
        acc = sum(1 for a, b in zip(truth, grade) if a == b) / len(recs)
        s1_hit = sum(1 for i, t in enumerate(truth) if t == "S1" and grade[i] == "S1")
        s1_n = sum(1 for t in truth if t == "S1")
        return {"acc": round(acc, 4), "auto": len(auto),
                "auto_rate": round(len(auto) / len(recs), 4),
                "miss": len(miss), "miss_upper": round(wilson_upper(len(miss), len(auto)), 5),
                "applied": applied, "s1_recall": round(s1_hit / s1_n, 4) if s1_n else None,
                "dist": dict(Counter(grade))}

    out: dict = {"records": args.records, "tau": args.tau, "kappa": args.kappa, "runs": []}
    print(f"{'커버리지':>8s}{'오류율':>7s}{'적용n':>7s}{'4등급':>8s}{'S1재현':>8s}"
          f"{'자동확정':>9s}{'무음미탐':>9s}{'미탐상한':>10s}")
    for cov, err in ((0.0, 0.0), (0.5, 0.0), (1.0, 0.0), (1.0, 0.05), (1.0, 0.20), (0.5, 0.20)):
        r = sim(cov, err)
        out["runs"].append({"coverage": cov, "error": err, **r})
        print(f"{cov:>8.0%}{err:>7.0%}{r['applied']:>7d}{r['acc']:>8.4f}"
              f"{(r['s1_recall'] or 0):>8.4f}{r['auto_rate']:>9.1%}{r['miss']:>9d}{r['miss_upper']:>10.5f}")
    Path(args.report).write_text(json.dumps(out, ensure_ascii=False, indent=2), "utf-8")
    print(f"\n[report] {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
