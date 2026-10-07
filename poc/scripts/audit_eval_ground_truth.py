#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""우리 '정답' 은 누가 정했는가 — 전수로 세어 등급을 매긴다.

## 왜 만들었나 (2026-09-13)

사용자 질문: "분류품질은 뭘 기준으로 판단하는거야? 우리가 기준이 있니?"

그때까지 우리가 "품질" 이라 부른 것은 세 평가면(holdout109·hardened42·golden100)에서
**정답 대비 틀린 비율**이었다. 그런데 그 정답을 누가 정했는지는 한 번도 세지 않았다.
세어 보니 448,445행 중 사람이 서명한 것은 **39건(0.009%)** 이었다.

한 번 세고 끝낼 일이 아니다. 실문서·서명이 들어오기 시작하면 이 비율이 움직이고,
**"지금 우리가 무엇을 주장할 수 있는가" 가 그 비율로 정해진다.** 그래서 도구로 남긴다.

## 정답 등급 — 이 사업에서 쓰는 정의

    GOLD     사람이 판단하고 서명했다. 신원이 남는다.
             -> 실무 성능을 주장할 수 있다. 단 표본 수 안에서만.
    SILVER   기계가 판정했으나 **여럿이 합의**했다(LLM 다수결·이중 라벨러 합의·판례 근거).
             -> 상대 비교(A vs B)에 쓴다. 실무 성능 주장에는 못 쓴다.
    BRONZE   한 기계가 단독으로 매겼거나, 생성기가 의도한 라벨을 그대로 정답이라 했다.
             -> 회귀 감시용. 성능 주장에 인용 금지.
    CIRCULAR 우리 규칙이 만든 라벨(규칙 역산·factor 상태 파생).
             -> **우리 규칙을 우리가 얼마나 재현하나** 를 잴 뿐이다. 성능이 아니다.
    REJECTED 라벨이 데이터 정의와 **모순됨이 확인됐다**(예: 공개문서인데 비밀등급).
             -> 학습·평가 어디에도 쓰면 안 된다. 신뢰도가 낮다는 뜻이 아니라 틀렸다는 뜻이다.

⚠ 등급은 라벨이 **맞다/틀리다** 가 아니라 **무엇을 주장할 수 있나** 를 정한다.
   BRONZE 라벨이 틀렸다는 뜻이 아니다. 그것으로 "재현율 90% 달성" 을 말할 수 없다는 뜻이다.
   REJECTED 만 예외다 — 이건 신뢰도가 아니라 **정의 위반이 실측으로 확인된 상태**다
   (2026-09-18: patent_proxy_nkt 가 SILVER 로 잘못 분류돼 있었다 — 이미 check_no_
   patent_proxy.py(2026-07-26)가 "공개특허=정의상 S3인데 S1/TS 로 라벨함"을 지적한
   데이터였는데, 이 표에는 반영이 안 돼 있었다. 근거: source='공개특허'인데 label='S1'
   인 모순 자체, grade_basis가 키워드매칭 하나뿐인 기계적 배정(다수 합의 흔적 없음)).

사용:

    PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe scripts/audit_eval_ground_truth.py
    ... --json reports/EVAL_GROUND_TRUTH.json
    ... --eval-only          # 실제로 채점에 쓰는 평가면만
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

POC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(POC / "src"))

from koipa.golden_tiers import (  # noqa: E402
    document_origin, evaluation_signoff_gaps, is_valid_signoff,
)

# 채점에 실제로 쓰는 평가면. 여기 없는 셋은 성능 주장에 등장하면 안 된다.
EVAL_SETS = {
    "holdout109": "datasets/gold_real/holdout_eval.jsonl",
    "hardened42": "datasets/gold_real/holdout_eval.hardened.jsonl",
    "golden100": "datasets/gold/golden100_labeled_v3.jsonl",
    "v5_clean/test": "datasets/labeled_p1_v5_clean/test.jsonl",
}

# label_source -> 등급. 모르는 값은 UNKNOWN 으로 드러낸다(조용히 BRONZE 로 넘기지 않는다).
TIER_BY_SOURCE = {
    # 사람
    "human_review": "GOLD",
    # 여럿이 합의
    "llm_judge_consensus": "SILVER",
    "rule_llm_agreement": "SILVER",
    "dual_labeler_agreement": "SILVER",
    "rubric_3judge_blind": "SILVER",
    "koipa_case_based": "SILVER",      # 판례 근거
    "public_definitive": "SILVER",     # 공개 원문이 등급을 명시
    "public_form_definitive": "SILVER",
    "nkt_designated": "SILVER",        # 국가핵심기술 지정 목록
    "provenance_gate_s3": "SILVER",    # 출처가 공개임이 확정
    # 2026-09-20 golden100 재구성(v3): 의도 라벨(target)과 독립된 블라인드 LLM 재판정이
    # 진짜로 일치하는지 실측(91.0%, TS 100%·S1 98%·S2 78%·S3 88% — 전부 review_status에
    # 정직하게 기록됨, 옛 v2처럼 rule=llm=target을 스크립트에 미리 박아넣지 않음).
    # 불일치 22건 전부 "더 민감한 등급으로" 방향(안전한 쪽)만 존재. 근거: memory
    # golden100-v3-rebuild-2026-09-20.
    # ⚠ 2026-09-20 저녁: 이 SILVER 는 "라벨이 본문과 독립 재판정에서 91.0% 일치"라는 라벨 근거이지 지름길 부재의
    # 증거가 아니다. 글자 2~4gram 로지스틱회귀가 5겹 교차검증 100.0%(라벨섞기 28.0%)로 등급을 맞힌다 — 그래서
    # SILVER 의 뜻("상대 비교(A vs B)에만")이 딱 맞는 셋이고 절대 성능 근거로는 못 쓴다.
    "golden100_v3_generator": "SILVER",
    # 기계 단독
    # ⛔ 2026-09-20: holdout_eval.jsonl에서 이 출처 22건이 rule_grade=S3인데도 LLM이
    # 덮어써 채택(agreement=False, 사람검수 없음)된 게 확인돼 정정됨(본문 직접확인,
    # 전부 실제 공개 판례·증권사 공개 시장리서치) — 정정된 행은
    # label_before_correction_2026_09_20 필드로 원래값 보존. 근거: memory
    # holdout109-llm-judge-overrode-rule-s3-2026-09-20. BRONZE 등급 자체는 유지(다른
    # llm_judge_primary 행까지 전수 확인한 건 아님).
    "llm_judge_primary": "BRONZE",
    "codex_review": "BRONZE",
    "synthetic_llm": "BRONZE",
    "generator_intended_label": "BRONZE",
    "curated_scenario": "BRONZE",
    "bilingual_en": "BRONZE",
    "rag_corpus_v2": "BRONZE",
    "needs_review": "BRONZE",
    # 우리 규칙이 만든 것
    "derived_from_factor_states": "CIRCULAR",
    "rule_from_content_svm": "CIRCULAR",
    # 정의 위반이 확인됨 — 학습·평가 배제 대상(위 REJECTED 설명 참고)
    "patent_proxy_nkt": "REJECTED",
}

TIER_ORDER = ("REJECTED", "GOLD", "SILVER", "BRONZE", "CIRCULAR", "UNKNOWN", "NONE")

CLAIM = {
    "REJECTED": "정의 위반 확인 — 학습·평가 배제. 신뢰도 문제가 아니라 라벨이 틀림",
    "GOLD": "평가 증거 구비 — 목표·대표성·누출·승인 진위 별도 확인 필요",
    "SILVER": "상대 비교(A vs B)에만",
    "BRONZE": "회귀 감시용 · 성능 주장 금지",
    "CIRCULAR": "우리 규칙 재현율일 뿐 · 성능 아님",
    "UNKNOWN": "출처 미상 — 분류표에 넣을 것",
    "NONE": "출처 기록 없음",
}


def tier_of(row: dict) -> tuple[str, str]:
    src = row.get("label_source")
    if not src:
        prov = row.get("label_provenance")
        if isinstance(prov, dict) and prov.get("method"):
            src = str(prov["method"])
    if not src:
        return "NONE", ""
    src = str(src)
    if src == "human_review":
        # A string or legacy signature is not a policy-grounded evaluation answer.
        if evaluation_signoff_gaps(row):
            return "UNKNOWN", src
        # Approved, independently reviewed reference cases may be GOLD too.
        # Their synthetic origin/scope must NOT become a customer performance claim.
    return TIER_BY_SOURCE.get(src, "UNKNOWN"), src


def scan(paths: dict[str, Path]) -> dict:
    out: dict[str, dict] = {}
    for name, p in paths.items():
        if not p.exists():
            out[name] = {"error": f"파일 없음: {p}"}
            continue
        tiers: collections.Counter[str] = collections.Counter()
        sources: collections.Counter[str] = collections.Counter()
        origins: collections.Counter[str] = collections.Counter()
        scopes: collections.Counter[str] = collections.Counter()
        signoff_missing = 0
        n = 0
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            n += 1
            t, s = tier_of(row)
            tiers[t] += 1
            origins[document_origin(row)] += 1
            scopes[str(row.get("evaluation_scope") or "unrecorded")] += 1
            if s:
                sources[s] += 1
            # 서명이 필요하다고 스스로 적어 놓고 서명이 없는 행
            if row.get("requires_human_signoff") and not is_valid_signoff(row):
                signoff_missing += 1
        out[name] = {
            "path": str(p.relative_to(POC)),
            "n": n,
            "tiers": {k: tiers[k] for k in TIER_ORDER if tiers[k]},
            "sources": dict(sources.most_common()),
            "document_origins": dict(origins), "evaluation_scopes": dict(scopes),
            "requires_signoff_but_unsigned": signoff_missing,
            # 가장 낮은 등급이 그 셋이 주장할 수 있는 한계를 정한다.
            "claim_ceiling": next(
                (k for k in reversed(TIER_ORDER) if tiers[k]), "NONE"
            ),
            "worst_tier": next(
                (k for k in ("REJECTED", "CIRCULAR", "UNKNOWN", "NONE", "BRONZE", "SILVER", "GOLD") if tiers[k]),
                "NONE",
            ),
        }
    return out


def scan_all_datasets() -> dict:
    tiers: collections.Counter[str] = collections.Counter()
    sources: collections.Counter[str] = collections.Counter()
    origins: collections.Counter[str] = collections.Counter()
    scopes: collections.Counter[str] = collections.Counter()
    files = rows = 0
    for p in (POC / "datasets").rglob("*.jsonl"):
        files += 1
        try:
            fh = p.open(encoding="utf-8")
        except Exception:
            continue
        with fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                rows += 1
                t, s = tier_of(row)
                tiers[t] += 1
                origins[document_origin(row)] += 1
                scopes[str(row.get("evaluation_scope") or "unrecorded")] += 1
                if s:
                    sources[s] += 1
    return {
        "files": files,
        "rows": rows,
        "tiers": {k: tiers[k] for k in TIER_ORDER if tiers[k]},
        "sources": dict(sources.most_common(30)),
        "document_origins": dict(origins), "evaluation_scopes": dict(scopes),
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--json", default=None, help="결과를 JSON 으로 저장")
    ap.add_argument("--eval-only", action="store_true", help="채점에 쓰는 평가면만 본다")
    args = ap.parse_args()

    paths = {k: POC / v for k, v in EVAL_SETS.items()}
    per_set = scan(paths)

    print("=" * 78)
    print("채점에 쓰는 평가면 — 정답을 누가 정했나")
    print("=" * 78)
    for name, d in per_set.items():
        if "error" in d:
            print(f"\n  [{name}] {d['error']}")
            continue
        print(f"\n  [{name}] {d['n']}건 · {d['path']}")
        for t in TIER_ORDER:
            if d["tiers"].get(t):
                print(f"      {t:9s} {d['tiers'][t]:>5}건   {CLAIM[t]}")
        if d["requires_signoff_but_unsigned"]:
            print(
                f"      ⚠ 스스로 '사람 서명 필요' 라 적어 놓고 서명이 없는 행: "
                f"{d['requires_signoff_but_unsigned']}건"
            )
        print(f"      => 이 셋으로 할 수 있는 주장: {CLAIM[d['worst_tier']]}")

    if not args.eval_only:
        allsets = scan_all_datasets()
        print("\n" + "=" * 78)
        print(f"전 데이터셋 — 파일 {allsets['files']}개 · 행 {allsets['rows']:,}개")
        print("=" * 78)
        total = allsets["rows"] or 1
        for t in TIER_ORDER:
            c = allsets["tiers"].get(t)
            if c:
                print(f"  {t:9s} {c:>9,}건 ({c/total*100:6.3f}%)   {CLAIM[t]}")
        unknown = [
            s
            for s in allsets["sources"]
            if s not in TIER_BY_SOURCE
        ]
        if unknown:
            print(f"\n  ⚠ 등급표에 없는 출처 {len(unknown)}종 — 분류해서 표에 넣을 것:")
            for s in unknown[:12]:
                print(f"      {allsets['sources'][s]:>8,}  {s}")
    else:
        allsets = None

    if args.json:
        out = POC / args.json
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(
                {"eval_sets": per_set, "all_datasets": allsets, "tier_map": TIER_BY_SOURCE},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
