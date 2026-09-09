#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""평가셋 봉인 CLI — 봉인 · 개봉 · 상태 · 대조.

감리 회신 5(6)이 약속한 "평가셋 사전 봉인 → 1회 개봉 측정 → 재측정 시 재봉인" 절차의
실행 도구다. 규약과 상태 기계는 koipa.eval_seal 머리말에 있다.

사용:
    python scripts/eval_seal.py status
    python scripts/eval_seal.py seal <파일> --reason "1차 기준선 측정용" --owner "홍길동"
    python scripts/eval_seal.py open <파일> --reason "1차 기준선 측정" --owner "홍길동"
    python scripts/eval_seal.py verify <파일>

원장: poc/evidence/eval_seals.jsonl (append-only · 커밋 대상 · 감리 증적)
"""
from __future__ import annotations

try:
    from _cli_io import force_utf8_stdio  # noqa: E402
except ImportError:
    from scripts._cli_io import force_utf8_stdio  # noqa: E402

force_utf8_stdio()

import argparse  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

_HERE = Path(__file__).resolve().parent
_POC = _HERE.parent
if str(_POC / "src") not in sys.path:
    sys.path.insert(0, str(_POC / "src"))

from koipa.eval_seal import (  # noqa: E402
    LEDGER,
    SealViolation,
    all_states,
    file_sha256,
    open_seal,
    seal,
    state_of,
)

_LABEL = {
    "sealed": "봉인됨 — 측정하려면 개봉하십시오",
    "opened": "개봉됨 — 1회 측정할 수 있습니다",
    "consumed": "측정에 쓰임 — 다시 재려면 재봉인하십시오",
    "unsealed": "봉인 대상 아님 — 자유롭게 쓸 수 있습니다",
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_seal = sub.add_parser("seal", help="평가셋을 봉인한다(재봉인도 같은 명령)")
    p_seal.add_argument("path")
    p_seal.add_argument("--reason", required=True)
    p_seal.add_argument("--owner", required=True)

    p_open = sub.add_parser("open", help="봉인을 연다(측정은 아직)")
    p_open.add_argument("path")
    p_open.add_argument("--reason", required=True)
    p_open.add_argument("--owner", required=True)

    p_st = sub.add_parser("status", help="상태를 본다")
    p_st.add_argument("path", nargs="?")

    p_v = sub.add_parser("verify", help="봉인 이후 내용이 바뀌지 않았는지 대조한다")
    p_v.add_argument("path")

    args = ap.parse_args()

    try:
        if args.cmd == "seal":
            st = seal(args.path, reason=args.reason, owner=args.owner)
            print(f"봉인했습니다: {st.path}\n  sha256 {st.sha256}\n  원장 {LEDGER}")
        elif args.cmd == "open":
            st = open_seal(args.path, reason=args.reason, owner=args.owner)
            print(f"개봉했습니다: {st.path}\n  {_LABEL[st.state]}")
        elif args.cmd == "verify":
            st = state_of(args.path)
            if st.state == "unsealed":
                print(f"봉인 대상이 아닙니다: {st.path}")
                return 0
            now = file_sha256(args.path)
            same = now == st.sha256
            print(f"{st.path}\n  봉인 당시 {st.sha256}\n  지 금     {now}")
            print("  같습니다." if same else "  ⛔ 다릅니다 — 봉인 이후 내용이 바뀌었습니다.")
            return 0 if same else 1
        else:  # status
            states = [state_of(args.path)] if args.path else all_states()
            if not states:
                print(f"봉인 원장이 비어 있습니다: {LEDGER}")
                return 0
            for st in states:
                print(f"  {st.path}")
                print(f"    {_LABEL[st.state]}  · 기록 {st.events}건"
                      + (f" · 마지막 {st.last_at} ({st.last_owner})" if st.last_at else ""))
    except SealViolation as exc:
        print(f"⛔ {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
