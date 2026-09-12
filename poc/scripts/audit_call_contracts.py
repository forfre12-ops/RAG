#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""스크립트가 koipa 함수를 **없는 인자로 부르는가** — 돌려 보지 않고 센다.

왜 이 도구가 있는가(2026-09-06). `scripts/run_perf_scenarios.py` 가 이틀간 죽어 있었다.

    TypeError: capture_env() got an unexpected keyword argument 'vector_backend'

2026-09-04 커밋이 `perf/env.py` 에서 그 인자를 지웠는데 호출부가 남았다. 그 커밋은
"판정면 회귀 0" 으로 통과했다 — 회귀 게이트가 스크립트를 돌리지 않으니까. 그 사이
PER-002 성능 증빙은 2026-08-17 자 옛 보고서 그대로였다.

**스크립트는 시험이 안 돈다.** 라이브러리를 고치면 스크립트가 조용히 깨지고, 그 스크립트가
만들던 증빙은 낡은 채로 남는다. 돌려서 확인할 수는 없다 — 대부분 파일을 쓰거나 모델을
로드한다. 그래서 **부르지 않고** 인자 이름만 대조한다.

한계(정직하게):
  - `from koipa.x import f` 로 들여온 이름을 `f(...)` 로 직접 부르는 경우만 본다.
  - 메서드 호출(`obj.f(...)`)·동적 호출은 안 본다.
  - 인자 **이름**만 본다. 타입·값은 안 본다.
  그래도 하니스를 이틀 죽인 그 부류는 이걸로 잡힌다.

사용:
    python scripts/audit_call_contracts.py          # 요약
    python scripts/audit_call_contracts.py --list   # 어긋난 자리 전부
"""
from __future__ import annotations

import argparse
import ast
import importlib
import inspect
import io
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_POC = _HERE.parent
if str(_POC / "src") not in sys.path:
    sys.path.insert(0, str(_POC / "src"))


def _imported_koipa_names(tree: ast.AST) -> dict[str, str]:
    """{지역이름: 'koipa.모듈.원래이름'} — koipa 에서 직접 들여온 함수만."""
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or not node.module:
            continue
        if not node.module.startswith("koipa"):
            continue
        for alias in node.names:
            if alias.name == "*":
                continue
            out[alias.asname or alias.name] = "%s.%s" % (node.module, alias.name)
    return out


def _resolve(dotted: str):
    mod, _, name = dotted.rpartition(".")
    try:
        m = importlib.import_module(mod)
    except Exception:  # noqa: BLE001 — 무거운 선택 의존성은 건너뛴다(못 본 것은 못 봤다고 센다)
        return None
    return getattr(m, name, None)


def _accepted_kwargs(fn) -> tuple[set[str], bool] | None:
    """(받는 키워드 이름, **kwargs 여부). 시그니처를 못 읽으면 None."""
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return None
    names, star = set(), False
    for p in sig.parameters.values():
        if p.kind is inspect.Parameter.VAR_KEYWORD:
            star = True
        elif p.kind in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ):
            names.add(p.name)
    return names, star


def scan(py: Path) -> list[tuple[int, str, list[str], list[str]]]:
    """(줄번호, 함수, 안 받는 인자, 받는 인자) 목록."""
    try:
        tree = ast.parse(py.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    names = _imported_koipa_names(tree)
    if not names:
        return []

    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        dotted = names.get(node.func.id)
        if dotted is None:
            continue
        fn = _resolve(dotted)
        if fn is None or not callable(fn):
            continue
        acc = _accepted_kwargs(fn)
        if acc is None:
            continue
        accepted, star = acc
        if star:
            continue  # **kwargs 를 받으면 이름으로는 못 가린다
        passed = {kw.arg for kw in node.keywords if kw.arg}
        unknown = sorted(passed - accepted)
        if unknown:
            out.append((node.lineno, dotted, unknown, sorted(accepted)))
    return out


def main(argv=None) -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="스크립트 → koipa 호출 인자 계약 감사")
    ap.add_argument("--list", action="store_true", help="어긋난 자리 전부")
    ap.add_argument("--root", default=str(_HERE), help="검사할 디렉터리")
    a = ap.parse_args(argv)

    files = sorted(Path(a.root).glob("*.py"))
    bad: list[tuple[str, int, str, list[str], list[str]]] = []
    checked = 0
    for py in files:
        hits = scan(py)
        checked += 1
        for line, dotted, unknown, accepted in hits:
            bad.append((py.name, line, dotted, unknown, accepted))

    print("=" * 74)
    print(" 호출 인자 계약 감사 — 스크립트가 koipa 함수를 없는 인자로 부르는가")
    print("=" * 74)
    print("  검사한 스크립트     %5d 개" % checked)
    print("  어긋난 호출        %5d 건" % len(bad))
    if bad:
        print("")
        for name, line, dotted, unknown, accepted in bad:
            print("  %s:%d" % (name, line))
            print("      %s(...) 가 안 받는 인자: %s" % (dotted, unknown))
            if a.list:
                print("      받는 것: %s" % accepted)
    else:
        print("")
        print("  어긋난 자리 없음.")
    print("")
    print("  ⚠ 이름만 본다 — 메서드 호출·동적 호출·**kwargs 는 대상이 아니다.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
