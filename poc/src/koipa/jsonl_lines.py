"""JSONL 한 줄 = JSON 한 개 — 줄 구분자를 '\\n' 하나로 고정한다 (2026-09-22).

왜. `json.dumps(ensure_ascii=False)` 는 U+2028(줄 구분자)·U+2029(문단 구분자)·U+0085(NEL)를 이스케이프하지 않고 글자 그대로
쓴다. 그런데 `str.splitlines()` 는 이 세 문자를 줄 끝으로 본다 — 그래서 사유·본문에 그 문자가 든 줄은 읽을 때 둘로 쪼개져
둘 다 JSON 이 아니게 된다. 읽는 쪽이 파싱 실패를 건너뛰면(결정 원장) **기록됐다고 답하고도 결정이 사라지고**, 건너뛰지 않으면
(평가정답 잠금 파일) 배포 게이트가 읽는 파일에서 예외로 죽는다. 검수자가 웹·워드에서 사유를 복사해 넣거나 실문서 본문에 그
문자가 있으면 생긴다.

재현·범위(2026-09-22): poc/datasets 의 jsonl 473개(17.4억 자)에는 이 문자가 원문 그대로 든 파일이 0개 — 기존 데이터는 안전하고
새 자유 입력에서만 생기는 잠복 결함이다. ASCII 제어문자(\\x0b·\\x0c·\\x1c-\\x1e)는 json.dumps 가 이미 \\u000b 꼴로 이스케이프하므로 문제 없다.

고치는 방식은 양쪽이다.
  · 읽는 쪽 `split_lines` — 줄을 '\\n' 으로만 가른다. JSONL 은 우리가 쓰는 줄 단위 파일이고 줄 구분자는 '\\n' 뿐이다.
    종전 작성기가 원문 그대로 남긴 줄도 읽는다.
  · 쓰는 쪽 `dumps_line` — 그 세 문자만 JSON 이스케이프(\\u2028 …)로 쓴다. 파싱하면 같은 값이고, 이 문자가 없는 줄은 종전과 바이트가
    같다. 다른 곳의 splitlines() 독자도 안전해진다.
"""
from __future__ import annotations

import json
from typing import Any

# json.dumps(ensure_ascii=False) 가 이스케이프하지 않지만 str.splitlines() 는 줄바꿈으로 보는 문자.
# JSON 에서 이 문자들은 문자열 값·키 안에만 나타날 수 있으므로 이스케이프로 바꿔도 구조가 변하지 않는다.
_LINE_SEPARATOR_ESCAPES = {"\u2028": "\\u2028", "\u2029": "\\u2029", "\u0085": "\\u0085"}


def dumps_line(obj: Any, **kwargs: Any) -> str:
    """`json.dumps(obj, ensure_ascii=False, **kwargs)` 인데 U+2028·U+2029·U+0085 를 JSON 이스케이프로 쓴다(끝 개행은 호출자가 붙인다)."""
    text = json.dumps(obj, ensure_ascii=False, **kwargs)
    for char, escaped in _LINE_SEPARATOR_ESCAPES.items():
        text = text.replace(char, escaped)
    return text


def split_lines(text: str) -> list[str]:
    """JSONL 본문을 줄로 가른다 — '\\n' 으로만. `str.splitlines()` 를 쓰면 안 된다(위 설명). 빈 줄은 호출자가 걸러낸다."""
    return text.split("\n")
