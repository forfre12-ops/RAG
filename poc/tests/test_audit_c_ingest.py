"""Track C 감사 회귀 테스트 — 수집·PII·헬스 버그 3종.

대상 버그:
  [H1] 경로 탈출 — 사용자 filename(../..\\evil) 이 정제 없이 스토리지 키로 합성돼
       .storage 루트 밖에 쓰일 수 있었다(폐쇄망 LocalStorage 폴백 기본).
  [H3] PII 마스킹 우회 — 주민/외국인/사업자 번호 정규식이 하이픈 필수라
       무하이픈 번호(8001011234567)가 마스킹을 빠져나갔다.
  [H5] healthz_ready() 가 (dict, status_code) 튜플을 반환해 FastAPI가 배열로
       직렬화하고 상태가 항상 200 — 비정상에도 503 을 못 내렸다.

인프라(Postgres/ES/MinIO) 불필요: 서비스를 LocalStorage 로 직접 구동하고,
헬스 핸들러는 개별 probe 를 mock 한 뒤 직접 호출한다.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from lloydk.adapters.storage import LocalStorage
from lloydk.modules.m2_preprocess.pii_masker import mask_pii
from lloydk.services.document_ingestion_service import DocumentIngestionService


def _storage(tmp_path) -> LocalStorage:
    return LocalStorage(root=str(tmp_path / "store"))


# ===========================================================================
# [H1] 경로 탈출 (path traversal)
# ===========================================================================
class TestPathTraversal:
    def test_localstorage_rejects_traversal_key(self, tmp_path):
        """LocalStorage._path 는 루트 밖으로 해소되는 key 를 거부(fail-closed)."""
        st = _storage(tmp_path)
        with pytest.raises(ValueError, match="traversal"):
            st.put("bucket", "../../escape.txt", b"x")
        with pytest.raises(ValueError, match="traversal"):
            st.put("bucket", "..\\..\\escape.txt", b"x")

    def test_localstorage_allows_normal_key(self, tmp_path):
        """정상 key 는 그대로 동작 — 기존 흐름 회귀 없음."""
        st = _storage(tmp_path)
        uri = st.put("documents-raw", "t1/abc/file.txt", b"hello")
        assert uri.startswith("file://")
        assert st.get("documents-raw", "t1/abc/file.txt") == b"hello"

    @pytest.mark.parametrize(
        "evil",
        [
            "..\\..\\evil.txt",       # 윈도우 구분자
            "../../../etc/passwd",    # 유닉스 구분자
            "/abs/leak.txt",          # 절대경로 시도
            "C:\\windows\\sys.txt",   # 윈도우 절대경로
        ],
    )
    def test_ingest_malicious_filename_stays_in_root(self, tmp_path, evil):
        """악성 filename 이 와도 원본은 루트 하위에만 기록된다."""
        st = _storage(tmp_path)
        svc = DocumentIngestionService(storage=st)
        res = svc.ingest(
            filename=evil, content_bytes=b"secret-bytes", persist=False
        )
        root = (tmp_path / "store").resolve()
        # 1) raw_uri 의 실제 경로가 루트 하위
        path_part = res.raw_text_uri.replace("file://", "")
        written = Path(path_part).resolve()
        written.relative_to(root)  # ValueError 면 테스트 실패
        # 2) 루트 밖 어디에도 유출 파일이 없음
        for leaked_name in ("evil.txt", "passwd", "leak.txt", "sys.txt"):
            assert not list(root.parent.rglob(leaked_name)) or all(
                str(root) in str(p) for p in root.parent.rglob(leaked_name)
            )
        # 3) 원본은 정상 보관(감사 요건) — 디렉터리 성분만 제거된 basename 으로.
        assert st.get(svc.RAW_BUCKET, _key_of(res)) == b"secret-bytes"

    def test_dotdot_only_filename_falls_back_to_hash(self, tmp_path):
        """'.' / '..' / 빈 이름은 file_hash 로 대체(키 충돌·탈출 방지)."""
        st = _storage(tmp_path)
        svc = DocumentIngestionService(storage=st)
        res = svc.ingest(
            filename="..", content_bytes=b"d", persist=False
        )
        # 키 마지막 성분이 file_hash 여야 함
        assert res.raw_text_uri.rstrip("/").endswith(res.file_hash)

    def test_original_filename_preserved_for_audit(self, tmp_path):
        """스토리지 키는 정제하되, 결과/감사용 filename 은 원본 그대로 유지."""
        st = _storage(tmp_path)
        svc = DocumentIngestionService(storage=st)
        res = svc.ingest(
            filename="..\\..\\evil.txt",
            content_bytes=b"x",
            persist=False,
        )
        assert res.filename == "..\\..\\evil.txt"


def _key_of(res) -> str:
    """raw_text_uri 에서 (bucket 이후) 스토리지 key 복원."""
    p = res.raw_text_uri.replace("file://", "")
    # .../store/documents-raw/<hash>/<name>
    parts = p.split("/documents-raw/")
    return parts[1]


# ===========================================================================
# [H3] PII 마스킹 — 무하이픈 우회 차단
# ===========================================================================
class TestPiiNoHyphenBypass:
    def test_rrn_no_hyphen_masked(self):
        """무하이픈 주민번호(8001011234567)도 마스킹 — 7번째 자리 [1-4] 제약 유지."""
        r = mask_pii("주민 8001011234567 등록")
        assert "[RRN]" in r.text
        assert "8001011234567" not in r.text
        assert r.counts.get("rrn") == 1

    def test_rrn_hyphen_still_masked(self):
        """기존 하이픈 형식 회귀 없음."""
        r = mask_pii("주민번호: 901231-1234567")
        assert "[RRN]" in r.text
        assert r.counts.get("rrn") == 1

    def test_frn_no_hyphen_masked(self):
        """무하이픈 외국인등록번호(7번째 [5-8])도 마스킹."""
        r = mask_pii("외국인 8001015234567 등록")
        assert "[FRN]" in r.text
        assert r.counts.get("frn") == 1

    def test_rrn_seventh_digit_constraint_suppresses_false_positive(self):
        """7번째 자리가 5~9면 주민번호 아님 — 임의 13자리 영수증/일련번호 오탐 억제."""
        # 7번째 자리 = 9 → RRN/FRN 모두 미해당
        r = mask_pii("일련번호 1234569876543 끝")
        assert "[RRN]" not in r.text
        assert "[FRN]" not in r.text

    def test_rrn_not_partial_match_in_longer_run(self):
        """더 긴 숫자열 내부 부분매치 방지(lookaround)."""
        # 14자리 — 앞뒤 숫자 lookaround 로 13자리 부분매치 금지
        r = mask_pii("값 80010112345678 입니다")
        assert "[RRN]" not in r.text
        assert "80010112345678" in r.text

    def test_business_no_hyphen_masked(self):
        """하이픈 사업자번호는 형식 신뢰 — 그대로 마스킹(체크섬 무관)."""
        r = mask_pii("사업자등록번호: 123-45-67890")
        assert "[BIZNO]" in r.text
        assert r.counts.get("business_no") == 1

    def test_business_no_no_hyphen_valid_checksum_masked(self):
        """무하이픈 사업자번호는 가중합 체크섬 통과 시 마스킹(2208162517=유효)."""
        r = mask_pii("사업자 2208162517 입니다")
        assert "[BIZNO]" in r.text
        assert r.counts.get("business_no") == 1

    def test_business_no_no_hyphen_invalid_checksum_not_masked(self):
        """체크섬 불일치 10자리는 마스킹 안 함 — 일반 일련번호 오탐 억제."""
        r = mask_pii("일련번호 1078701974 입니다")  # 체크섬 불일치
        assert "[BIZNO]" not in r.text
        assert "1078701974" in r.text

    def test_business_no_space_separator_masked(self):
        """공백 구분자도 처리([- ]?)."""
        r = mask_pii("사업자 123 45 67890 입니다")
        assert "[BIZNO]" in r.text


# ===========================================================================
# [H5] healthz_ready — 비정상 시 실제 503
# ===========================================================================
class TestHealthReady503:
    def _patch_checks(self, model_ok, db_ok=True, es_ok=True, st_ok=True):
        import lloydk.api.health as h

        return [
            mock.patch.object(h, "_check_model", return_value={"status": "m", "ok": model_ok}),
            mock.patch.object(h, "_check_db", return_value={"status": "d", "ok": db_ok}),
            mock.patch.object(h, "_check_es", return_value={"status": "e", "ok": es_ok}),
            mock.patch.object(h, "_check_storage", return_value={"status": "s", "ok": st_ok}),
        ]

    def test_returns_503_when_unhealthy(self):
        import json

        import lloydk.api.health as h
        from fastapi.responses import JSONResponse

        patches = self._patch_checks(model_ok=False)
        with patches[0], patches[1], patches[2], patches[3]:
            h.STARTUP_COMPLETE = True
            resp = h.healthz_ready()
        assert isinstance(resp, JSONResponse)
        assert resp.status_code == 503
        body = json.loads(resp.body.decode())
        assert body["status"] == "not_ready"
        assert body["checks"]["model"]["ok"] is False

    def test_returns_200_when_healthy(self):
        import lloydk.api.health as h
        from fastapi.responses import JSONResponse

        patches = self._patch_checks(model_ok=True)
        with patches[0], patches[1], patches[2], patches[3]:
            h.STARTUP_COMPLETE = True
            resp = h.healthz_ready()
        assert isinstance(resp, JSONResponse)
        assert resp.status_code == 200

    def test_503_when_warmup_pending(self):
        """모든 probe ok 라도 warmup 미완이면 not_ready(503)."""
        import lloydk.api.health as h

        patches = self._patch_checks(model_ok=True)
        with patches[0], patches[1], patches[2], patches[3]:
            h.STARTUP_COMPLETE = False
            resp = h.healthz_ready()
        assert resp.status_code == 503
