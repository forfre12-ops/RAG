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
def test_sample_row_has_dataset_version_column():
    """응답 스키마에만 있고 표에 칸이 없어 늘 None 이던 것을 실제 칼럼으로 뒀다."""
    from koipa.db.models import SampleDocument  # noqa: PLC0415

    assert hasattr(SampleDocument, "added_to_dataset_version")


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
