#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""검수 후보를 여러 검수자에게 무작위로, 겹치지 않게 나눠 배정한다.

AssignmentLedger(golden_reviewer_access.py)는 "누구에게 무엇을 배정할지" 관리자가
직접 정해서 한 건씩 넣는 수동 기능만 제공한다(POST /golden/assignments) — 후보군을
섞어서 N명에게 교집합 없이 나누는 기능은 없다. 이 스크립트가 그 위에 얹는 유일한
추가 로직이다(이상민, 영업비밀보호센터 주임, 요청 #3 "로그인 시 카테고리와 관계없이
무작위로 할당, 단 계정 간 데이터 중복 할당 불가" 대응).

같은 AssignmentLedger.apply() 를 직접 호출해 원장(candidate_assignments.jsonl)에
적으므로 API 서버를 띄우지 않아도 되고, 적은 뒤에는 콘솔 배정 화면(manage.html)에
그대로 보인다(원장 파일이 유일한 진실이기 때문).

겹치지 않는 이유: 이미 누군가에게 배정된 doc_id(문서 단위 배정 + 배정된 배치에 속한
문서)는 풀에서 먼저 뺀다 — 새 후보가 나중에 추가된 뒤 재실행해도 이미 배정된 문서가
다른 사람에게 다시 배정되지 않는다. 그 다음 남은 풀을 시드로 섞어 N명에게 최대한
균등하게(나머지는 앞쪽 사람부터 1건씩) 나눈다 — 한 문서는 정확히 한 묶음에만 들어가므로
묶음 간 교집합은 0이다.

사용(먼저 반드시 --dry-run으로 분배만 확인):
  python scripts/assign_golden_reviewers_randomly.py \
      --review-batch expert_review_mock1000_20261002 \
      --reviewers 김변호사,이교수,박포렌식 \
      --actor-id admin-script --seed 20261002 --dry-run

--dry-run 없이 실행하면 원장에 실제로 assign 이벤트가 쌓인다(원장은 append-only —
되돌리려면 `POST /golden/assignments/revoke` 로 unassign 이벤트를 추가해야 한다).
"""
from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

try:  # 스크립트로 직접 실행 - scripts/ 가 sys.path 에 들어온다
    from _cli_io import force_utf8_stdio  # noqa: E402
except ImportError:  # 패키지로 import - 릴리스 번들의 import 폐쇄 검사가 이 경로다
    from scripts._cli_io import force_utf8_stdio  # noqa: E402

force_utf8_stdio()

ROOT = Path(__file__).resolve().parent.parent
_SRC = ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from koipa.services.golden_reviewer_access import (  # noqa: E402
    EVENT_ASSIGN,
    AssignmentLedger,
    norm_id,
)
from koipa.services.proxy_gold_candidate_service import ProxyGoldCandidateService  # noqa: E402


def split_evenly(pool: list[str], n: int) -> list[list[str]]:
    """n묶음으로 최대한 균등하게 나눈다(나머지는 앞쪽부터 1건씩 더 받는다). 입력 순서를 그대로
    쪽지어 자르므로, 무작위성이 필요하면 호출 전에 pool 을 섞어 둔다."""
    if n <= 0:
        raise ValueError("n은 1 이상이어야 한다")
    base, extra = divmod(len(pool), n)
    out: list[list[str]] = []
    i = 0
    for k in range(n):
        size = base + (1 if k < extra else 0)
        out.append(pool[i : i + size])
        i += size
    return out


def already_assigned_doc_ids(ledger: AssignmentLedger, batches: dict[str, str | None]) -> set[str]:
    """지금 누군가에게 배정돼 있는 doc_id 전체(문서 단위 + 배정된 배치에 속한 문서)."""
    assigned: set[str] = set()
    for slot in ledger.state().values():
        assigned |= set(slot.get("doc_ids", set()))
        for batch_name in slot.get("review_batches", set()):
            assigned |= {d for d, b in batches.items() if b and norm_id(b) == batch_name}
    return assigned


def parse_reviewers(raw: str) -> list[str]:
    reviewers = [norm_id(r) for r in raw.split(",") if r.strip()]
    if len(reviewers) < 2:
        raise SystemExit("[error] --reviewers 에 최소 2명을 쉼표로 줘야 한다")
    if len(set(reviewers)) != len(reviewers):
        raise SystemExit(f"[error] --reviewers 에 중복된 이름이 있다: {reviewers}")
    return reviewers


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--review-batch", default="", help="이 배치로 후보군을 좁힌다(비우면 전체 후보)")
    ap.add_argument("--status", default="proposed", help="이 상태의 후보만 배정 대상(기본 proposed)")
    ap.add_argument("--reviewers", required=True, help="쉼표로 구분한 reviewer_id 목록(최소 2명)")
    ap.add_argument("--actor-id", required=True, help="배정한 사람(원장에 남는다)")
    ap.add_argument("--reason", default="전문가 자문 — 카테고리 무관 무작위 배정")
    ap.add_argument("--seed", type=int, default=None, help="재현 가능한 섞기(없으면 매번 다름)")
    ap.add_argument("--dry-run", action="store_true", help="원장에 쓰지 않고 분배 결과만 출력")
    ap.add_argument("--root", default="", help="후보 폴더(기본: 서비스 기본 경로)")
    args = ap.parse_args(argv)

    reviewers = parse_reviewers(args.reviewers)

    service = ProxyGoldCandidateService(root=args.root or None)
    ledger = AssignmentLedger(service.root)

    listing = service.list_candidates(
        review_batch=args.review_batch or None, status=args.status, limit=None,
    )
    all_doc_ids = sorted({c["doc_id"] for c in listing["candidates"]})

    batches = service.review_batch_index()
    assigned = already_assigned_doc_ids(ledger, batches)
    pool = [d for d in all_doc_ids if d not in assigned]

    print(
        f"후보 {len(all_doc_ids)}건 중 이미 배정된 {len(all_doc_ids) - len(pool)}건을 빼고 "
        f"{len(pool)}건을 {len(reviewers)}명에게 나눈다."
    )
    if not pool:
        print("나눌 후보가 없다(전부 이미 배정됨, 또는 대상 0건).")
        return 0

    rng = random.Random(args.seed)
    rng.shuffle(pool)
    groups = split_evenly(pool, len(reviewers))

    for reviewer, docs in zip(reviewers, groups):
        print(f"  {reviewer}: {len(docs)}건")

    if args.dry_run:
        print("--dry-run: 원장에 쓰지 않았다.")
        return 0

    for reviewer, docs in zip(reviewers, groups):
        if not docs:
            continue
        written, skipped_targets = ledger.apply(
            event=EVENT_ASSIGN,
            reviewer_id=reviewer,
            actor_id=args.actor_id,
            targets=[("doc_id", d) for d in docs],
            reason=args.reason,
        )
        print(f"  [기록] {reviewer}: {len(written)}건 적음, {len(skipped_targets)}건 건너뜀(이미 같은 상태)")

    print(f"\n완료. 원장 파일: {ledger.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
