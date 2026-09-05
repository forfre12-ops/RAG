# -*- coding: utf-8 -*-
"""합성 경로의 계약 결함 다섯 가지를 지키는 시험 (2026-09-05 지적 대응).

무엇을 지키나 — 전부 "고쳤다"가 아니라 **다시 나면 잡히는가**를 본다.

  ⑧ noop 산출물이 학습셋에 들어가지 않는다
     실측으로 확인한 구멍이었다: NoopProvider 는 파싱되는 JSON 을 주므로 label_source 가
     None 으로 남아, 마커 기반 게이트를 그대로 통과했다. provider 축으로 막는다.
  ④ API 가 받는 provider 목록이 팩토리와 같다
     종전 API 는 vllm_qwen·vllm_exaone 을 받았는데 build_provider 는 그 이름을 모른다.
  ③ seed_documents 는 없다 — 요건(RTM FUN-003)에 없고 구현도 없었다. 되살리면 발주처
     원문을 합성 프롬프트로 보내는 경로가 생긴다.
  ⑤ 발사 여부가 응답에 담긴다 — 종전에는 등록만 되고 생성이 안 돼도 202 였다.
  ② 요청한 provider 가 태스크까지 전달된다.
"""
from __future__ import annotations

import inspect
import re

import pytest

from koipa.adapters.llm import build_provider
from koipa.config import _VALID_LLM_PROVIDER
from koipa.schemas.synthesis import (
    LLM_PROVIDER_PATTERN,
    SynthGenerateRequest,
    SynthGenerateResponse,
)
from koipa.services.synthesis_service import _is_training_admissible


# ── ⑧ noop 더미의 학습 편입 차단 ─────────────────────────────────────
@pytest.mark.parametrize(
    ("label_source", "provider", "expected"),
    [
        (None, "noop", False),        # ← 구멍이었던 자리: 파싱 성공한 noop 산출물
        (None, "NOOP", False),        # 대소문자 무관
        (None, " noop ", False),      # 공백 무관
        (None, "anthropic", True),
        ("noop_fallback", "anthropic", False),
        ("llm_nonjson", "anthropic", False),
        (None, None, True),           # provider 미상은 종전 동작 유지
    ],
)
def test_training_admission_gate(label_source, provider, expected):
    assert _is_training_admissible(label_source, provider) is expected


def test_noop_generated_doc_is_refused(monkeypatch):
    """생성기를 실제로 돌려 확인한다 — 패턴이 아니라 동작을 본다."""
    monkeypatch.setenv("LLM_PROVIDER", "noop")
    from koipa.modules.m1_synthesis.generator import (  # noqa: PLC0415
        SynthRequest, SyntheticDocGenerator,
    )

    docs = SyntheticDocGenerator().generate(
        SynthRequest(target_grade="TS", domain="tech", count=1)
    )
    d = docs[0]
    assert d.llm_provider == "noop"
    assert not _is_training_admissible(d.label_source, d.llm_provider), (
        "noop 산출물이 학습 편입 가능으로 나왔다 — 게이트가 뚫렸다"
    )


# ── ④ provider 목록이 팩토리와 같다 ──────────────────────────────────
def test_api_provider_names_are_all_buildable():
    names = re.fullmatch(r"\^\((.+)\)\$", LLM_PROVIDER_PATTERN).group(1).split("|")
    assert set(names) == set(_VALID_LLM_PROVIDER), (
        "API 가 받는 이름과 설정 정본이 갈렸다"
    )
    # 팩토리가 모르는 이름이 하나도 없어야 한다(네트워크 없이 생성만 확인).
    for n in names:
        if n == "noop":
            assert build_provider(n) is not None
            continue
        try:
            build_provider(n)
        except ValueError as exc:  # 이름을 모른다 = 계약 불일치
            pytest.fail(f"팩토리가 모르는 이름이 API 에 열려 있다: {n} ({exc})")
        except Exception:  # noqa: BLE001  # 자격증명·endpoint 부재는 여기 관심 밖
            pass


# ── ③ seed_documents 는 없다 ─────────────────────────────────────────
def test_seed_documents_is_gone():
    assert "seed_documents" not in SynthGenerateRequest.model_fields, (
        "발주처 원문을 합성 프롬프트로 보내는 경로다 — 요건(FUN-003)에도 없다"
    )


# ── ⑤ 발사 여부가 응답에 있다 ────────────────────────────────────────
def test_response_reports_dispatch():
    for f in ("dispatched", "dispatch_note"):
        assert f in SynthGenerateResponse.model_fields, (
            "등록만 되고 생성이 안 된 것을 호출자가 알 수 있어야 한다"
        )


# ── ② provider 가 태스크까지 간다 ────────────────────────────────────
def test_task_accepts_llm_provider():
    from koipa.workers.tasks import synthesize_batch  # noqa: PLC0415

    fn = getattr(synthesize_batch, "run", synthesize_batch)
    assert "llm_provider" in inspect.signature(fn).parameters, (
        "요청자가 고른 모델과 실제로 쓴 모델이 갈린다"
    )


# ── ⑥ 승인본이 어느 학습셋 판에 들어갔는지 되짚을 수 있다 ────────────
def test_dataset_membership_is_append_only():
    """칼럼 하나가 아니라 **연결 표**여야 이력이 남는다.

    [2026-09-05] 앞선 판은 tb_sample_documents 의 칼럼을 UPDATE 로 덮어써서, 재방출하면
    한 문서가 여러 판에 들어간 기록을 잃었다. 같은 표에서 생성 작업 연결도 푼다.
    """
    from koipa.db.models import SampleDatasetMembership, SampleDocument  # noqa: PLC0415

    cols = {c.name for c in SampleDatasetMembership.__table__.columns}
    assert {"sample_id", "dataset_version", "synth_job_id"} <= cols
    # 덮어쓰던 칼럼은 되돌렸다 — 남겨 두면 표와 어느 쪽이 진실인지 갈린다.
    assert not hasattr(SampleDocument, "added_to_dataset_version")
    # 같은 판에 두 번 넣지 않는다.
    uq = {c.name for c in SampleDatasetMembership.__table__.constraints if c.name}
    assert "uq_sdm_sample_version" in uq


def test_dataset_version_is_content_hash(monkeypatch):
    """판 이름은 **내용 해시**다 — 같은 승인 집합이면 같은 값이어야 되짚기가 된다.

    시각으로 가르면 같은 내용이 매번 다른 판이 되어 추적이 무의미해진다.
    """
    import types  # noqa: PLC0415

    from koipa.services import synthesis_service as mod  # noqa: PLC0415

    def _s(sid, text, code="S2"):
        return types.SimpleNamespace(
            sample_id=sid, generated_content=text, label_source=None,
            corrected_level_id=None, target_level_id=1, doc_type="tech",
            llm_provider="anthropic", llm_model="m", body_prompt_version="v2-x",
            qc_prompt_version="metric-gate-v1", quality_score=1.0,
        )

    rows = [_s("a", "본문 하나"), _s("b", "본문 둘")]

    class _Repo:
        def __init__(self, _db): pass
        def count_by_status(self, _s): return len(rows)
        def list_by_status(self, _s, limit=0, offset=0): return rows if offset == 0 else []
        def mark_added_to_dataset(self, ids, version): return len(ids)

    class _Cls:
        def __init__(self, _db): pass
        def level_id_by_code(self, code): return 1 if code == "S2" else 2

    class _Db:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(mod, "SynthRepo", _Repo)
    monkeypatch.setattr(mod, "ClassifyRepo", _Cls)
    monkeypatch.setattr(mod, "session_scope", lambda: _Db())

    a = mod.SynthesisService().build_training_rows()
    b = mod.SynthesisService().build_training_rows()
    assert a["dataset_version"] == b["dataset_version"], "같은 내용인데 판 이름이 갈렸다"
    assert a["dataset_version"].startswith("synth-")
    # 생산 이력이 학습 행까지 나온다 — 로컬 대 상용 비교의 기준선.
    for f in ("llm_provider", "llm_model", "body_prompt_version", "quality_score"):
        assert f in a["rows"][0], f"생산 이력 {f} 가 학습 행에 없다"


# ── E-1 도메인 어휘 통일 ─────────────────────────────────────────────
def test_domain_aliases_fold_to_canonical():
    """같은 산업이 영문·한글 두 칸으로 갈려 커버리지 빈 칸이 부풀려져 있었다."""
    from koipa.modules.m1_synthesis.generator import (  # noqa: PLC0415
        DOMAIN_DOC_TYPES, canonical_domain,
    )

    for alias, canon in (("semiconductor", "반도체"), ("battery", "배터리"),
                         ("pharma", "화학_제약"), ("bio", "바이오_농업")):
        assert canonical_domain(alias) == canon
        assert canon in DOMAIN_DOC_TYPES, f"{canon} 문서 유형이 없으면 생성할 수 없다"
    assert canonical_domain("tech") == "tech"      # 별칭 아닌 것은 그대로
    assert canonical_domain(None) == "mixed"


# ── E-2 학습셋에 실재하는 한국 산업 도메인을 생성기가 안다 ───────────
def test_generator_knows_korean_industry_domains():
    """실측: 이 도메인들이 학습셋 944행(37.0%)인데 생성기가 몰라 채울 수 없었다."""
    from koipa.modules.m1_synthesis.generator import DOMAIN_DOC_TYPES  # noqa: PLC0415

    for d in ("배터리", "반도체", "화학_제약", "바이오_농업",
              "소프트웨어", "경영정보", "기타"):
        assert d in DOMAIN_DOC_TYPES, f"{d} 도메인을 생성기가 모른다"
        assert len(DOMAIN_DOC_TYPES[d]) > 10, f"{d} 문서 유형이 비었다"


def test_api_accepts_both_alias_and_canonical():
    from koipa.schemas.synthesis import SynthGenerateRequest  # noqa: PLC0415
    from koipa.schemas.common import Actor  # noqa: PLC0415

    for d in ("semiconductor", "반도체", "배터리", "경영정보"):
        SynthGenerateRequest(
            target_grade="TS", domain=d, count=1,
            actor=Actor(user_id="t", role="admin"),
        )


# ── 누출 게이트 fail-open (2026-09-05 monimo 세션 지적) ──────────────
#
# 워커의 게이트는 screen_batch 예외 시 전량을 검수큐로 넣고 batch_verdict='gate_error' 만
# 남긴다. 생성 결과를 버리지 않는 판단은 맞지만, 그 문서가 승인만 되면 **검사받지 않은
# 채로** 학습셋에 들어갔다. 검수큐에는 남기고 학습 편입만 막는다.

def test_gate_error_sample_is_refused_for_training():
    from koipa.services.synthesis_service import _is_training_admissible as ok  # noqa: PLC0415

    assert ok(None, "anthropic", "ok") is True
    assert ok(None, "anthropic", "too_small_for_corpus_metrics") is True
    assert ok(None, "anthropic", "gate_error") is False, (
        "게이트를 못 돌린 문서가 학습 편입 가능으로 나왔다"
    )
    # 옛 호출(인자 둘·하나)이 깨지지 않는다
    assert ok(None, "anthropic") is True
    assert ok(None) is True


def test_gate_error_is_counted_separately_from_noise(monkeypatch):
    """'게이트를 못 돌렸다'와 '게이트가 걸렀다'는 다른 사실이다 — 합치면 못 센다."""
    import types  # noqa: PLC0415

    from koipa.services import synthesis_service as mod  # noqa: PLC0415

    def _s(sid, *, verdict=None, label_source=None):
        return types.SimpleNamespace(
            sample_id=sid, generated_content="본문 " + sid, label_source=label_source,
            corrected_level_id=None, target_level_id=1, doc_type="tech",
            llm_provider="anthropic", llm_model="m", body_prompt_version="v2-x",
            qc_prompt_version="metric-gate-v1", quality_score=1.0,
            quality_report={"batch_verdict": verdict} if verdict else None,
        )

    rows = [
        _s("ok1", verdict="ok"),
        _s("broken", verdict="gate_error"),          # 검사받지 않음
        _s("noise", label_source="noop_fallback"),   # 자리표시 본문
    ]

    class _Repo:
        def __init__(self, _db): pass
        def count_by_status(self, _s): return len(rows)
        def list_by_status(self, _s, limit=0, offset=0): return rows if offset == 0 else []
        def record_dataset_membership(self, ids, v, **kw): return len(ids)

    class _Cls:
        def __init__(self, _db): pass
        def level_id_by_code(self, code): return 1 if code == "S2" else 2

    class _Db:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(mod, "SynthRepo", _Repo)
    monkeypatch.setattr(mod, "ClassifyRepo", _Cls)
    monkeypatch.setattr(mod, "session_scope", lambda: _Db())

    res = mod.SynthesisService().build_training_rows()
    assert res["included"] == 1, res
    assert res["excluded_gate_error"] == 1, "게이트 미실행 건이 따로 세어지지 않는다"
    assert res["excluded_noise"] == 1, "잡음과 섞였다"


def test_error_path_still_carries_dataset_version(monkeypatch):
    """DB 오류가 호출부에서 KeyError 로 둔갑하면 진짜 사유가 가려진다."""
    from sqlalchemy.exc import SQLAlchemyError  # noqa: PLC0415

    from koipa.services import synthesis_service as mod  # noqa: PLC0415

    def _boom():
        raise SQLAlchemyError("DB 없음")

    monkeypatch.setattr(mod, "session_scope", _boom)
    res = mod.SynthesisService().build_training_rows()
    for k in ("dataset_version", "excluded_gate_error", "included"):
        assert k in res, f"오류 경로에 {k} 가 없다 — 호출부가 KeyError 로 끝난다"


def test_dataset_version_covers_production_conditions():
    """같은 문서 집합이라도 다른 모델·프롬프트로 만든 것은 다른 판이어야 한다."""
    from koipa.services.synthesis_service import _dataset_version  # noqa: PLC0415

    base = {"doc_id": "a", "label": "S2", "text": "본문", "llm_provider": "anthropic",
            "llm_model": "claude-x", "body_prompt_version": "v2-aaa",
            "qc_prompt_version": "metric-gate-v1", "quality_score": 1.0}
    assert _dataset_version([base]) == _dataset_version([base])
    assert _dataset_version([base]) != _dataset_version([dict(base, llm_model="gpt-x")])
    assert _dataset_version([base]) != _dataset_version([dict(base, body_prompt_version="v2-b")])
    assert _dataset_version([base]) != _dataset_version([dict(base, quality_score=0.5)])


# ── ② 생성 작업 ↔ 문서 연결 (조회 경로) ─────────────────────────────
def test_job_status_endpoint_exists_and_answers():
    """응답이 synth_job_id 를 주는데 조회 경로가 없었다 —
    "이 작업이 성공했나 · 몇 건 만들었나 · 어느 문서인가"를 물을 수 없었다.
    """
    import uuid as _uuid  # noqa: PLC0415

    from fastapi.testclient import TestClient  # noqa: PLC0415

    from koipa.api.app import app  # noqa: PLC0415

    with TestClient(app) as cli:
        r = cli.get(
            f"/api/v1/synth/jobs/{_uuid.uuid4()}",
            headers={"X-API-Key": "test-key", "X-Actor-Role": "admin"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    # 없는 잡도 답한다 — 404 로 감추면 "만료됐다"와 "그런 잡이 없다"를 못 가린다.
    assert body["status"] == "unknown"
    for f in ("synth_job_id", "sample_ids", "persisted", "leakage_gate", "error"):
        assert f in body, f"{f} 가 응답에 없다"


def test_membership_answers_both_questions():
    """한 표로 둘을 푼다 — 이 문서가 들어간 판 · 이 작업이 만든 문서."""
    from koipa.repositories.synth_repo import SynthRepo  # noqa: PLC0415

    for m in ("record_dataset_membership", "dataset_versions_of", "samples_of_job"):
        assert hasattr(SynthRepo, m), f"{m} 가 없다"
