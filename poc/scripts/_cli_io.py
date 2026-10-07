# -*- coding: utf-8 -*-
"""콘솔 출구를 UTF-8 로 고정한다 — 스크립트 공용 정본.

왜(2026-09-07). 한국어 Windows 콘솔의 기본 코덱은 cp949 다. em dash(—)나 화살표(→)처럼
cp949 에 없는 문자를 한 번이라도 print 하면 **UnicodeEncodeError 로 스크립트가 통째로
죽는다.** 계산은 다 끝났는데 결과를 못 읽는다.

실측(2026-09-07):

    자기만의 출구 고정을 복사해 둔 스크립트   133개
    고정이 없는데 cp949 로 못 찍는 문자 출력   25개  <- 콘솔에서 죽는다

  죽는 25개에 검사기 넷(audit_doc_claims·audit_doc_runtime·audit_silent_exceptions·
  audit_table_placement)과 학습 CLI(p1_train_classifier)·보정(calibrate_classifier)·
  DR 훈련(dr_drill)·보존정리(purge_retention)가 들어 있었다. 검사기가 못 돌면 아무것도
  셀 수 없다.

같은 코드를 134번째로 복사하는 대신 여기 한 벌 둔다. scripts/ 는 스크립트를 실행하면
sys.path 에 자동으로 들어가므로 경로 설정 없이 import 된다(_pg_probe 와 같은 방식).

    from _cli_io import force_utf8_stdio
    force_utf8_stdio()

⚠ 문자를 쫓지 말 것. "em dash 를 쓰지 말자"로는 못 막는다 — 다음 사람이 화살표를 쓰고,
  그때 또 죽는다. 출구를 고정하는 것이 근본이다.
"""

from __future__ import annotations

import io
import sys

_UTF8_NAMES = ("utf-8", "utf8", "utf-8-sig")


def force_utf8_stdio() -> None:
    """stdout·stderr 를 UTF-8 로 바꾼다. 이미 UTF-8 이면 아무것도 하지 않는다.

    못 바꾸는 환경(파이프가 이미 닫혔거나 buffer 가 없는 스트림)에서도 **예외를 내지
    않는다** — 출력 설정 때문에 본작업이 죽으면 고치려던 것을 그대로 되풀이하는 셈이다.
    """
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            continue
        encoding = (getattr(stream, "encoding", "") or "").lower()
        if encoding in _UTF8_NAMES:
            continue
        buffer = getattr(stream, "buffer", None)
        if buffer is None:
            continue
        try:
            setattr(sys, name, io.TextIOWrapper(buffer, encoding="utf-8", errors="replace"))
        except (ValueError, OSError):
            continue
