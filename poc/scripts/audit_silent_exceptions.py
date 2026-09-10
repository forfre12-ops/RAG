#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""무음 예외 감사 — 예외를 삼키고 아무 흔적도 남기지 않는 자리를 센다.

왜 필요한가.
  except 로 잡고 폴백으로 넘어가는 코드는 대개 의도된 것이다. 문제는 **판정이
  달라지는데 흔적이 없는 경우**다. 설정값을 못 읽어 기본값으로 갔는지, 룰+LLM
  병합이 죽어 룰 단독으로 갔는지, 운영에서는 알 방법이 없다. 등급이 조용히
  바뀌고 로그에는 아무것도 없다.

무엇을 무음으로 보는가.
  예외 핸들러 본문에 아래가 **하나도 없으면** 무음으로 센다.
    - logging 호출        logger.warning / log.error / logging.exception ...
    - 재발생·전파         raise
    - 경고                warnings.warn
    - 계량                _record_gate_fail_open 등 metric 계열 호출
  `pass` 한 줄뿐인 경우가 대표적이고, `return None` · `continue` 만 있는 것도 포함한다.

무엇을 세지 않는가(의도적 제외).
  - ImportError / ModuleNotFoundError 만 잡는 핸들러: 선택 의존성 폴백이라
    로그가 오히려 소음이다. 별도로 세어 따로 보고한다.
  - 테스트 코드(tests/).

사용:
  python scripts/audit_silent_exceptions.py            # 요약
  python scripts/audit_silent_exceptions.py --list     # 파일:줄 전체
  python scripts/audit_silent_exceptions.py --path     # 판정 경로만
"""
from __future__ import annotations

# 콘솔 출구를 UTF-8 로 고정한다 — cp949 콘솔에서 em dash 하나에 죽던 것을 막는다.
# 정본은 scripts/_cli_io.py 한 곳이다(같은 코드가 133벌 복사돼 있었다).
try:  # 스크립트로 직접 실행 - scripts/ 가 sys.path 에 들어온다
    from _cli_io import force_utf8_stdio  # noqa: E402
except ImportError:  # 패키지로 import - 릴리스 번들의 import 폐쇄 검사가 이 경로다
    from scripts._cli_io import force_utf8_stdio  # noqa: E402

force_utf8_stdio()

import argparse
import ast
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent

# 판정 경로 — 문서 한 건의 등급이 정해지기까지 실제로 지나가는 모듈.
# 여기의 무음 예외는 "등급이 조용히 달라진다"와 같은 말이다.
DECISION_PATH = (
    "src/koipa/services/classify_service.py",
    "src/koipa/modules/m5_inference/",
    "src/koipa/modules/m3_labeling/",
    "src/koipa/modules/m2_preprocess/",
    "src/koipa/api/async_classify.py",
    "src/koipa/api/classify.py",
)

_LOG_NAMES = ("debug", "info", "warning", "warn", "error", "exception", "critical", "log")
# 함수 이름에 이 조각이 들어 있으면 흔적을 남기는 호출로 본다. 로거를 직접 부르지 않고
# 얇은 래퍼(_warn_setting_fallback · _record_gate_fail_open)를 두는 자리가 실제로 있어서,
# 이름만 보고 무음으로 세면 이미 고쳐 둔 곳까지 결함으로 잡힌다(2026-08-27 실측 오검).
_TRACE_HINTS = ("record", "metric", "counter", "observe", "gauge",
                "warn", "log", "report", "emit", "trace", "audit", "notify")


def _is_trace(node: ast.AST) -> bool:
    """이 노드가 '흔적을 남기는' 행위인가."""
    if isinstance(node, ast.Raise):
        return True
    if isinstance(node, ast.Return):
        return _return_carries_failure(node)
    if not isinstance(node, ast.Expr):
        return False
    call = node.value
    if not isinstance(call, ast.Call):
        return False
    f = call.func
    name = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else None)
    if name is None:
        return False
    low = name.lower()
    return low in _LOG_NAMES or any(h in low for h in _TRACE_HINTS)


# 반환값에 실패를 담는 패턴을 흔적으로 인정한다.
#
# 왜(2026-09-10 실측). 종전에는 로그·메트릭 **호출**만 흔적으로 봤다. 그래서
# extractor.py 의 이 코드가 '무음'으로 잡혔다:
#
#     return ([], [f"pdf_table_extractor_error:{type(exc).__name__}"])
#
# 호출자가 그 warnings 를 받아 검수 라우팅까지 태우는데도 결함으로 셌다. 이미 올바른
# 자리를 결함으로 세면 목록의 신뢰가 떨어지고, 고칠 자리를 가리게 된다.
#
# 키워드(error=·warnings=)와 **위치 인자 문자열**(위 예처럼 튜플 안에 사유를 담는 형태)
# 둘 다 본다. 실패를 뜻하는 낱말이 들어간 문자열 리터럴이 반환값 어딘가에 있으면 흔적이다.
_FAIL_KW = ("error", "warning", "warnings", "detail", "reason", "status", "quality")
_FAIL_WORD = re.compile(r"fail|error|unavailable|missing|incomplete|timeout|denied|invalid")


def _return_carries_failure(node: ast.Return) -> bool:
    if node.value is None:
        return False
    for sub in ast.walk(node.value):
        # error=... · warnings=[...] 처럼 이름 붙은 자리
        if isinstance(sub, ast.keyword) and sub.arg and sub.arg.lower() in _FAIL_KW:
            return True
        # 위치 인자로 사유 문자열을 담는 자리 — 상수든 f-string 이든
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            if _FAIL_WORD.search(sub.value.lower()):
                return True
        if isinstance(sub, ast.JoinedStr):
            lit = "".join(v.value for v in sub.values
                          if isinstance(v, ast.Constant) and isinstance(v.value, str))
            if _FAIL_WORD.search(lit.lower()):
                return True
    return False


def _only_import_error(handler: ast.ExceptHandler) -> bool:
    t = handler.type
    names: list[str] = []
    if isinstance(t, ast.Name):
        names = [t.id]
    elif isinstance(t, ast.Tuple):
        names = [e.id for e in t.elts if isinstance(e, ast.Name)]
    return bool(names) and all(n in ("ImportError", "ModuleNotFoundError") for n in names)


_METRIC_HINTS = ("prom_metrics", ".inc(", ".labels(", ".observe(", ".set(")


def _classify(try_body: list) -> str:
    """try 블록이 무엇을 감쌌는가 — 우선순위를 가르는 것은 이것이다.

    [2026-09-06] 종전에는 판정 경로 파일 안이면 전부 "등급이 조용히 달라질 수 있는 자리"로
    셌다. 실제로 열어 보니 메트릭 증가와 lazy import 가 절반 가까이였다. 그런 것이 섞여
    숫자가 커지면 목록을 아무도 안 연다.

        metric   메트릭 증가 실패 — 등급과 무관
        import   lazy import 폴백 — 등급과 무관(선택 의존성)
        other    그 밖 — 실제 검토 대상
    """
    import ast as _ast

    if not try_body:
        return "other"
    src = "\n".join(_ast.unparse(st) for st in try_body)
    if any(h in src for h in _METRIC_HINTS) and "settings" not in src:
        return "metric"
    if all(isinstance(st, (_ast.Import, _ast.ImportFrom)) for st in try_body):
        return "import"
    return "other"


def scan(py: Path) -> list[tuple[int, str, bool]]:
    """(줄번호, 예외타입, import폴백여부) 목록."""
    try:
        tree = ast.parse(py.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    out = []
    for tnode in ast.walk(tree):
        if not isinstance(tnode, ast.Try):
            continue
        for node in tnode.handlers:
            if any(_is_trace(st) for st in ast.walk(node)
                   if isinstance(st, (ast.Raise, ast.Expr, ast.Return))):
                continue
            t = node.type
            label = ast.unparse(t) if t is not None else "bare except"
            # 분류는 **감싼 것**을 보고 정한다 — 핸들러가 아니라 try 본문이다.
            out.append((node.lineno, label, _only_import_error(node), _classify(tnode.body)))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="파일:줄 전체 출력")
    ap.add_argument("--path", action="store_true", help="판정 경로만")
    args = ap.parse_args(argv)

    files = sorted(p for p in (_REPO / "src").rglob("*.py"))
    scripts = sorted(p for p in (_REPO / "scripts").rglob("*.py"))

    total_handlers = 0
    rows: list[tuple[str, int, str, bool, bool, str]] = []  # rel, line, label, import_fb, on_path, kind
    for py in files + scripts:
        rel = py.relative_to(_REPO).as_posix()
        try:
            total_handlers += sum(
                1 for n in ast.walk(ast.parse(py.read_text(encoding="utf-8")))
                if isinstance(n, ast.ExceptHandler)
            )
        except SyntaxError:
            pass
        on_path = any(rel.startswith(d) for d in DECISION_PATH)
        for line, label, imp, kind in scan(py):
            rows.append((rel, line, label, imp, on_path, kind))

    silent = [r for r in rows if not r[3]]
    imp_fb = [r for r in rows if r[3]]
    on_path = [r for r in silent if r[4]]
    # [2026-09-06] 판정 경로 안이라고 다 "등급이 달라질 수 있는 자리"가 아니다. 실제로
    # 열어 보니 메트릭 증가와 lazy import 가 절반 가까이였고, 손으로 다 읽은 22건 중
    # 진짜 결함은 1건이었다. 세는 것과 고를 것이 달랐다 — 여기서 갈라 놓는다.
    review = [r for r in on_path if r[5] == "other"]
    metric_only = [r for r in on_path if r[5] == "metric"]
    lazy_import = [r for r in on_path if r[5] == "import"]

    print("=" * 74)
    print(" 무음 예외 감사")
    print("=" * 74)
    print(f"  전체 except 핸들러      {total_handlers:>5} 개  (src/ + scripts/)")
    print(f"  흔적 없는 핸들러        {len(silent):>5} 개  ({len(silent)/max(total_handlers,1)*100:.1f}%)")
    print(f"    ├ 판정 경로          {len(on_path):>5} 개")
    print(f"    │   ├ 검토 대상      {len(review):>5} 개  <- 등급이 조용히 달라질 수 있는 자리")
    print(f"    │   ├ 메트릭만       {len(metric_only):>5} 개     증가 실패 — 등급과 무관")
    print(f"    │   └ lazy import   {len(lazy_import):>5} 개     선택 의존성 폴백 — 등급과 무관")
    print(f"    └ 그 밖              {len(silent)-len(on_path):>5} 개")
    print(f"  선택 의존성 폴백(제외)  {len(imp_fb):>5} 개  ImportError 전용 - 로그가 소음")
    print("")
    print("  판정 경로 정의:")
    for d in DECISION_PATH:
        print(f"    {d}")

    # --path 는 **검토 대상만** 보여준다. 메트릭·lazy import 를 섞으면 목록이 길어져
    # 사람이 안 연다. 전부 보려면 --list 다.
    show = review if args.path else silent
    if args.list or args.path:
        print("")
        print("-" * 74)
        cur = None
        for rel, line, label, _imp, p, kind in sorted(show):
            if rel != cur:
                print(f"\n  {rel}")
                cur = rel
            tag = "   [판정경로]" if p else ""
            if kind != "other":
                tag += f" ({kind})"
            print(f"    :{line:<6} except {label}{tag}")
    else:
        print("")
        print("  --list 로 전체, --path 로 판정 경로만 본다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
