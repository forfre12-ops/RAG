"""배포 템플릿과 setup.sh 가 만드는 .env 가 블라인드 검수를 기본으로 켠다.

`GOLDEN_REVIEW_BLIND_ENFORCED` 의 코드 기본값은 False 다. 템플릿에 이 줄이 없으면 오류 없이 기동하고 검수자
화면에 제안 등급·근거가 그대로 보여 독립 판정이 깨진다(재학습 라벨이 AI 라벨을 따라가 '정정'이 아니라 '확인
도장'이 된다). setup.sh 는 이미 1 로 쓰는데 템플릿 두 개에는 없어, 템플릿을 직접 복사해 배포하면 노출됐다
(2026-09-25).
"""
from __future__ import annotations

from pathlib import Path

import pytest

POC = Path(__file__).resolve().parents[1]


def _env_value(text: str, key: str) -> str | None:
    for line in text.replace("\r\n", "\n").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    return None


@pytest.mark.parametrize("template", [".env.onprem-local", ".env.full-train"])
def test_the_deployment_template_turns_blind_review_on(template):
    text = (POC / template).read_text(encoding="utf-8")
    assert _env_value(text, "GOLDEN_REVIEW_BLIND_ENFORCED") == "1", f"{template} 에 GOLDEN_REVIEW_BLIND_ENFORCED=1 이 없다"


def test_the_env_that_setup_sh_generates_turns_blind_review_on():
    text = (POC / "scripts" / "setup.sh").read_text(encoding="utf-8")
    assert "\nGOLDEN_REVIEW_BLIND_ENFORCED=1\n" in text


def test_blind_review_is_a_real_setting_and_defaults_off_in_code():
    """이 시험이 지키는 전제 — 코드 기본값이 꺼짐이라 템플릿이 켜 줘야 한다."""
    from koipa.config import Settings

    assert "golden_review_blind_enforced" in Settings.model_fields
    assert Settings.model_fields["golden_review_blind_enforced"].default is False
