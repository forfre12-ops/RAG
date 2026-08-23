#!/usr/bin/env python
"""CI 안전 게이트 검증 — 하드닝 프로파일이 '게이트ON=가시성ON'을 실제로 강제하는지.

배경(P0#5): assert_production_credentials / evaluate_safety_gates 는 배선됐으나 poc-ci.yml
이 이를 한 번도 검증하지 않아, 하드닝 스프린트(require_safety_gates 강제)가 CI로 영속화되지
않고 회귀 무방비였다. 본 스크립트를 CI job 이 실행해 두 방향을 잠근다:

  (양성) require_safety_gates=True 프로파일(onprem-local·full-train)은 기본값에서 안전 게이트가
         하나도 꺼져 있지 않아야 한다 (off=[] · must_raise=False). 프로파일이 게이트를 빠뜨리면 fail.
  (음성) 그 프로파일에서 게이트 하나를 .env override(=0)로 끄면 강제로직이 반드시
         must_raise=True 를 내야 한다. 강제가 무력화되면(조용히 통과) fail.

exit 0 = 강제 정상, exit 1 = 회귀(하드닝 프로파일 gate 누락 또는 강제 무력화).

순수 config 로직만 사용 — DB/redis/creds 커플링 없음(evaluate_safety_gates 는 순수 함수).
"""

from __future__ import annotations

import os
import sys

# Windows 콘솔(cp949)에서도 한국어·em-dash 출력이 깨지지 않도록 UTF-8 강제(CI ubuntu는 no-op).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

# 프로세스 전역 env 오염 방지: 음성 테스트 전에 clean 상태에서 양성부터 평가.
from koipa.config import (  # noqa: E402
    _PROFILE_DEFAULTS,
    _SAFETY_GATES,
    Settings,
    apply_profile_defaults,
    evaluate_safety_gates,
)


def _settings_for(profile: str) -> Settings:
    # _env_file=None: 운영자 .env 를 무시하고 '프로파일 기본값 + 명시적 os.environ override'만 평가.
    # CI 체크아웃엔 .env 가 없으므로 실 동작 불변이고, 검증이 stray .env 에 의존하지 않아 hermetic.
    s = Settings(deploy_profile=profile, _env_file=None)
    apply_profile_defaults(s)
    return s


def main() -> int:
    failures: list[str] = []

    # [hermetic] 강제집합 env 는 아래 (음성) 루프가 명시적으로 토글하는 대상이다. 호출자(pytest
    # conftest 가 REQUIRE_REAL_EMBEDDER=false 주입, 또는 개발 셸이 이 값들을 켜/꺼 둠)가 미리
    # 설정해 두면 (양성) 프로파일 '순수 기본값' 검증이 오염된다 — 시작 시 걷어내 프로파일 기본값만 본다.
    for _, _env in _SAFETY_GATES:
        os.environ.pop(_env, None)

    hardened = [
        name for name, d in _PROFILE_DEFAULTS.items() if d.get("require_safety_gates") is True
    ]
    if not hardened:
        print("FAIL: require_safety_gates=True 하드닝 프로파일이 하나도 없음", file=sys.stderr)
        return 1
    print(f"하드닝 프로파일: {hardened}")
    print(f"안전 게이트 강제집합: {[env for _, env in _SAFETY_GATES]}")

    # (양성) 각 하드닝 프로파일 기본값 — 게이트가 하나도 꺼져 있으면 안 된다.
    for profile in hardened:
        s = _settings_for(profile)
        res = evaluate_safety_gates(s)
        if not res["required"]:
            failures.append(f"[{profile}] require_safety_gates 가 True 로 해석되지 않음: {res}")
        if res["off"]:
            failures.append(f"[{profile}] 하드닝 프로파일인데 안전 게이트 OFF: {res['off']}")
        if res["must_raise"]:
            failures.append(f"[{profile}] 기본 프로파일이 startup 을 차단함(게이트 누락): {res}")
        if not failures:
            print(f"  OK (양성) {profile}: off=[] must_raise=False")

    # (음성) 하드닝 프로파일에서 게이트 하나를 env override 로 끄면 강제가 반드시 걸려야 한다.
    # 각 안전 게이트에 대해 개별 검증 — 하나라도 강제가 무력화되면 fail.
    neg_profile = hardened[0]
    for attr, env_name in _SAFETY_GATES:
        prev = os.environ.get(env_name)
        os.environ[env_name] = "0"
        try:
            s = _settings_for(neg_profile)
            res = evaluate_safety_gates(s)
            if getattr(s, attr) is not False:
                failures.append(f"[{neg_profile}] {env_name}=0 override 가 반영되지 않음(attr={attr})")
            elif not res["must_raise"]:
                failures.append(
                    f"[{neg_profile}] {env_name}=0 인데 강제가 걸리지 않음(must_raise=False) — 무음 OFF 회귀"
                )
            else:
                print(f"  OK (음성) {neg_profile} {env_name}=0 → must_raise=True (off={res['off']})")
        finally:
            if prev is None:
                os.environ.pop(env_name, None)
            else:
                os.environ[env_name] = prev

    if failures:
        print("\n=== 안전 게이트 강제 검증 FAIL ===", file=sys.stderr)
        for f in failures:
            print("  - " + f, file=sys.stderr)
        return 1
    print("\n안전 게이트 강제 검증 PASS — 하드닝 프로파일 게이트ON + 강제로직 정상.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
