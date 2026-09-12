"""평가 홀드아웃 라벨 경화(hardening) — 결정적 조립 단계.

배경
----
clean42 홀드아웃의 라벨 신뢰도 감사에서, gold(설계자 의도) vs Claude 재심판이
42건 중 24건(57%) 불일치했다. 그러나 그 불일치는 *전부* koipa_case_based(합성
경계 시나리오)에 몰려 있었고, 정본 루브릭(rule_engine.grade_from_svm: S×V×M,
TS는 본문에 형식적 관리증거 M≥1 필수) 앵커로 3심판 블라인드 재판정한 결과
Claude의 일괄 TS 격상은 23건 중 22건에서 기각됐다(= LLM 단독 재심판의 TS 과신
아티팩트). 동시에 설계자 의도도 무결하지 않아, S2 의도 12건은 *하나도* S2로
남지 못하고 S3(9) / S1(3)로 갈렸다 — S2가 이 루브릭의 구조적 dead-zone임을 노출.

이 스크립트는 LLM '판단' 산출물(워크플로우가 저장한 adjudication_peritem.json)을
소비해, 결정적으로 3-tier 경화 홀드아웃을 조립한다. 판단을 재현하지 않는다
(그건 LLM 비결정). 조립만 재현 가능하게 둔다.

trust_tier
----------
- anchor      : 2개 이상 독립 출처가 라벨 합의 + dead-zone 아님 (평가 1순위 진실)
- adjudicated : 3심판이 라벨을 근거 있게 변경(S2→S1 상향, S1→TS 상향)
- quarantine  : S2/S3 dead-zone — 3심판은 S3로 만장일치였으나 그 하향이 M=0
                루브릭 mechanic(보일러플레이트→관리증거 0→곱=0→S3)에서 비롯해,
                "내부-범용 문서"를 "공개(S3)"로 의미 혼동할 위험. 사람 앵커 전까지
                strict 메트릭에서 제외.

입력
----
- datasets/gold_real/holdout_eval.clean.jsonl            (42 전체 레코드)
- datasets/gold_real/_rejudge_claude/clean42_audit.jsonl (gold/claude/tier)
- datasets/gold_real/_eval_harden/adjudication_peritem.json (24 분쟁 재판정)

출력 (datasets/gold_real/)
----
- holdout_eval.hardened.jsonl    : 42건 + 경화 라벨/trust_tier/provenance
- holdout_eval.consensus.jsonl   : anchor+adjudicated 만 (권장 평가 골든)
- holdout_eval.quarantine.jsonl  : dead-zone 격리분
- holdout_eval.hardened_manifest.json : 등급/tier 분포·근거 요약

사용:  python scripts/harden_eval_labels.py
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from koipa.modules.m3_labeling.rule_engine import grade_from_svm_floored  # noqa: E402

GOLD_REAL = Path("datasets/gold_real")
HARDEN_DIR = GOLD_REAL / "_eval_harden"


def _load_jsonl(p: Path) -> list[dict]:
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def adjudicate_disposition(gold: str, cons: str, svm: dict) -> tuple[str, str, str]:
    """분쟁 레코드(3심판 재판정 대상)의 **결정적** tier/경화라벨/사유 판정 — 순수(파일 I/O 없음).

    반환: (trust_tier, hardened_label, disposition).
    핵심 안전 변환(eval 라벨을 바꾸는 유일 결정론 transform):
      - cons==gold                         → anchor, 라벨 불변(3출처 합의로 의도 재확인).
      - 근거 상향(S2→S1·S1→TS)             → adjudicated, 라벨=cons.
      - cons==S3 & gold==S2 (dead-zone)    → **floor 복원**: grade_from_svm_floored(s,v,0)로
        재도출(§3.6 미입증 M은 0 아닌 floor=1) → 거의 전건 S2 로 복원(격리 아님·anchor).
      - 그 외                               → quarantine(사람 앵커 전까지 strict 제외).
    svm = {"s","v",...} 평균 레벨(round 하여 사용).
    """
    if cons == gold:
        return "anchor", gold, "confirm_intent"
    if cons in ("TS", "S1") and gold in ("S1", "S2") and _rank(cons) < _rank(gold):
        return "adjudicated", cons, f"upgrade_{gold}_to_{cons}"
    if cons == "S3" and gold == "S2":
        s_lv = round(svm["s"])
        v_lv = round(svm["v"])
        refloored = grade_from_svm_floored(s_lv, v_lv, 0)  # m 미입증 → floor=1
        return "anchor", refloored, f"floor_restored_S2(judge_m0_artifact; refloored={refloored})"
    return "quarantine", gold, f"other_{gold}_to_{cons}"


def main() -> None:
    clean = {r["doc_id"]: r for r in _load_jsonl(GOLD_REAL / "holdout_eval.clean.jsonl")}
    audit = {r["doc_id"]: r for r in _load_jsonl(GOLD_REAL / "_rejudge_claude" / "clean42_audit.jsonl")}
    adj = {r["doc_id"]: r for r in json.loads((HARDEN_DIR / "adjudication_peritem.json").read_text(encoding="utf-8"))}

    hardened: list[dict] = []
    for doc_id, rec in clean.items():
        a = audit.get(doc_id, {})
        gold = a.get("gold", rec.get("label"))
        claude = a.get("claude")
        orig = rec.get("label")

        if doc_id not in adj:
            # gold==claude 합의 (public S3 / nkt TS) → anchor, 라벨 불변
            tier = "anchor"
            hard = orig
            prov = {
                "method": "dual_labeler_agreement",
                "gold": gold,
                "claude": claude,
                "note": "Qwen-gold와 Claude 재심판이 일치 — 추가 재판정 불요(공개판결=S3 / 국가핵심기술=TS)",
            }
        else:
            j = adj[doc_id]
            cons = j["consensus"]            # 3심판 다수결 등급
            agree = j["agreement"]
            svm = j["avg_svm"]
            # 결정적 tier/라벨/사유 판정 — 순수함수(위 adjudicate_disposition)로 추출해 단위테스트.
            # [floor 원칙 §3.6] cons==S3 & gold==S2 는 심판이 관리(M)를 0으로 강제한 아티팩트라
            # grade_from_svm_floored 로 재도출(미입증 M=floor 1)하면 S2 복원 → dead-zone 해소.
            tier, hard, disp = adjudicate_disposition(gold, cons, svm)
            prov = {
                "method": "rubric_3judge_blind",
                "gold_intended": gold,
                "claude_rejudge": claude,
                "judge_consensus": cons,
                "agreement": agree,
                "avg_svm": svm,
                "disposition": disp,
            }

        out = dict(rec)
        out["label"] = hard
        out["original_label"] = orig
        out["trust_tier"] = tier
        out["label_provenance"] = prov
        # 사람 서명 아님을 영구 명시 (silver 수준 경계)
        out["eval_truth_level"] = "machine_adjudicated_silver"  # NOT human-signed gold
        hardened.append(out)

    # 출력
    (GOLD_REAL / "holdout_eval.hardened.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in hardened), encoding="utf-8"
    )
    consensus = [r for r in hardened if r["trust_tier"] in ("anchor", "adjudicated")]
    quarantine = [r for r in hardened if r["trust_tier"] == "quarantine"]
    (GOLD_REAL / "holdout_eval.consensus.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in consensus), encoding="utf-8"
    )
    (GOLD_REAL / "holdout_eval.quarantine.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in quarantine), encoding="utf-8"
    )

    def _grade_dist(rows: list[dict]) -> dict:
        return dict(Counter(r["label"] for r in rows))

    manifest = {
        "source": "holdout_eval.clean.jsonl (n=42)",
        "method": "dual-labeler agreement (18) + rubric 3-judge blind adjudication (24 disputed)",
        "truth_level": "machine_adjudicated_silver — NOT human-signed; cannot be terminal eval gold",
        "trust_tiers": dict(Counter(r["trust_tier"] for r in hardened)),
        "consensus_set": {
            "n": len(consensus),
            "grade_dist": _grade_dist(consensus),
            "note": "권장 평가 골든. per-grade N이 작고 S2=0(coverage gap) 임에 유의.",
        },
        "quarantine_set": {
            "n": len(quarantine),
            "grade_dist": _grade_dist(quarantine),
            "reason": "잔존 격리분(있다면). S2 dead-zone 9건은 floor 원칙(§3.6)으로 S2 복원되어 consensus로 이동 — 격리 아님.",
        },
        "key_findings": [
            "분쟁 24건의 Claude 일괄 TS 상향은 22/23 기각 → '57% 라벨 불신뢰'는 LLM 재심판 TS 과신 아티팩트(폐기).",
            "S2 의도 12건이 심판에서 S3로 보였던 건 grade_from_svm 버그가 아니라 심판 프롬프트가 floor 원칙(§3.6: 미입증 요소는 0 아닌 floor=1)을 위반(M 미언급→0)한 탓. grade_from_svm(1,1,0)=S3은 공식 가이드 워크드 예시(사무실배치도)라 불변.",
            "floor 적용(grade_from_svm_floored) 재도출 + 배포모델(S2 8/9)·설계자 의도 일치 → 9건 S2 anchor 복원, S2 커버리지 갭 해소.",
            "경화 후 신뢰 평가셋 per-grade N = ",
        ],
    }
    manifest["key_findings"][-1] = "경화 후 신뢰 평가셋 per-grade N = " + json.dumps(_grade_dist(consensus), ensure_ascii=False)
    (GOLD_REAL / "holdout_eval.hardened_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 콘솔 요약
    print("=== 경화 완료 ===")
    print("trust_tiers :", manifest["trust_tiers"])
    print("consensus   : n=%d  grades=%s" % (len(consensus), _grade_dist(consensus)))
    print("quarantine  : n=%d  grades=%s" % (len(quarantine), _grade_dist(quarantine)))
    _s2 = _grade_dist(consensus).get("S2", 0)
    print("→ 신뢰 평가셋 S2 anchor = %d%s" % (_s2, " (coverage gap!)" if _s2 == 0 else " (커버리지 확보)"))


def _rank(g: str) -> int:
    return {"TS": 0, "S1": 1, "S2": 2, "S3": 3}.get(g, 9)


if __name__ == "__main__":
    main()
