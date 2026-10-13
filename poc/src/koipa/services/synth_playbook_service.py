"""관리자 편집형 합성 생성 규칙 저장소.

코드에 들어 있는 기본 안전 규칙은 배포물의 일부라 여기서 지우지 않는다. 이 저장소는
조직의 문서 작성 관행을 반영하는 *추가* 규칙과 반복 금지 표현만 관리한다. API와 Celery
worker가 함께 마운트하는 ``datasets`` 아래에 두므로, 콘솔에서 저장한 규칙이 다음 배치
생성에도 같은 내용으로 적용된다.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

from koipa.config import settings

logger = logging.getLogger(__name__)

_MAX_RULES = 50
_MAX_AVOID_PHRASES = 100
_MAX_RULE_LENGTH = 2000
_MAX_PHRASE_LENGTH = 200


class PlaybookValidationError(ValueError):
    """관리자가 저장하려는 규칙의 형식이 허용 범위를 벗어났다."""


def _path() -> Path:
    return Path(getattr(settings, "synth_playbook_path", "datasets/runtime/synth_playbook.json"))


def _clean_items(
    values: list[str] | tuple[str, ...] | None,
    *,
    name: str,
    maximum: int,
    max_length: int,
) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, (list, tuple)):
        raise PlaybookValidationError(f"{name}은(는) 목록이어야 합니다")
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            raise PlaybookValidationError(f"{name} 항목은 문자열이어야 합니다")
        text = value.strip()
        if not text:
            continue
        if len(text) > max_length:
            raise PlaybookValidationError(
                f"{name} 항목은 {max_length}자를 넘길 수 없습니다"
            )
        if text not in seen:
            out.append(text)
            seen.add(text)
    if len(out) > maximum:
        raise PlaybookValidationError(f"{name}은(는) 최대 {maximum}개까지 저장할 수 있습니다")
    return out


def load_override(*, path: Path | None = None) -> dict[str, Any]:
    """런타임 추가 규칙을 읽는다. 손상·부재는 기본 규칙만 쓰는 안전한 폴백이다."""
    target = path or _path()
    if not target.exists():
        return {"custom_rules": [], "custom_avoid_phrases": [], "updated_at": None, "updated_by": None}
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("JSON 최상위가 객체가 아님")
        return {
            "custom_rules": _clean_items(
                raw.get("custom_rules"), name="추가 규칙", maximum=_MAX_RULES, max_length=_MAX_RULE_LENGTH
            ),
            "custom_avoid_phrases": _clean_items(
                raw.get("custom_avoid_phrases"), name="반복 금지 표현",
                maximum=_MAX_AVOID_PHRASES, max_length=_MAX_PHRASE_LENGTH,
            ),
            "updated_at": raw.get("updated_at") if isinstance(raw.get("updated_at"), str) else None,
            "updated_by": raw.get("updated_by") if isinstance(raw.get("updated_by"), str) else None,
        }
    except Exception as exc:  # noqa: BLE001 - 생성 자체를 규칙 파일 오류로 중단하지 않는다.
        logger.error("합성 추가 규칙을 읽지 못해 기본 규칙만 사용: path=%s err=%s", target, exc)
        return {"custom_rules": [], "custom_avoid_phrases": [], "updated_at": None, "updated_by": None}


def save_override(
    *,
    custom_rules: list[str],
    custom_avoid_phrases: list[str],
    updated_by: str,
    path: Path | None = None,
) -> dict[str, Any]:
    """추가 규칙을 원자적으로 저장한다.

    임시 파일을 같은 디렉터리에 만든 뒤 교체해서 API가 반쯤 쓴 JSON을 읽는 일을 막는다.
    """
    target = path or _path()
    rules = _clean_items(custom_rules, name="추가 규칙", maximum=_MAX_RULES, max_length=_MAX_RULE_LENGTH)
    phrases = _clean_items(
        custom_avoid_phrases, name="반복 금지 표현",
        maximum=_MAX_AVOID_PHRASES, max_length=_MAX_PHRASE_LENGTH,
    )
    payload = {
        "schema_version": 1,
        "custom_rules": rules,
        "custom_avoid_phrases": phrases,
        "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "updated_by": updated_by,
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, target)
    return payload


def effective_parts() -> tuple[tuple[str, ...], tuple[str, ...], dict[str, Any]]:
    """기본 안전 규칙과 관리자 추가 규칙을 합친 실제 생성 입력을 돌려준다."""
    # generation_playbook이 이 모듈의 load_override를 부르므로, 순환 import를 피하려고
    # 함수 안에서만 기본 상수를 읽는다.
    from koipa.modules.m1_synthesis.generation_playbook import (  # noqa: PLC0415
        AVOID_PHRASES,
        GENERATION_RULES,
    )

    override = load_override()
    return (
        tuple(GENERATION_RULES) + tuple(override["custom_rules"]),
        tuple(AVOID_PHRASES) + tuple(override["custom_avoid_phrases"]),
        override,
    )


def view() -> dict[str, Any]:
    """콘솔 팝업이 표시할 규칙과 변경 메타데이터."""
    from koipa.modules.m1_synthesis.generation_playbook import (  # noqa: PLC0415
        AVOID_PHRASES,
        GENERATION_RULES,
        playbook_text,
        playbook_version,
    )

    override = load_override()
    return {
        "version": playbook_version(),
        "rules": list(GENERATION_RULES) + list(override["custom_rules"]),
        "avoid_phrases": list(AVOID_PHRASES) + list(override["custom_avoid_phrases"]),
        "baseline_rules": list(GENERATION_RULES),
        "baseline_avoid_phrases": list(AVOID_PHRASES),
        "custom_rules": list(override["custom_rules"]),
        "custom_avoid_phrases": list(override["custom_avoid_phrases"]),
        "updated_at": override["updated_at"],
        "updated_by": override["updated_by"],
        "text": playbook_text(),
    }


def content_digest() -> str:
    """테스트·감사용: 현재 적용 내용 전체의 짧은 해시."""
    parts = effective_parts()
    raw = json.dumps({"rules": parts[0], "avoid": parts[1]}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]


__all__ = [
    "PlaybookValidationError",
    "content_digest",
    "effective_parts",
    "load_override",
    "save_override",
    "view",
]
