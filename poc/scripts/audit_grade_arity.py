#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""등급 수가 4 로 고정된 자리를 센다 — FUN-005-01(등급 추가·변경·삭제) 영향 검토용.

왜 이 도구가 있는가(2026-09-10). 감리 별첨 177(마)가 "등급 추가·삭제 요구사항은 AI
분류모델의 재학습 필요성이 예상되므로 변경 영향을 검토하고 확정하라"고 권고했다.
`PUT /schema/grades` 는 DB(classification_levels)에 새 등급을 넣고
`requires_retraining=True` 만 돌려준다. 그런데 학습기 라벨 목록은 `Grade` 열거형(4개)에서
오므로 등급을 늘리거나 빼도 분류기는 여전히 4등급을 낸다. 그 영향 범위를 기억으로
말하지 않고 센다.

세는 것(범주별 파일 수 · 줄 수):
    label_list   학습·서빙 라벨 목록을 4등급으로 고정
    rank_table   등급 위험순위 표(TS=0 … S3=3) — 미탐/과분류 방향 판정의 기준
    upper_set    고등급 집합(TS·S1) — 미탐 판정·자동확정 금지 기준
    literal_cmp  특정 등급 문자열과의 직접 비교(== "TS" 등)
    enum_ref     Grade.TS 등 열거형 멤버 직접 참조
    head_dim     분류기 출력 차원(num_labels)

분모: `git ls-files -z src/koipa` 의 .py 전부(한글 경로 누락 방지로 -z).
주석 줄과 docstring 은 뺀다(ast 로 docstring 줄 범위를 구한다).
⚠ 한 줄이 여러 범주에 걸리면 범주마다 센다. 파일 수 합계는 합집합으로 따로 낸다.
⚠ 이 수는 "고칠 자리의 하한"이다. 등급 이름을 변수로 받아 쓰는 간접 참조는 못 잡는다.

사용:
    python scripts/audit_grade_arity.py                 # 요약
    python scripts/audit_grade_arity.py --list          # 줄 단위 목록
    python scripts/audit_grade_arity.py --json out.json
"""
from __future__ import annotations

import argparse
import ast
import io
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

_POC = Path(__file__).resolve().parents[1]

_G = r"""["'](?:TS|S1|S2|S3)["']"""
CATEGORIES: dict[str, re.Pattern] = {
    "label_list": re.compile(
        r"_LABEL_LIST"
        r"|[\[(]\s*[\"']TS[\"']\s*,\s*[\"']S1[\"']\s*,\s*[\"']S2[\"']\s*,\s*[\"']S3[\"']\s*[\])]"
        r"|Grade\.TS\s*,\s*Grade\.S1\s*,\s*Grade\.S2\s*,\s*Grade\.S3"
    ),
    "rank_table": re.compile(r"GRADE_RANK|[\"']TS[\"']\s*:\s*0\b|\b_?RANK\s*=\s*\{"),
    "upper_set": re.compile(
        r"\bUPPER\b|[\[({]\s*[\"']TS[\"']\s*,\s*[\"']S1[\"']\s*[\])}]"
    ),
    "literal_cmp": re.compile(r"(?:==|!=)\s*" + _G + r"|" + _G + r"\s*(?:==|!=)"),
    "enum_ref": re.compile(r"\bGrade\.(?:TS|S1|S2|S3)\b"),
    "head_dim": re.compile(r"\bnum_labels\b"),
}


def _files() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "-z", "src/koipa"], cwd=_POC,
                         capture_output=True, check=True).stdout
    return [(_POC / p.decode("utf-8")) for p in out.split(b"\0")
            if p and p.decode("utf-8").endswith(".py")]


def _docstring_lines(src: str) -> set[int]:
    lines: set[int] = set()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return lines
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(
                getattr(body[0], "value", None), ast.Constant
            ) and isinstance(body[0].value.value, str):
                lines.update(range(body[0].lineno, (body[0].end_lineno or body[0].lineno) + 1))
    return lines


def scan() -> dict:
    files = _files()
    hits: dict[str, list[tuple[str, int, str]]] = defaultdict(list)
    for f in files:
        src = f.read_text(encoding="utf-8", errors="replace")
        skip = _docstring_lines(src)
        rel = f.relative_to(_POC).as_posix()
        for i, line in enumerate(src.splitlines(), 1):
            s = line.strip()
            if not s or s.startswith("#") or i in skip:
                continue
            code = line.split("  #", 1)[0]
            for cat, pat in CATEGORIES.items():
                if pat.search(code):
                    hits[cat].append((rel, i, s[:140]))
    per_cat = {c: {"files": len({h[0] for h in hits[c]}), "lines": len(hits[c])} for c in CATEGORIES}
    union_files = sorted({h[0] for c in hits for h in hits[c]})
    by_file: dict[str, int] = defaultdict(int)
    for c in hits:
        for h in hits[c]:
            by_file[h[0]] += 1
    return {"denominator_files": len(files), "per_category": per_cat,
            "union_files": len(union_files),
            "top_files": sorted(by_file.items(), key=lambda kv: -kv[1])[:15],
            "hits": {c: hits[c] for c in CATEGORIES}}


def main(argv=None) -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="4등급 고정 자리 계수")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--json", default="")
    a = ap.parse_args(argv)
    r = scan()
    print(f"분모: src/koipa .py {r['denominator_files']}개 (git ls-files -z)")
    for c, v in r["per_category"].items():
        print(f"  {c:12s} 파일 {v['files']:3d} · 줄 {v['lines']:4d}")
    print(f"  합집합       파일 {r['union_files']:3d}")
    print("상위 파일:")
    for f, n in r["top_files"]:
        print(f"  {n:4d}  {f}")
    if a.list:
        for c, hs in r["hits"].items():
            print(f"\n[{c}]")
            for rel, i, s in hs:
                print(f"  {rel}:{i}  {s}")
    if a.json:
        Path(a.json).write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
