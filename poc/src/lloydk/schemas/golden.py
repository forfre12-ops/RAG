"""Golden builder 도메인 스키마 (/golden/*) — G3b."""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from .common import Actor


class GoldenBuildRequest(BaseModel):
    source_type: str = Field(default="inline", pattern=r"^(inline|corpus)$")
    docs: list[dict] = Field(default_factory=list)          # source_type=inline
    corpus_dir: Optional[str] = None                         # source_type=corpus (jsonl 파일 또는 *.json 디렉토리)
    n: int = Field(default=200, ge=1, le=5000)
    llm_provider: str = Field(
        default="noop",
        pattern=r"^(anthropic|openai|google|gemini|vllm_qwen|vllm_exaone|local_openai|noop)$",
    )
    sensitive: bool = Field(default=False)  # True=실고객 비밀 → airgap(Qwen), 공개 클라우드 금지
    min_rule_conf: float = Field(default=0.5, ge=0.0, le=1.0)        # 레거시(게이트 미사용)
    min_llm_conf: float = Field(default=0.7, ge=0.0, le=1.0)         # 레거시(게이트 미사용)
    min_self_consistency: float = Field(default=0.67, ge=0.0, le=1.0)
    # False=합성 빌드 모드 — 룰 시드 근거(has_real_evidence) 미요구, rule==llm 합의+self-consistency만으로
    # admission(leakage-free 합성이 no_evidence로 무더기 탈락하던 문제 완화·레버3). 운영 기본=True.
    require_evidence: bool = Field(default=True)
    holdout_path: Optional[str] = None                       # 누출 차단용 홀드아웃 jsonl
    # run-스코프 후보 출력 위치. 정본(datasets/gold_real/classification_gold.jsonl)과 산출물이
    # 섞이지 않게 builds/ 하위로 분리(run-스코프 파일명이라 덮어쓰진 않으나 위생). 승격은
    # scripts/promote_golden_candidates.py 로 명시적 게이트 통과 후 정본에 병합.
    out_dir: str = Field(default="datasets/gold_real/builds")
    actor: Actor


class GoldenBuildResponse(BaseModel):
    golden_job_id: UUID
    status_url: str


class GoldenRegisterRequest(BaseModel):
    """이미 만들어진 build_*.jsonl(gold 후보)을 재라벨링 없이 골든 잡으로 등록.

    /golden/build 는 LLM 재라벨링을 하므로 큐레이트된 슬레이트(예: 실서명 스프린트 34건)의
    라벨이 바뀐다. 이 경로는 기존 build 파일을 그대로 잡으로 올려 signoff.html 이 그 후보를
    가리키게 한다(검수자 화면 서명 대상 연결). 경로는 datasets/ 하위로 샌드박스된다.
    """
    build_path: str
    actor: Actor


class GoldenBuildStatus(BaseModel):
    status: str                                  # queued | running | done | failed
    stats: Optional[dict] = None
    gold_count: Optional[int] = None
    uncertain_count: Optional[int] = None
    gold_path: Optional[str] = None
    uncertain_path: Optional[str] = None
    error: Optional[str] = None


# ── 골든셋 검수 서명(locked_gold_eval 승격) — POST /golden/jobs/{id}/signoff ──────
# 지재원 관리자가 빌드 잡의 후보를 화면에서 승인/등급변경/거부하면 그 결정이 golden_signoff.
# promote_to_locked 로 흘러 사람서명(label_source=human_review) 평가정답을 만든다. 운영 교정
# 루프(/confirm)와 별개인 '골든셋 검수' 경로 — golden_signoff.py 계약 그대로.
class GoldenSignoffDecision(BaseModel):
    doc_id: str
    # approve=제안등급 유지 / change=grade 로 변경 / reject=서명 안 함(승격 제외).
    decision: str = Field(pattern=r"^(approve|change|reject)$")
    grade: Optional[str] = Field(default=None, pattern=r"^(TS|S1|S2|S3)$")  # change 시 필수
    note: str = ""

    @model_validator(mode="after")
    def _grade_required_for_change(self) -> "GoldenSignoffDecision":
        # change 인데 grade 미지정이면 서비스가 조용히 no_signoff 로 흘리는 대신 여기서 422 로 거부
        # (검수자 실수를 즉시 피드백 — golden_build_service 의 방어적 skip 은 벨트-서스펜더로 유지).
        if self.decision == "change" and not self.grade:
            raise ValueError("decision=change 인데 grade 미지정")
        return self


class GoldenSignoffRequest(BaseModel):
    decisions: list[GoldenSignoffDecision] = Field(default_factory=list)
    actor: Actor
    # True 면 승격 locked 를 운영 readiness 읽기경로(settings.locked_eval_jsonl)에 dedup 병합
    # (배포 게이트가 즉시 소비). 기본 False = run-스코프 미리보기만(정본·라이브 경로 무변경).
    publish: bool = False
    # 고등급(TS/S1)만 독립 reviewer ≥2 요구(golden_signoff.dual_for_upper). 단일 세션 서명이라
    # 기본 False(단일). True 로 켜면 이 요청의 단일 서명으론 TS/S1 이 insufficient_reviewers 로 남는다.
    dual_for_upper: bool = False


class GoldenSignoffResponse(BaseModel):
    locked: int
    rejected: int
    locked_by_grade: dict
    rejected_reasons: dict
    readiness: dict            # 라이브+이번 승격 union 기준(published=False 면 '미리보기')
    published: bool
    reviewer_id: str           # 실제 기록된 서명자(인증 신원)
    overridden: bool           # 클라 actor.user_id 가 인증 sub 로 덮어써졌나(위조 시도/클라 버그 신호)
    # publish 요청이 라이브 경로에 반영되지 못한 이유(경로 미설정·승격 0건). 정상 반영/미요청이면 None.
    publish_note: Optional[str] = None
