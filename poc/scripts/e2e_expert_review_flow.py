#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""전문가 검수 사이트 e2e — 설치된 지재원 노드에 검수자 토큰으로 실제 호출해 본다.

■ 무엇을 보는가
  설치 직후 상태에서 외부 전문가가 겪는 경로가 서버 응답 수준에서 맞는지. 화면(jsdom)은 tests/e2e_console 이,
  서버 격리 규칙은 tests/test_golden_*.py 가 각각 보지만, **설치된 스택**에서 한 줄로 이어 보는 것은 이 도구다.

    1. 배정 강제 검수자에게 자기 50건만 보이고, 관리자는 지정 배치 1,000건을 본다
    2. 검수자(reviewer 전용 토큰) 응답에서 제안 등급·근거·정답 단서가 안 보인다(블라인드 기본 켜짐)
       · 문서 번호는 별칭(RV-…)이고 실 doc_id(MD-/MK-/MS1-/RF-…)가 응답 어디에도 없다
    3. 「제안 등급 그대로 확정」(approve)은 403이다. 쓰기 검증은 UAT에서만 opt-in한다
    4. 공유 API 키(system)로는 검수 결정을 못 쓴다(포털 JWT 만)
    5. 관리자 토큰으로는 제안 등급·실 doc_id 가 보이고 배정 API 가 열려 있다

■ 사용
    python scripts/e2e_expert_review_flow.py --base-url http://127.0.0.1:8000 \
        --reviewer-token <JWT> --admin-token <JWT> --api-key <키>

    # 현재 전문가 검수 운영(1,000건을 20명에게 50건씩 문서 단위로 배정)에서는
    # reviewer 는 자기 50건만, admin 은 지정 배치의 1,000건을 본다.
    # 다른 시험 배치를 쓸 때만 아래 기대값을 명시적으로 바꾼다.
    ... --review-batch expert_review_product_1000_20261010_rf \
        --expect-reviewer-total 50 --expect-batch-total 1000

■ 기본값은 읽기 전용이다. ``--write-probe`` 는 검수자 결정 1건을 원장에 **남기므로**,
  운영 1,000건이 아닌 별도 UAT 배치에서만 쓴다.
  종료 코드 0 = 전부 통과, 1 = 하나라도 실패.
"""

from __future__ import annotations

import argparse
import json
import re
import sys

import requests

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

# 검수자가 보면 독립 판정이 깨지는 필드(golden_reviewer_access.BLIND_HIDDEN_*).
ANSWER_KEYS = {"proposed_grade", "proposed_grade_basis", "by_proposed_grade", "by_final_grade", "document_path",
               "management_before", "grade_rationale", "intended_label"}
# 배치가 바뀌면 doc_id 접두사도 바뀐다(MD-, MK-, MS1-, RF- 등). 특정 옛 접두사만
# 찾으면 새 배치의 실 ID 누출을 놓친다. 별칭은 RV- 이므로 이 패턴에는 걸리지 않는다.
REAL_ID = re.compile(r"\b(?:MD|MK|MS1|RF)-\d{4}\b")


def walk_keys(obj) -> set[str]:
    out: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(k)
            out |= walk_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            out |= walk_keys(v)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--reviewer-token", required=True)
    ap.add_argument("--admin-token", default="")
    ap.add_argument("--api-key", default="")
    ap.add_argument("--review-batch", default="expert_review_product_1000_20261010_rf",
                    help="관리자가 검증할 활성 전문가 검수 배치")
    ap.add_argument("--expect-reviewer-total", type=int, default=50,
                    help="배정 강제 상태에서 이 검수자에게 보여야 할 문서 수")
    ap.add_argument("--expect-batch-total", type=int, default=1000,
                    help="관리자가 --review-batch 에서 봐야 할 문서 수")
    ap.add_argument("--write-probe", action="store_true",
                    help="UAT 전용: change 결정 1건을 실제 원장에 남겨 쓰기 권한도 점검")
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    base = args.base_url.rstrip("/") + "/api/v1/golden"
    rev = {"Authorization": f"Bearer {args.reviewer_token}"}
    adm = {"Authorization": f"Bearer {args.admin_token}"} if args.admin_token else {}
    results: list[dict] = []

    def check(name: str, ok: bool, detail: str = "") -> bool:
        results.append({"name": name, "ok": bool(ok), "detail": detail})
        print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
        return bool(ok)

    print("\n[1] 적재 확인 — 검수자 목록")
    r = requests.get(f"{base}/candidates", headers=rev, params={"limit": 200}, timeout=60)
    check("검수자 목록 200", r.status_code == 200, f"status={r.status_code} body={r.text[:100]}")
    lst = r.json() if r.status_code == 200 else {}
    cands = lst.get("candidates") or []
    total = (lst.get("summary") or {}).get("total")
    check(f"배정된 후보 총 {args.expect_reviewer_total}건", total == args.expect_reviewer_total,
          f"summary.total={total}")
    check("첫 쪽에 문서가 온다", len(cands) > 0, f"len={len(cands)}")

    print("\n[2] 블라인드 — 검수자 응답에 정답 단서가 없다")
    keys = walk_keys(lst)
    check("목록에 제안 등급·근거·집계 필드가 없다", not (keys & ANSWER_KEYS), f"발견={sorted(keys & ANSWER_KEYS)}")
    ids = [c.get("doc_id", "") for c in cands]
    check("문서 번호가 전부 별칭(RV-…)", bool(ids) and all(i.startswith("RV-") for i in ids), f"예={ids[:2]}")
    check("실 doc_id(MD-####)가 목록 어디에도 없다", not REAL_ID.search(json.dumps(lst, ensure_ascii=False)),
          "")
    first = ids[0] if ids else ""
    r = requests.get(f"{base}/candidates/{first}", headers=rev, timeout=60)
    det = r.json() if r.status_code == 200 else {}
    check("상세 200 · 본문이 온다", r.status_code == 200 and len(det.get("text") or "") > 100,
          f"status={r.status_code} 글자수={len(det.get('text') or '')}")
    check("상세에도 정답 단서 필드가 없다", not (walk_keys(det) & ANSWER_KEYS), f"발견={sorted(walk_keys(det) & ANSWER_KEYS)}")
    r = requests.get(f"{base}/candidates/summary", headers=rev, timeout=60)
    check("요약에 등급 분포가 없다", r.status_code == 200 and not (walk_keys(r.json()) & ANSWER_KEYS),
          f"발견={sorted(walk_keys(r.json()) & ANSWER_KEYS) if r.status_code == 200 else r.status_code}")
    r = requests.get(f"{base}/candidates", headers=rev, params={"grade": "S1"}, timeout=60)
    check("등급 필터는 검수자에게 닫혀 있다(422)", r.status_code == 422, f"status={r.status_code}")

    print("\n[3] 결정 권한 — 기본은 원장 비변경, UAT에서만 쓰기 탐침")
    r = requests.post(f"{base}/candidates/{first}/decision", headers=rev, json={"action": "approve"}, timeout=60)
    check("approve(제안 등급 그대로 확정) → 403", r.status_code == 403, f"status={r.status_code} body={r.text[:100]}")
    if args.write_probe:
        r = requests.post(f"{base}/candidates/{first}/decision", headers=rev,
                          json={"action": "change", "grade": "S2", "reason": "e2e UAT 쓰기 탐침"}, timeout=60)
        dec = r.json() if r.status_code == 200 else {}
        check("change(등급 직접 지정) → 200", r.status_code == 200, f"status={r.status_code} body={r.text[:120]}")
        check("응답이 자기 결정만 보인다(확정 S2)", (dec.get("final_grade") or dec.get("candidate", {}).get("final_grade")) in ("S2", None)
              and r.status_code == 200, f"final_grade={dec.get('final_grade')}")
        r = requests.get(f"{base}/candidates/decisions", headers=rev, timeout=60)
        ev = (r.json() or {}).get("events") if r.status_code == 200 else []
        check("결정 이력에 1건이 남고 별칭 번호다", r.status_code == 200 and len(ev) >= 1
              and all(str(e.get("doc_id", "")).startswith("RV-") for e in ev), f"events={len(ev)}")
    else:
        check("쓰기 탐침 생략(원장 비변경)", True, "--write-probe 없음")

    print("\n[4] 인증 — 공유 API 키로는 쓸 수 없다")
    if args.api_key:
        r = requests.post(f"{base}/candidates/{first}/decision", headers={"X-API-Key": args.api_key},
                          json={"action": "defer", "reason": "e2e"}, timeout=60)
        check("공유 API 키로 결정 쓰기 → 거부(401/403)", r.status_code in (401, 403), f"status={r.status_code} body={r.text[:100]}")
    r = requests.post(f"{base}/candidates/{first}/decision", json={"action": "defer", "reason": "e2e"}, timeout=60)
    check("인증 없이 결정 쓰기 → 401", r.status_code == 401, f"status={r.status_code}")

    if args.admin_token:
        print("\n[5] 관리자 시야")
        r = requests.get(
            f"{base}/candidates", headers=adm,
            params={"limit": 5, "review_batch": args.review_batch}, timeout=60,
        )
        al = r.json() if r.status_code == 200 else {}
        ac = al.get("candidates") or []
        check("관리자 목록 200", r.status_code == 200, f"status={r.status_code}")
        check(f"관리자 배치 후보 총 {args.expect_batch_total}건", al.get("total") == args.expect_batch_total,
              f"total={al.get('total')} batch={args.review_batch}")
        check("관리자에게는 제안 등급이 보인다", bool(ac) and all("proposed_grade" in c for c in ac), "")
        check("관리자에게는 실 doc_id가 보인다", bool(ac) and all(REAL_ID.fullmatch(str(c.get("doc_id", ""))) for c in ac),
              f"예={[c.get('doc_id') for c in ac[:2]]}")
        r = requests.get(f"{base}/assignments", headers=adm, timeout=60)
        check("배정 조회 API 200(관리자)", r.status_code == 200, f"status={r.status_code}")
        r = requests.get(f"{base}/assignments", headers=rev, timeout=60)
        check("배정 조회는 검수자에게 403", r.status_code == 403, f"status={r.status_code}")

    failed = sum(1 for x in results if not x["ok"])
    print(f"\n== 전문가 검수 e2e: {len(results) - failed}/{len(results)} 통과 · 실패 {failed} · 대상 {base} ==")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"base": base, "total": len(results), "failed": failed, "results": results}, f,
                      ensure_ascii=False, indent=2)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
