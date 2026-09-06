"""cp949 콘솔에서 죽는 스크립트가 없는가 — 출구 고정 잠금.

왜(2026-09-07). 한국어 Windows 콘솔의 기본 코덱은 cp949 다. em dash(—)나 화살표(→)처럼
cp949 에 없는 문자를 한 번이라도 print 하면 **UnicodeEncodeError 로 스크립트가 통째로
죽는다.** 계산은 다 끝났는데 결과를 못 읽는다.

같은 사고가 이 리포에서 최소 두 번 났다:

    audit_unused.py            절 제목의 em dash 하나에 죽었다 (2026-09-05 고침)
    audit_silent_exceptions.py 새로 넣은 출력줄에서 **다시** 났다 (2026-09-07)

실측(2026-09-07): 자기만의 출구 고정을 복사해 둔 스크립트가 133개, 고정이 없는데
cp949 로 못 찍는 문자를 출력하는 것이 25개였다. 죽는 25개에 검사기 넷과 학습 CLI·
보정·DR 훈련·보존정리가 들어 있었다 — **검사기가 못 돌면 아무것도 셀 수 없다.**

이 시험은 "문자를 쓰지 마라"고 하지 않는다. 그건 못 지킨다 — 다음 사람이 화살표를 쓴다.
**출구를 고정했는가**만 본다.
"""
from __future__ import annotations

import io
import re
from pathlib import Path

_POC = Path(__file__).resolve().parents[1]
SCRIPTS = _POC / "scripts"

# 출구를 고정하는 방법 세 가지. 정본은 _cli_io.force_utf8_stdio 이고, 나머지 둘은
# 이미 각자 복사해 둔 스크립트들이 쓰는 형태다(133개 — 되돌리지 않는다).
_GUARD = re.compile(
    r"force_utf8_stdio|TextIOWrapper\([^)]*utf-8|reconfigure\(encoding=[\"']utf-8"
)


def _prints_non_cp949(text: str) -> list[str]:
    """cp949 로 인코딩할 수 없는 문자를 **출력하는** 줄만 고른다.

    주석·docstring 은 보지 않는다 — 그건 찍히지 않으므로 죽지 않는다.
    """
    bad: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not (stripped.startswith(("print(", "log(")) or '.append(f"' in stripped):
            continue
        try:
            line.encode("cp949")
        except UnicodeEncodeError:
            bad.append(stripped[:70])
    return bad


def test_every_printing_script_fixes_the_console_encoding() -> None:
    offenders: dict[str, list[str]] = {}
    for path in sorted(SCRIPTS.glob("*.py")):
        text = io.open(path, encoding="utf-8", errors="replace").read()
        if _GUARD.search(text):
            continue
        bad = _prints_non_cp949(text)
        if bad:
            offenders[path.name] = bad[:2]

    assert not offenders, (
        "cp949 콘솔에서 UnicodeEncodeError 로 죽는 스크립트가 있다. 문자를 바꾸지 말고 "
        "출구를 고정할 것 — scripts/_cli_io.py 의 force_utf8_stdio() 를 부른다:\n"
        + "\n".join(f"  {name}: {lines}" for name, lines in offenders.items())
    )


def test_canonical_guard_is_idempotent_and_safe() -> None:
    """이미 UTF-8 이면 건드리지 않고, 못 바꾸는 스트림에서도 예외를 내지 않는다.

    출력 설정 때문에 본작업이 죽으면 고치려던 것을 그대로 되풀이하는 셈이다.
    """
    import sys

    sys.path.insert(0, str(SCRIPTS))
    try:
        from _cli_io import force_utf8_stdio  # noqa: PLC0415
    finally:
        sys.path.remove(str(SCRIPTS))

    before_out, before_err = sys.stdout, sys.stderr
    force_utf8_stdio()
    force_utf8_stdio()  # 두 번 불러도 스트림을 겹겹이 감싸지 않는다
    assert (getattr(sys.stdout, "encoding", "") or "").lower().startswith("utf-8")
    # pytest 는 이미 UTF-8 로 잡아 두므로 스트림 자체가 바뀌지 않아야 한다.
    assert sys.stdout is before_out
    assert sys.stderr is before_err
