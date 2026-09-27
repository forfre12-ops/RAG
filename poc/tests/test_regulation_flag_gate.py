"""규정 참고 표시는 **기본 꺼짐·무동작**이다(설계서 P4) — 켜지 않으면 라우트가 없다.

왜 새 프로세스로 재나. 라우터 등록은 앱을 불러오는 순간(import time)에 한 번 정해진다. 같은 프로세스에서 설정 모듈을 다시
불러 확인하는 방식(test_deploy_profile.py)은 다른 시험에 부작용을 남길 수 있어, 여기서는 **환경변수만 다르게 준 새 프로세스**가
앱을 불러 라우트 표를 찍게 한다. 저장소의 `.env` 를 읽지 않도록 빈 작업 폴더에서 돌린다.

이 시험이 지키는 것: (1) 아무것도 안 주면 규정 API 가 없다 (2) 명시적으로 끄면 없다 (3) 켜면 계약(ICD)에 적힌 경로가 전부 생긴다
(4) 어떤 배포 프로파일도 기본값으로 이 기능을 켜지 않는다.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

POC = Path(__file__).resolve().parents[1]

_PROBE = (
    "import json\n"
    "from koipa.api.app import app\n"
    "print('PATHS=' + json.dumps(sorted(p for p in app.openapi()['paths'] if 'regulation' in p)))\n"
)

EXPECTED_WHEN_ON = {
    "/api/v1/regulations",
    "/api/v1/regulations/{reg_id}",
    "/api/v1/regulations/{reg_id}/clauses",
    "/api/v1/regulations/{reg_id}/clauses/{clause_id}",
    "/api/v1/regulations/{reg_id}/preview",
    "/api/v1/regulations/{reg_id}/activate",
    "/api/v1/regulations/{reg_id}/archive",
    "/api/v1/documents/{doc_id}/regulation-evidence",
    "/api/v1/regulations/runtime-toggle",
    "/api/v1/regulations/llm-select-toggle",
}


def _regulation_paths(tmp_path: Path, **env_over: str) -> set[str]:
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("REGULATION_", "DEPLOY_PROFILE"))}
    env.update({"PYTHONPATH": str(POC / "src"), "SLOWAPI_SKIP_DOTENV": "1", "TESTING": "1",
                "PYTHONIOENCODING": "utf-8", **env_over})
    r = subprocess.run([sys.executable, "-c", _PROBE], cwd=tmp_path, env=env, capture_output=True, text=True,
                       encoding="utf-8", timeout=240)
    assert r.returncode == 0, f"앱을 불러오지 못했다:\n{r.stderr[-1500:]}"
    line = next((ln for ln in r.stdout.splitlines() if ln.startswith("PATHS=")), None)
    assert line is not None, f"라우트 표를 못 읽었다:\n{r.stdout[-800:]}\n{r.stderr[-800:]}"
    return set(json.loads(line[len("PATHS="):]))


def test_nothing_is_exposed_when_the_flag_is_not_given(tmp_path):
    assert _regulation_paths(tmp_path) == set()


def test_nothing_is_exposed_when_the_flag_is_explicitly_off(tmp_path):
    assert _regulation_paths(tmp_path, REGULATION_REFERENCE_ENABLED="false") == set()


def test_every_contract_path_appears_when_the_flag_is_on(tmp_path):
    assert _regulation_paths(tmp_path, REGULATION_REFERENCE_ENABLED="true") == EXPECTED_WHEN_ON


def test_no_deploy_profile_turns_the_feature_on_by_default():
    """프로파일 기본값 표(_PROFILE_DEFAULTS)가 이 플래그를 켜면 — 켠 적 없는 배포본에 화면이 생긴다."""
    from koipa.config import _PROFILE_DEFAULTS, Settings

    assert Settings.model_fields["regulation_reference_enabled"].default is False
    turned_on = [p for p, d in _PROFILE_DEFAULTS.items() if d.get("regulation_reference_enabled")]
    assert not turned_on, f"기본으로 규정 참고 표시를 켜는 프로파일: {turned_on}"


def test_no_deploy_profile_turns_the_llm_option_on_by_default():
    """로컬 LLM 으로 해당 항을 고르는 옵션은 GPU·LLM 서버가 있어야 하고 검수 화면 응답이 몇 초 걸린다 — 프로파일이 몰래 켜면 안 된다."""
    from koipa.config import _PROFILE_DEFAULTS, Settings

    assert Settings.model_fields["regulation_llm_select_enabled"].default is False
    turned_on = [p for p, d in _PROFILE_DEFAULTS.items() if d.get("regulation_llm_select_enabled")]
    assert not turned_on, f"기본으로 규정 LLM 판정을 켜는 프로파일: {turned_on}"


@pytest.mark.parametrize("name", ["regulation_evidence_max_items", "regulation_min_similarity",
                                  "regulation_max_sentences", "regulation_active_max"])
def test_tuning_knobs_reject_nonsense_values(name):
    """조정값이 엉뚱한 값으로 조용히 들어가면 표시 개수·상한이 어긋난다 — 기동 때 거절한다."""
    from pydantic import ValidationError

    from koipa.config import Settings

    bad = {"regulation_evidence_max_items": 4, "regulation_min_similarity": 1.5,
           "regulation_max_sentences": 0, "regulation_active_max": 0}[name]
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{name: bad})


def test_the_original_regulation_file_is_stored_in_an_encrypted_bucket_by_default():
    """규정 자체가 회원사의 기밀이다 — 원본 보관 버킷이 암호화 대상 목록에서 빠지면 평문으로 저장된다."""
    from koipa.config import Settings
    from koipa.services.regulation_service import RAW_BUCKET

    assert RAW_BUCKET in Settings.model_fields["storage_encrypted_buckets"].default


def test_turning_the_feature_on_adds_the_raw_bucket_even_when_the_operator_listed_buckets_by_hand(caplog):
    """`.env` 에서 암호화 버킷을 직접 지정하면 기본값이 통째로 대체된다 — 켠 배포에서 규정 원본이 평문이 되면 안 된다."""
    from koipa.config import Settings
    from koipa.services.regulation_service import RAW_BUCKET

    with caplog.at_level("WARNING", logger="koipa.config"):
        s = Settings(_env_file=None, regulation_reference_enabled=True, storage_encrypted_buckets=["documents-raw"])
    assert s.storage_encrypted_buckets == ["documents-raw", RAW_BUCKET]
    assert "regulations-raw" in caplog.text

    # 이미 들어 있으면 그대로(중복·경고 없음), 꺼져 있으면 손대지 않는다
    caplog.clear()
    with caplog.at_level("WARNING", logger="koipa.config"):
        ok = Settings(_env_file=None, regulation_reference_enabled=True, storage_encrypted_buckets=["documents-raw", RAW_BUCKET])
        off = Settings(_env_file=None, regulation_reference_enabled=False, storage_encrypted_buckets=["documents-raw"])
    assert ok.storage_encrypted_buckets == ["documents-raw", RAW_BUCKET] and off.storage_encrypted_buckets == ["documents-raw"]
    assert "regulations-raw" not in caplog.text


def test_a_regulation_original_is_unreadable_on_disk_and_comes_back_intact(tmp_path):
    """기본 설정의 암호화 버킷 목록으로 실제 파일 저장소를 감싸면, 디스크의 규정 원본에 평문이 없고 읽으면 원본이 돌아온다."""
    from koipa.adapters.storage.encrypted_store import EncryptingStorage
    from koipa.adapters.storage.local_store import LocalStorage
    from koipa.config import Settings
    from koipa.services.regulation_service import RAW_BUCKET, _raw_key

    secret = "제12조(극비 문서의 취급) 극비 문서는 사전 승인 없이 사외로 반출할 수 없다.".encode("utf-8")
    storage = EncryptingStorage(LocalStorage(str(tmp_path)), key_material="regulation-at-rest-test-key-0123456789abcdef",
                                buckets=set(Settings.model_fields["storage_encrypted_buckets"].default))
    key = _raw_key("a" * 64, "규정.md")
    storage.put(RAW_BUCKET, key, secret)

    on_disk = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert len(on_disk) == 1 and RAW_BUCKET in on_disk[0].parts
    assert secret not in on_disk[0].read_bytes() and "극비".encode("utf-8") not in on_disk[0].read_bytes()
    assert storage.get(RAW_BUCKET, key) == secret
