#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""콘솔 검수 결정을 사람 서명(locked_gold_eval)으로 승격한다 — 검수 배치가 끝난 뒤 돌린다.

왜 이 도구가 있는가(2026-09-09). 승격 경로가 API 엔드포인트로만 있으면 배포 서버에서
부를 방법이 없다. 콘솔 화면에는 아직 이 동작을 거는 버튼이 없고, 검수 배치는 사람이
끝났다고 판단하는 시점에 한 번 묶어서 승격하는 것이 정상 운용이다.

무엇을 하는가. 후보 폴더의 결정 원장(candidate_decisions.jsonl)을 읽어, **등급을 확정한
결정**만 골라 golden_signoff.promote_to_locked 로 넘긴다. 배선의 전말은 koipa.console_signoff
참조 — 이 다리가 없던 동안 콘솔 검수는 평가정답을 한 건도 만들지 못했다.

  · 원장은 건드리지 않는다(투영). 여러 번 돌려도 결과가 같다.
  · 서명자는 **결정을 내린 검수자**다. 이 스크립트를 돌린 사람이 아니다.
  · 기본은 미리보기다. 파일을 쓰려면 --apply 를 준다.

사용:
    python scripts/promote_console_decisions.py                      # 미리보기
    python scripts/promote_console_decisions.py --apply              # 후보 폴더에 누적
    python scripts/promote_console_decisions.py --apply --publish    # 라이브 readiness 까지
    python scripts/promote_console_decisions.py --root <후보폴더>     # 배포본 경로 지정
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_POC = _HERE.parent
if str(_POC / "src") not in sys.path:
    sys.path.insert(0, str(_POC / "src"))

from koipa.services.proxy_gold_candidate_service import ProxyGoldCandidateService  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=None,
                    help="후보 폴더(기본: datasets/proxy_gold/single_document_candidates)")
    ap.add_argument("--apply", action="store_true",
                    help="실제로 쓴다. 주지 않으면 미리보기만 한다")
    ap.add_argument("--publish", action="store_true",
                    help="라이브 readiness 읽기경로(locked_eval_jsonl)까지 반영")
    ap.add_argument("--json", action="store_true", help="집계를 JSON 으로만 출력")
    args = ap.parse_args()

    out = ProxyGoldCandidateService(args.root).promote_decisions_to_locked(
        publish=args.publish, dry_run=not args.apply
    )

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0

    print(f"후보                {out['candidates']:>6,}건")
    print(f"등급 확정 결정      {out['promotable']:>6,}건")
    print(f"이번 승격           {out['locked']:>6,}건   {out['locked_by_grade'] or ''}")
    if out["rejected"]:
        print(f"거부                {out['rejected']:>6,}건   {out['rejected_reasons']}")
    print(f"누적 평가정답       {out['locked_total']:>6,}건")
    # 감리 회신에 적는 '실문서 평가정답 몇 건' 이 이 값이다. 합성 본문 서명은 locked tier
    # 에는 들어가지만 여기에는 안 잡힌다(golden_tiers.is_real_locked_eval).
    print(f"  └ 실문서          {out['real_locked']:>6,}건")
    print(f"기록 위치           {out['locked_path']}")
    if out["dry_run"]:
        print("\n※ 미리보기입니다 — 아무 파일도 쓰지 않았습니다. 반영하려면 --apply")
    if out["publish_note"]:
        print(f"※ {out['publish_note']}")
    elif out["published"]:
        print("※ 라이브 readiness 읽기경로에 반영했습니다")
    return 0


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    raise SystemExit(main())
