"""서명 화면에 기본 검수자 이름을 **미리 채우지 않는다**.

왜(실측 2026-08-17, KL 223).

    SIGNOFF_DEFAULT_REVIEWER=hong.gildong   (.env.jjw:26 · 컨테이너 동일)
    locked_gold_eval 20건                    전원 reviewer_id=hong.gildong
    그중 19건                                 같은 마이크로초 서명(2026-08-06T14:42:14.904855)

**19건이 같은 마이크로초에 서명됐다는 것은 사람이 한 것이 아니다.** 화면이 채워 준 이름을
그대로 제출한 결과다. 이 편의 기능은 "누가 검수했나" 를 지우는 방향으로만 작동했다.

⚠ 고치는 방향이 중요하다. **설정값을 비우면 안 된다.**

    golden_tiers.is_human_reviewer 는 그 설정값과 같은 이름을 거부한다(DEF-2026-53).
        default_rid = _configured_default_reviewer()
        if default_rid and rid == default_rid:   # 설정이 비면 앞 조건이 거짓 → 검사 자체가 사라진다
            return False

    즉 설정을 비우면 hong.gildong 이 다시 유효한 사람 검수자가 되어 위 20건이 되살아난다.
    게다가 이 판정은 읽기 시점마다 재계산되므로(locked_readiness), 파일을 안 건드려도
    env 를 바꾸는 순간 등급이 바뀐다.

그래서 **주입만 끊고 설정값은 남긴다.** 화면은 빈칸으로 뜨고 거부는 계속 작동한다.
"""
from __future__ import annotations

import inspect

from koipa.golden_tiers import is_human_reviewer
from koipa.services.golden_build_service import GoldenBuildService


def test_screen_is_not_prefilled_with_configured_reviewer():
    """화면 렌더 호출이 검수자 이름을 아예 넘기지 않는다.

    [C2 2026-08-17] 처음에는 `default_reviewer=""` 로 **빈 값을 넘기는지** 검사했다.
    이제 렌더러에서 인자 자체를 없앴으므로(신원은 로그인 쿠키 sub 에서만 온다) 더 강한
    성질로 바꾼다 — 호출부에 그 이름이 등장하면 안 된다. 인자가 없으면 되살리는 데
    한 줄이 아니라 시그니처 변경이 필요하다.
    """
    # 주석에 이름이 나오는 것은 허용한다(경위 설명). 막는 것은 **인자로 넘기는 것**이다.
    code = "".join(
        ln for ln in inspect.getsource(GoldenBuildService).splitlines(keepends=True)
        if not ln.lstrip().startswith("#")
    )
    assert "default_reviewer=" not in code, (
        "검수자 이름을 화면에 넘기고 있다 — 신원은 로그인 쿠키(JWT sub)에서만 와야 한다"
    )
    assert "default_api_key=" not in code, (
        "공유 API Key 를 화면에 넘기고 있다 — 페이지 본문에 관리자 키가 박혀 나간다"
    )
    assert "signoff_prefill_api_key" not in code, "프리필 설정을 다시 읽고 있다"


def test_config_field_is_kept_for_the_rejection_check():
    """설정값 자체는 지우면 안 된다 — 거부 판정의 입력이다."""
    from koipa.config import Settings

    assert "signoff_default_reviewer" in Settings.model_fields, (
        "설정을 지우면 golden_tiers 의 기본이름 거부가 무력화된다"
    )


def test_configured_default_name_is_still_rejected(monkeypatch):
    """설정된 이름은 여전히 사람 검수자가 아니다(DEF-2026-53 유지)."""
    from koipa.config import settings

    monkeypatch.setattr(settings, "signoff_default_reviewer", "hong.gildong", raising=False)
    assert is_human_reviewer("hong.gildong") is False
    assert is_human_reviewer("HONG.GILDONG") is False, "대소문자만 바꿔 우회되면 안 된다"
    assert is_human_reviewer("kim.cs") is True, "실계정은 통과해야 한다"


def test_emptying_the_setting_would_disable_the_check():
    """왜 설정을 비우면 안 되는지를 코드로 고정한다 — 이 성질이 사라지면 위 결정의 전제가 깨진다."""
    from koipa.config import settings

    old = getattr(settings, "signoff_default_reviewer", "")
    try:
        settings.signoff_default_reviewer = ""
        assert is_human_reviewer("hong.gildong") is True, (
            "설정이 비면 기본이름 거부가 사라진다 — 이 성질 때문에 '설정 비우기'로 고치면 안 된다"
        )
    finally:
        settings.signoff_default_reviewer = old
