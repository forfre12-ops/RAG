#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""API 소스가 HTTP 상태 코드를 직접 지정한 지점을 세고, API_정의서 §2-1 표와 맞춘다.

왜 필요한가(2026-09-26). 「API 정의서」 §2-1 「상태 코드 사용 분포」는 2026-08-26 에 집계한 값이었는데
어떻게 셌는지 남아 있지 않았다. 지금 소스를 정규식·AST 로 다시 세어 보니 표의 값과 코드 절반이 달랐고
(404 19→20 · 422 12→14 · 413 9→7 · 403 8→10 · 401 6→4 …), 표에 없는 400·500 도 소스에 있었다.
재현할 수 없는 숫자는 다음 사람이 다시 못 맞춘다. 그래서 세는 방법을 도구로 남긴다.

세는 것
  `src/koipa/api/` 의 파이썬 소스를 AST 로 읽어, 호출의 `status_code=` 키워드에 정수 상수(또는
  `status.HTTP_404_...`)를 준 지점을 코드별로 센다 — HTTPException · JSONResponse · HTMLResponse ·
  라우트 데코레이터(`@router.post(..., status_code=202)`)가 전부 해당한다.

세지 않는 것
  · 200 — 기본 성공 응답이다. HTML 화면 하나가 `status_code=200` 을 명시해도 사용 분포가 아니다.
  · FastAPI 가 요청 검증 실패에 자동으로 내는 422 — 소스에 지정 지점이 없다. 표의 422 는
    코드가 직접 낸 것(HTTPException(422))만이다.
  · 미들웨어가 예외 처리기에서 만드는 응답 중 `status_code=` 를 안 쓰는 것.

이 숫자는 "코드가 그 상태를 **낼 수 있는 지점**의 수"이지 운영 중 실제로 나간 횟수가 아니다.

사용:
    python scripts/audit_status_codes.py            # 코드별 지점 수와 위치
    python scripts/audit_status_codes.py --check    # API_정의서 §2-1 표와 대조(다르면 exit 1)
"""
from __future__ import annotations

try:  # 스크립트로 직접 실행 - scripts/ 가 sys.path 에 들어온다
    from _cli_io import force_utf8_stdio  # noqa: E402
except ImportError:  # 패키지로 import
    from scripts._cli_io import force_utf8_stdio  # noqa: E402

force_utf8_stdio()

import argparse
import ast
import re
import sys
from collections import defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent          # poc/
_API_DIR = _ROOT / "src" / "koipa" / "api"
_DOC = _ROOT.parent / "doc" / "감리문서" / "API_정의서.html"
_EXCLUDED = {200}                                        # 기본 성공 응답 — 사용 분포가 아니다


def _code_of(value: ast.AST) -> int | None:
    if isinstance(value, ast.Constant) and isinstance(value.value, int):
        return value.value
    if isinstance(value, ast.Attribute) and value.attr.startswith("HTTP_"):
        try:
            return int(value.attr.split("_")[1])            # status.HTTP_404_NOT_FOUND
        except (IndexError, ValueError):
            return None
    return None


def count_sites(api_dir: Path = _API_DIR) -> tuple[dict[int, list[tuple[str, int, str]]], int]:
    """{코드: [(파일, 줄, 호출 이름)]} 과 읽은 파일 수."""
    sites: dict[int, list[tuple[str, int, str]]] = defaultdict(list)
    files = sorted(p for p in api_dir.rglob("*.py") if "__pycache__" not in p.parts)
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rel = path.relative_to(api_dir).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = (node.func.id if isinstance(node.func, ast.Name)
                    else node.func.attr if isinstance(node.func, ast.Attribute) else "")
            for kw in node.keywords:
                if kw.arg != "status_code":
                    continue
                code = _code_of(kw.value)
                if code is not None and code not in _EXCLUDED:
                    sites[code].append((rel, node.lineno, name))
    return dict(sites), len(files)


def read_doc_table(doc: Path = _DOC) -> dict[int, int]:
    """API_정의서 §2-1 표에서 {코드: 사용 횟수}."""
    text = doc.read_text(encoding="utf-8")
    start = text.find("2-1. 상태 코드 사용 분포")
    if start < 0:
        raise SystemExit(f"§2-1 표를 못 찾았다: {doc}")
    end = text.find("</table>", start)
    rows = re.findall(r'<tr><td class="c">(\d{3})</td><td class="n">(\d+)</td>', text[start:end])
    return {int(c): int(n) for c, n in rows}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="API_정의서 §2-1 표와 대조한다")
    ap.add_argument("--where", action="store_true", help="코드별로 지정 위치를 모두 보인다")
    args = ap.parse_args(argv)

    sites, nfiles = count_sites()
    total = sum(len(v) for v in sites.values())
    print(f"분모: src/koipa/api/ 파이썬 {nfiles}개 · status_code= 지정 지점 {total}곳 (200 제외)")
    # 문서 표와 같은 순서 — 지점 수 내림차순, 같으면 코드 오름차순
    order = sorted(sites, key=lambda c: (-len(sites[c]), c))
    for code in order:
        print(f"  {code}  {len(sites[code]):3d}")
        if args.where:
            for rel, line, name in sites[code]:
                print(f"        {rel}:{line}  {name}")

    if not args.check:
        return 0
    doc = read_doc_table()
    counted = {c: len(v) for c, v in sites.items()}
    diffs = [(c, doc.get(c), counted.get(c)) for c in sorted(set(doc) | set(counted))
             if doc.get(c) != counted.get(c)]
    if not diffs:
        print(f"\nAPI_정의서 §2-1 표와 일치 — {len(doc)}개 코드 · {total}곳")
        return 0
    print("\nAPI_정의서 §2-1 표와 다르다 (코드 · 문서 · 소스):")
    for code, in_doc, in_src in diffs:
        print(f"  {code}  문서 {in_doc if in_doc is not None else '없음'} · 소스 {in_src if in_src is not None else '없음'}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
