# -*- coding: utf-8 -*-
"""검수자 아이디+비밀번호 — 파일 기반 저장소(DB 테이블 없음, 골든 검수 쪼각과 같은 패턴).

왜 DB가 아닌가: 이 저장소 전체(후보·배정 원장·결정 원장)가 파일 기반이다(DB 마이그레이션이
필요 없는 소규모 관리 데이터라는 같은 판단). 비밀번호는 bcrypt 로 해시해서만 저장한다 — 원문은
어디에도 남지 않는다.

이 파일은 "username → {password_hash, roles, created_at}" 매핑 JSON 하나다. 관리자(스크립트)만
쓰고, 로그인 API 는 읽기만 한다.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import bcrypt

_POC_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PATH = _POC_ROOT / "datasets" / "proxy_gold" / "reviewer_credentials.json"


class ReviewerCredentialError(Exception):
    pass


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise ReviewerCredentialError(f"credential store unreadable: {path}: {e}") from e


def _save(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(path)  # 원자적 교체 — 쓰다 죽어도 절반짜리 파일이 남지 않는다


def _resolve(path: Path | str | None) -> Path:
    """path=None 이면 **호출 시점의** DEFAULT_PATH 를 쓴다.

    [2026-10-02 결함] 전에는 `path: Path = DEFAULT_PATH` 처럼 기본값으로 직접 묶어 뒀다 —
    파이썬 기본값은 함수 "정의 시점"에 한 번만 평가되므로, 시험에서
    `monkeypatch.setattr(reviewer_credentials, "DEFAULT_PATH", tmp_path/...)` 로 바꿔도
    이미 정의된 함수들의 기본값은 그대로 **원래 경로**를 가리켰다. 그래서 격리됐어야 할 시험이
    실제 운영 파일(datasets/proxy_gold/reviewer_credentials.json)에 쓰고 지웠다 — 그 시험의
    remove_account 호출이 실제 reviewer-01 계정을 지웠다(발급해서 전달까지 한 계정).
    이제 모듈 전역 `DEFAULT_PATH` 를 매번 다시 읽는다.
    """
    return Path(path) if path is not None else DEFAULT_PATH


def set_password(username: str, password: str, *, roles: tuple[str, ...] = ("reviewer",),
                  path: Path | str | None = None) -> None:
    """비밀번호를 (재)설정한다. 계정이 없으면 새로 만든다."""
    username = username.strip()
    if not username:
        raise ReviewerCredentialError("username must not be empty")
    if len(password) < 8:
        raise ReviewerCredentialError("password must be at least 8 characters")
    resolved = _resolve(path)
    data = _load(resolved)
    data[username] = {
        "password_hash": bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii"),
        "roles": list(roles),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _save(resolved, data)


def create_accounts(
    accounts: Mapping[str, str], *, roles: tuple[str, ...] = ("reviewer",),
    path: Path | str | None = None,
) -> list[str]:
    """Create a set of new accounts without changing any existing account.

    Bulk reviewer issuance must never silently reset an existing password.  All
    names and passwords are validated before the one atomic credential-store
    replacement, so a duplicate or invalid password leaves every account as it
    was.  Plaintext passwords are accepted only from the caller and are not
    stored or returned.
    """
    normalized: dict[str, str] = {}
    for raw_username, password in accounts.items():
        username = str(raw_username).strip()
        if not username:
            raise ReviewerCredentialError("username must not be empty")
        if username in normalized:
            raise ReviewerCredentialError(f"duplicate username: {username}")
        if len(password) < 8:
            raise ReviewerCredentialError("password must be at least 8 characters")
        normalized[username] = password
    if not normalized:
        raise ReviewerCredentialError("at least one account is required")

    resolved = _resolve(path)
    data = _load(resolved)
    existing = sorted(set(normalized) & set(data))
    if existing:
        raise ReviewerCredentialError("account already exists: " + ", ".join(existing))

    now = datetime.now(timezone.utc).isoformat()
    for username, password in normalized.items():
        data[username] = {
            "password_hash": bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii"),
            "roles": list(roles),
            "created_at": now,
        }
    _save(resolved, data)
    return sorted(normalized)


def remove_account(username: str, *, path: Path | str | None = None) -> bool:
    resolved = _resolve(path)
    data = _load(resolved)
    if username not in data:
        return False
    del data[username]
    _save(resolved, data)
    return True


def verify_password(username: str, password: str, *, path: Path | str | None = None) -> tuple[str, ...] | None:
    """맞으면 역할 튜플, 틀리거나 계정이 없으면 None. 예외를 던지지 않는다(로그인 실패는 그냥 실패)."""
    try:
        data = _load(_resolve(path))
    except ReviewerCredentialError:
        return None
    record = data.get(username.strip())
    if not record:
        return None
    stored = str(record.get("password_hash", ""))
    try:
        ok = bcrypt.checkpw(password.encode("utf-8"), stored.encode("ascii"))
    except (ValueError, UnicodeEncodeError):
        return None
    if not ok:
        return None
    return tuple(record.get("roles") or ("reviewer",))


def list_usernames(*, path: Path | str | None = None) -> list[str]:
    return sorted(_load(_resolve(path)).keys())


def list_accounts(*, path: Path | str | None = None) -> list[dict[str, object]]:
    """관리자가 배정에 사용할 수 있는 **비밀 없는** 계정 명부를 돌려준다.

    비밀번호 해시나 생성 시각은 관리 화면에도 줄 이유가 없다. 다만 계정이 실제로
    로그인 가능한 검수자 역할인지 확인하지 않고 임의 문자열을 배정하면, 20×50 배정이
    원장에는 남아도 사람이 열 수 없는 문서 묶음이 된다. 기존 파일에는 ``active``가
    없으므로 하위호환상 활성으로 해석한다.
    """
    data = _load(_resolve(path))
    out: list[dict[str, object]] = []
    for raw_username, raw_record in data.items():
        username = str(raw_username).strip()
        record = raw_record if isinstance(raw_record, dict) else {}
        if not username:
            continue
        roles = tuple(
            str(role).strip() for role in (record.get("roles") or ("reviewer",))
            if str(role).strip()
        )
        out.append({
            "username": username,
            "roles": roles,
            "active": bool(record.get("active", True)),
        })
    return sorted(out, key=lambda item: str(item["username"]))
