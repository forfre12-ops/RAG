#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""표마다 **누가 읽고 쓰는지**를 세어 지재원 / 고객사 배치 근거를 만든다.

왜 도구인가 (2026-09-02). "이 표는 어느 서버에 두느냐"는 범위 질문이다. 기억나는
이름 몇 개를 grep 해서 답하면 빠진 표가 나오고, 그러면 같은 질문을 다시 받는다.
세는 것은 스크립트가 하고 판단은 사람이 한다 — 다시 물으면 다시 세지 말고 다시 돌린다.

    cd poc && python scripts/audit_table_placement.py            # 표 형태
    cd poc && python scripts/audit_table_placement.py --json     # 기계용

무엇을 세는가
    표 -> ORM 클래스              db/models.py 의 __tablename__ 에서 뽑는다
    표 -> 이 표를 만지는 모듈     ORM 클래스 참조 + 원시 SQL 의 표 이름
    모듈 -> 게이트                그 모듈이 어느 배포 플래그 뒤에 있는가

게이트는 config.py 실측이다(2026-09-02 확인):
    enable_training              full-train  에서만 True   -> 지재원 전용
    enable_incremental_retrain   onprem-local 에서만 True  -> 고객사 야간 증분

이 도구가 **정하지 못하는 것**. 정적 참조는 "만질 수 있다"까지만 말한다. 실제로
어느 노드에서 그 코드가 도는지는 라우터 등록 조건(app.py)과 운영 절차가 정한다.
그래서 결론 칸은 비워 두고 근거만 낸다 — 배치는 사람이 정한다.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_POC = _HERE.parent
_SRC = _POC / "src" / "koipa"
_MODELS = _SRC / "db" / "models.py"

# 모듈 경로 -> 그 코드가 도는 자리. app.py 등록 조건과 모듈 성격에서 읽었다.
#   train    학습 라우터/학습 모듈 — enable_training(지재원) 또는 enable_incremental_retrain(고객사)
#   synth    합성 생성 — enable_training 만(지재원 전용, 고객사에는 안 열림)
#   golden   골든셋 검수·서명 — 지재원 모델공장 절차
#   runtime  업로드·분류·검수·교정 — 어느 노드에서나 돈다
#   ops      파티션·리스너·관리 API 등 인프라
_GATE_RULES = [
    (r"modules/m4_training/", "train"),
    (r"api/training", "train"),
    (r"services/train", "train"),
    (r"tasks/train", "train"),
    (r"modules/m6_evaluation/", "train"),
    (r"synthes", "synth"),
    (r"api/golden|proxy_gold|golden_signoff|golden_tiers", "golden"),
    (r"services/partitions|db/listeners", "ops"),
    (r"api/admin", "ops"),
]


def _tables():
    """__tablename__ -> ORM 클래스 이름."""
    src = _MODELS.read_text(encoding="utf-8")
    out = {}
    cls = None
    for line in src.split("\n"):
        m = re.match(r"class ([A-Za-z_]+)\(Base\)", line)
        if m:
            cls = m.group(1)
        m2 = re.search(r"__tablename__ = \"([a-z_]+)\"", line)
        if m2 and cls:
            out[m2.group(1)] = cls
    return out


def _gate(rel):
    for pattern, gate in _GATE_RULES:
        if re.search(pattern, rel):
            return gate
    return "runtime"


def _scan(tables):
    """표마다 그것을 만지는 모듈과 게이트를 모은다."""
    by_class = {cls: tbl for tbl, cls in tables.items()}
    hits = defaultdict(set)
    scanned = 0

    for path in sorted(_SRC.rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        rel = path.relative_to(_SRC).as_posix()
        if rel == "db/models.py":
            continue                      # 정의 자체는 사용이 아니다
        scanned += 1
        text = path.read_text(encoding="utf-8", errors="replace")
        # 주석에 적힌 이름만 보고 "쓴다"고 세면 과다 계상된다. 주석 줄은 뺀다.
        code = "\n".join(ln for ln in text.split("\n") if not ln.lstrip().startswith("#"))
        for tbl in tables:
            if tbl in code:
                hits[tbl].add(rel)
        for cls, tbl in by_class.items():
            if re.search(r"\b" + cls + r"\b", code):
                hits[tbl].add(rel)

    out = {}
    for tbl, cls in sorted(tables.items()):
        mods = sorted(hits.get(tbl, ()))
        gates = defaultdict(int)
        for rel in mods:
            gates[_gate(rel)] += 1
        out[tbl] = {
            "orm_class": cls,
            "modules": mods,
            "module_count": len(mods),
            "gates": dict(sorted(gates.items())),
        }
    return out, scanned


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--table", default=None, help="한 표의 참조 모듈을 전부 본다")
    args = ap.parse_args()

    tables = _tables()
    if not tables:
        print("표를 하나도 못 읽었다 — models.py 경로를 확인할 것", file=sys.stderr)
        return 1
    result, scanned = _scan(tables)

    if args.table:
        info = result.get(args.table)
        if not info:
            print("그런 표가 없다: " + args.table, file=sys.stderr)
            return 1
        print(args.table + "  (" + info["orm_class"] + ")")
        for rel in info["modules"]:
            print("  [" + _gate(rel) + "] " + rel)
        return 0

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    print("=" * 78)
    print(" 표별 사용처 — 검사한 표 %d개 / src 파이썬 %d개" % (len(tables), scanned))
    print("=" * 78)
    print("%-30s %5s  %s" % ("TABLE", "MODS", "GATES (모듈 수)"))
    for tbl, info in result.items():
        gates = " · ".join("%s %d" % (g, n) for g, n in info["gates"].items()) or "-"
        print("%-30s %5d  %s" % (tbl, info["module_count"], gates))

    print()
    print("게이트 뜻 — train/synth/golden 은 모델공장(지재원) 쪽으로 기운다는 신호일 뿐")
    print("           배치를 결정하지는 않는다. runtime 은 어느 노드에서나 돈다.")
    print("한 표의 근거를 보려면: --table tb_training_runs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())