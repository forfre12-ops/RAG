#!/usr/bin/env python
"""학습셋·평가셋 누출 감사 CLI — 등급을 맞히는 데 본문 말고 다른 단서가 섞였는지 잰다.

``lloydk.dataset_leakage`` 는 빌드 게이트(build_kl_review_pool)에만 배선돼 있어, 이미 만들어
둔 학습셋·평가셋을 나중에 확인할 방법이 없었다. 이 스크립트가 그 자리를 채운다.

    python scripts/audit_dataset_leakage.py datasets/proxy_eval/**/development_200.jsonl
    python scripts/audit_dataset_leakage.py <jsonl>... --gate      # 임계 초과 시 exit 1
    python scripts/audit_dataset_leakage.py <jsonl>... --json out.json

읽기 전용이다 — 어떤 데이터도 고치지 않는다.

지표 (무작위 기대값 = 1/등급수 = 0.25)
  길이-only 1NN   글자 수만 보고 길이가 가장 가까운 **다른** 문서의 등급을 따라갔을 때 적중률.
                  1.0 = 등급별 길이 밴드가 완전히 갈려 있다 = 본문을 안 읽어도 맞힌다.
  tell 커버리지   한 등급에만 나오는 문장을 가진 문서 비율. 1.0 = 전 문서에 정답 문장이 있다.
  등급문자열      본문에 'TS'·'S1' 등이 남은 문서 수(검수 후보에서는 0 이어야 한다).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from lloydk.dataset_leakage import (  # noqa: E402
    DEFAULT_MAX_LENGTH_LEAK,
    DEFAULT_MAX_TELL_COVERAGE,
    GRADES,
    audit,
)

_TEXT_KEYS = ("text", "content", "body")
_LABEL_KEYS = ("label", "grade", "y")


def load_docs(path: Path) -> list[tuple[str, str]]:
    """jsonl → [(등급, 본문)]. 키 이름은 세대마다 달라 후보를 순서대로 본다."""
    docs: list[tuple[str, str]] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{path}:{lineno} JSON 파싱 실패: {exc}") from exc
        text = next((row[k] for k in _TEXT_KEYS if row.get(k)), "")
        grade = next((row[k] for k in _LABEL_KEYS if row.get(k)), None)
        if text and grade in GRADES:
            docs.append((grade, text))
    return docs


def main(argv: list[str] | None = None) -> int:
    # Windows cp949 콘솔에서 '·'·한글 출력 시 크래시 방지(weekly.py 와 동일 처리).
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 — 리다이렉트된 스트림은 그대로 둔다
            pass

    ap = argparse.ArgumentParser(description="학습셋·평가셋 누출 감사(읽기 전용)")
    ap.add_argument("paths", nargs="+", help="검사할 .jsonl 경로")
    ap.add_argument("--gate", action="store_true",
                    help="임계 초과 시 exit 1 (CI·빌드 게이트용)")
    ap.add_argument("--max-length-leak", type=float, default=DEFAULT_MAX_LENGTH_LEAK)
    ap.add_argument("--max-tell-coverage", type=float, default=DEFAULT_MAX_TELL_COVERAGE)
    ap.add_argument("--json", dest="json_out", help="지표를 JSON 으로 저장")
    ap.add_argument("--lengths", action="store_true", help="등급별 길이 밴드도 출력")
    args = ap.parse_args(argv)

    header = (f"{'셋':<44}{'n':>6}{'길이-only':>11}{'tell종':>8}"
              f"{'tell커버':>10}{'등급문자열':>11}")
    print(header)
    print("-" * len(header))

    reports: dict[str, dict] = {}
    violations: list[str] = []
    for raw in args.paths:
        path = Path(raw)
        if not path.is_file():
            print(f"{raw:<44}{'파일 없음':>46}")
            continue
        docs = load_docs(path)
        if not docs:
            print(f"{path.name:<44}{'등급·본문 인식 0건':>46}")
            continue
        rep = audit(docs)
        reports[str(path)] = rep
        flag_len = "!" if rep["length_only_1nn"] > args.max_length_leak else " "
        flag_tell = "!" if rep["tell_coverage"] > args.max_tell_coverage else " "
        print(f"{path.name:<44}{rep['documents']:>6}"
              f"{rep['length_only_1nn']:>10.3f}{flag_len}"
              f"{rep['tell_sentences']:>8}"
              f"{rep['tell_coverage']:>9.3f}{flag_tell}"
              f"{rep['grade_token_exposed']:>11}")
        if args.lengths:
            for grade in GRADES:
                band = rep["length_by_grade"].get(grade)
                if band:
                    print(f"      {grade}: n={band['n']:>5}  "
                          f"{band['min']:>6} ~ {band['max']:>6} 자 (중앙 {band['p50']})")
        if flag_len == "!":
            violations.append(f"{path.name}: 길이-only {rep['length_only_1nn']:.3f}")
        if flag_tell == "!":
            violations.append(f"{path.name}: tell 커버리지 {rep['tell_coverage']:.3f}")

    print(f"\n무작위 기대값 0.250 · 한계 길이-only {args.max_length_leak} · "
          f"tell 커버리지 {args.max_tell_coverage}  ('!' = 초과)")

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8")
        print("저장:", args.json_out)

    if violations:
        print(f"\n누출 {len(violations)}건:")
        for v in violations:
            print("  ·", v)
        if args.gate:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
