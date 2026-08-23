"""폐쇄망 번들이 **등급을 만드는 모델**을 실제로 싣는지 고정한다.

왜(실측 2026-08-15). `dist/` 에 있던 번들에 **6/2 모델**이 들어 있었다.

    번들 모델    정확도 0.783 · f1 0.783 · 미탐률 0.217
    배포본 v5    정확도 0.953 · f1 0.951 · 미탐률 0.047

미탐이 4.6배다. 지재원 서버가 이 번들로 설치되면 7/29 승격 전 모델이 뜬다. 그런데
**아무것도 막지 않았다** — 게이트 네 개가 연속으로 통과시켰다.

    1) dry-run(CI)         학습 분류기 관련 경고를 아예 안 냄(복사 함수가 dry-run 에선 안 돎)
    2) 실제 빌드            미지정이면 [WARN] 만 찍고 True 반환 → 빌드 성공
    3) check_model_parity  번들 dir 이 None 이면 "위반 아님" 반환 → 통과
    4) deploy_airgap.sh    부재 시 info "[주의]" 만 → 통과

그리고 parity 는 이번 경우 발동조차 안 했다. 번들에 모델이 *있긴* 했고, 검사가 보는 것은
경로 문자열에 릴리스 버전명이 있는지인데 `models/classifier-trained/` 에는 버전 표기가
없다. **내용이 다른 모델은 어떤 검사도 잡지 못했다.**

이 파일이 고정하는 것:
    1. 학습 분류기 미동봉 = parity 위반 (탈출구는 명시적 플래그로만)
    2. 번들에 남은 예전 분류기 = 위생 위반 (내용 해시로 대조)
    3. 출력 메시지가 cp949 로 나간다 (기록된 교훈 — em dash 하나가 배포 스크립트를 죽였다)
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "_bundle_builder", _ROOT / "scripts" / "build_offline_bundle.py"
)
bob = importlib.util.module_from_spec(_SPEC)
sys.modules["_bundle_builder"] = bob
_SPEC.loader.exec_module(bob)

RELEASE = "artifacts/classifier_p1_v5_clean/v-fe4b386b"


def test_missing_classifier_is_a_parity_violation():
    """미동봉을 통과시키면 폐쇄망이 rule-fallback 으로 뜬다 — 무음 미탐 위험."""
    v = bob.check_model_parity(None, RELEASE)
    assert v, "학습 분류기 미동봉이 위반으로 잡히지 않는다"
    assert "rule-fallback" in v


def test_wrong_version_is_a_parity_violation():
    v = bob.check_model_parity(Path("artifacts/classifier_p1_v5_clean/v-d2b4d2e1"), RELEASE)
    assert v and "v-fe4b386b" in v


def test_release_version_passes():
    assert bob.check_model_parity(Path(RELEASE), RELEASE) is None


def _manifest_with(src: Path):
    return bob.BundleManifest(
        bundle_name="t", version="t", build_date="t", git_commit="t",
        target_env="t", dry_run=True,
        models=[bob.ModelEntry(name="classifier-trained", dim=None, sha256=None,
                               license="internal-trained", role="classifier_trained",
                               source_path=str(src))],
    )


def test_stale_bundled_classifier_is_caught(tmp_path):
    """재빌드는 'already exists' 로 SKIP 한다 — 예전 모델이 그대로 실려나간다.

    이것이 실제로 일어난 일이다. 이미지·wheel 은 보면서 정작 모델은 안 봤다.
    """
    src = tmp_path / "release"; src.mkdir()
    (src / "model.safetensors").write_bytes(b"NEW-WEIGHTS")
    (src / "config.json").write_text("{}", encoding="utf-8")

    out = tmp_path / "bundle"
    bundled = out / "models" / "classifier-trained"; bundled.mkdir(parents=True)
    (bundled / "model.safetensors").write_bytes(b"OLD-WEIGHTS-FROM-JUNE")
    (bundled / "config.json").write_text("{}", encoding="utf-8")

    v = bob.check_bundle_hygiene(out, _manifest_with(src))
    assert any("stale classifier" in x for x in v), f"stale 분류기를 못 잡는다: {v}"


def test_same_content_passes(tmp_path):
    """같은 내용이면 통과해야 한다 — 재빌드마다 거짓 경보를 내면 아무도 안 본다."""
    src = tmp_path / "release"; src.mkdir()
    (src / "model.safetensors").write_bytes(b"SAME")
    out = tmp_path / "bundle"
    bundled = out / "models" / "classifier-trained"; bundled.mkdir(parents=True)
    (bundled / "model.safetensors").write_bytes(b"SAME")

    assert not [x for x in bob.check_bundle_hygiene(out, _manifest_with(src))
                if "stale classifier" in x]


@pytest.mark.parametrize("msg", [
    bob.check_model_parity(None, RELEASE),
    bob.check_model_parity(Path("artifacts/classifier_p1_v5_clean/v-d2b4d2e1"), RELEASE),
])
def test_messages_survive_cp949(msg):
    """한국어 콘솔은 cp949 다. em dash 하나가 배포 스크립트를 통째로 죽인 적이 있다.

    그 교훈이 기록에 있는데도 이 파일에 또 들어갔다(2026-08-15, 338행). 고정한다.
    """
    assert msg
    msg.encode("cp949")
