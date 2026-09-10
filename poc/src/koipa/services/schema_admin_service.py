"""Schema admin service — /schema/grades GET·PUT.

설계:
- GET: classification_levels 테이블에서 현재 등급체계 조회 (DB 진실 소스)
- PUT: 등급 추가/수정 (level_code 매치로 upsert), 등급 삭제는 is_active=FALSE.
       재학습 필요성은 '등급 수가 변경되거나 신규 코드 추가' 시 True.
- 모두 best-effort: DB 미가용 시 기본 4등급 응답 / PUT은 warning.
- [2026-09-10] 서빙 분류기를 멈추게 하는 변경은 거부한다(assess_grade_change → API 409).
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from koipa.db import session_scope
from koipa.db.models import ClassificationLevel
from koipa.schemas.common import FactorRegistry, Grade, GradeDefinition, GradeRegistry
from koipa.schemas.schema_admin import (
    GradesGetResponse,
    GradesPutRequest,
    GradesPutResponse,
)

logger = logging.getLogger(__name__)

_DEFAULT_GRADES = [
    GradeDefinition(code="TS", name="특급기밀", order=1, color="#7B1FA2"),
    GradeDefinition(code="S1", name="1급 비밀", order=2, color="#D32F2F"),
    GradeDefinition(code="S2", name="2급 대외비", order=3, color="#F57C00"),
    GradeDefinition(code="S3", name="3급 공개", order=4, color="#388E3C"),
]

# 학습기 라벨 목록(m4_training/trainer.py _LABEL_LIST)과 같은 4등급. 이 밖의 코드는
# 재학습으로도 학습되지 않는다 — 학습기 코드를 고쳐야 한다(scripts/audit_grade_arity.py).
_TRAINABLE_CODES = frozenset(g.value for g in Grade)


class GradeSchemaBlocked(Exception):
    """서빙 분류기를 멈추게 하는 등급체계 변경 — API 가 409 로 돌려준다."""


def served_model_codes() -> tuple[str | None, frozenset[str] | None]:
    """지금 서빙이 로드할 모델 디렉터리와 그 모델이 내는 등급 코드 집합.

    모델을 올리지 않고 config.json 의 id2label 만 읽는다. 모델이 없으면(규칙엔진 폴백)
    또는 매핑을 못 읽으면 코드 집합은 None — 지킬 분류기가 없다는 뜻이다. 매핑을 못 읽는
    모델은 서빙도 스스로 로드를 거부한다(pipeline._id2label_from_config).
    """
    try:
        from koipa.services.classify_service import _resolve_serving_model_dir  # noqa: PLC0415

        model_dir = _resolve_serving_model_dir()
    except Exception as exc:  # noqa: BLE001 — 판정을 못 해도 저장 경로를 죽이지 않는다(흔적은 남긴다)
        logger.warning("서빙 모델 경로를 못 구함 — 등급 변경 영향 판정 생략 (%s: %s)", type(exc).__name__, exc)
        return None, None
    if not model_dir:
        return None, None
    cfg = Path(model_dir) / "config.json"
    try:
        raw = json.loads(cfg.read_text(encoding="utf-8")).get("id2label") or {}
    except (OSError, ValueError) as exc:
        logger.warning("모델 등급 매핑(config.json)을 못 읽음 — 영향 판정 생략: %s (%s)", cfg, exc)
        return model_dir, None
    codes = frozenset(str(v) for v in raw.values())
    return model_dir, (codes or None)


def assess_grade_change(
    existing: set[str],
    proposed: set[str],
    model_codes: frozenset[str] | None,
    *,
    model_name: str = "",
    force: bool = False,
    force_reason: str | None = None,
) -> tuple[str, list[str]]:
    """등급 집합 변경이 서빙 분류기에 주는 영향을 판정한다 → (영향 문장, 덧붙일 사유).

    서빙(m5_inference/pipeline.py)은 모델의 등급 집합이 DB 활성 등급 집합과 다르면 모델
    로드를 거부하고 규칙엔진으로 내려간다. 그러므로 활성 집합을 모델과 다르게 만드는 변경은
    다음 로드(재기동·리로드)부터 분류기를 멈춘다. 종전에는 이런 변경이
    requires_retraining=True 만 달고 그대로 저장됐다(2026-09-10 발견).

    거부(GradeSchemaBlocked)하는 경우 — 활성 집합이 바뀌고 · 서빙 모델이 있고 · 새 집합이
    모델 집합과 다르고 · force 가 아니다. force 면 사유가 있어야 한다.
    이름·설명·색·순서만 바꾸는 변경은 집합이 같으므로 통과한다(순서는 모델 매핑과 무관).
    """
    extra: list[str] = []
    untrainable = sorted(proposed - existing - _TRAINABLE_CODES)
    if untrainable:
        extra.append(
            "학습기 라벨이 4등급(TS·S1·S2·S3) 고정이라 재학습으로도 학습하지 못하는 등급: "
            f"{untrainable}"
        )
    if proposed == existing or model_codes is None or proposed == set(model_codes):
        return "", extra
    impact = (
        f"서빙 모델{f'({model_name})' if model_name else ''}의 등급 {sorted(model_codes)} 과 "
        f"새 활성 등급 {sorted(proposed)} 이 달라, 다음 모델 로드(재기동·리로드)부터 "
        "분류기가 로드를 거부하고 규칙엔진으로 판정합니다"
    )
    if not force:
        raise GradeSchemaBlocked(impact + ". 그래도 저장하려면 force=true 와 force_reason 을 함께 보내십시오.")
    if not (force_reason or "").strip():
        raise GradeSchemaBlocked("force=true 는 사유(force_reason)가 있어야 합니다. " + impact)
    extra.append(f"forced: {force_reason.strip()}")
    return impact, extra


class SchemaAdminService:
    @staticmethod
    def _version_tag() -> str:
        return dt.datetime.now(dt.timezone.utc).strftime("v%Y%m%d-%H%M%S")

    @staticmethod
    def _active_codes() -> set[str] | None:
        """활성 등급 코드 — 영향 판정용 읽기. DB 를 못 읽으면 None(판정 생략 · 쓰기 경로가 db_error 를 남긴다)."""
        try:
            with session_scope() as db:
                return {
                    lv.level_code
                    for lv in db.query(ClassificationLevel).filter(ClassificationLevel.is_active.is_(True)).all()
                }
        except SQLAlchemyError as exc:
            logger.warning("등급 변경 영향 판정용 조회 실패 — 판정 생략 (%s: %s)", type(exc).__name__, exc)
            return None

    def get(self) -> GradesGetResponse:
        logger.debug("schema get enter")
        try:
            with session_scope() as db:
                levels = (
                    db.query(ClassificationLevel)
                    .filter(ClassificationLevel.is_active.is_(True))
                    .order_by(ClassificationLevel.level_order)
                    .all()
                )
                grades = [
                    GradeDefinition(
                        code=lv.level_code,
                        name=lv.level_name,
                        order=lv.level_order,
                        description=lv.description,
                        color=lv.color_hex,
                    )
                    for lv in levels
                ]
                if not grades:
                    grades = list(_DEFAULT_GRADES)
                logger.info("schema get done: grades_count=%d", len(grades))
                return GradesGetResponse(version=self._version_tag(), grades=grades)
        except SQLAlchemyError as exc:
            logger.warning("schema get fell back to default: %s", exc)
            return GradesGetResponse(version=self._version_tag(), grades=list(_DEFAULT_GRADES))

    def put(self, req: GradesPutRequest) -> GradesPutResponse:
        """등급체계 변경. 새 코드 추가 / 순서 변경 시 requires_retraining=True."""
        logger.debug(
            "schema put enter: grades_count=%d actor=%s",
            len(req.grades), req.actor.user_id,
        )
        reasons: list[str] = []
        requires = False
        serving_impact = ""
        new_codes = {g.code for g in req.grades}
        # 판정은 쓰기 세션 **밖**에서 한다 — 쓰기 전에 거부하므로 아무것도 바뀌지 않는다.
        # 세션 안에서 거부를 던지면 session_scope 가 정상적인 409 를 ERROR 추적 로그
        # ("session_scope rollback")로 남긴다(2026-09-10 실측). 활성 모델 조회도 자기 세션을 연다.
        model_dir, model_codes = served_model_codes()
        current = self._active_codes()
        if current is not None:
            serving_impact, extra = assess_grade_change(
                current, new_codes, model_codes,
                model_name=Path(model_dir).name if model_dir else "",
                force=req.force, force_reason=req.force_reason,
            )
            reasons.extend(extra)
            if serving_impact:
                logger.warning("등급체계 강제 변경 — actor=%s · %s", req.actor.user_id, serving_impact)
        try:
            with session_scope() as db:
                existing_codes = {
                    lv.level_code
                    for lv in db.query(ClassificationLevel).filter(ClassificationLevel.is_active.is_(True)).all()
                }

                added = new_codes - existing_codes
                removed = existing_codes - new_codes

                if added:
                    requires = True
                    reasons.append(f"added codes: {sorted(added)}")
                if removed:
                    requires = True
                    reasons.append(f"deactivated codes: {sorted(removed)}")

                # upsert
                for g in req.grades:
                    lv = (
                        db.query(ClassificationLevel)
                        .filter(ClassificationLevel.level_code == g.code)
                        .one_or_none()
                    )
                    if lv is None:
                        lv = ClassificationLevel(
                            level_code=g.code,
                            level_name=g.name,
                            level_order=g.order,
                            description=g.description,
                            color_hex=g.color,
                            is_active=True,
                            created_by=req.actor.user_id,
                        )
                        db.add(lv)
                    else:
                        if lv.level_order != g.order:
                            requires = True
                            reasons.append(f"order changed for {g.code}")
                        lv.level_name = g.name
                        lv.level_order = g.order
                        lv.description = g.description
                        lv.color_hex = g.color
                        lv.is_active = True

                # 새 목록에 없는 기존 등급은 deactivate
                for code in removed:
                    lv = (
                        db.query(ClassificationLevel)
                        .filter(ClassificationLevel.level_code == code)
                        .one_or_none()
                    )
                    if lv is not None:
                        lv.is_active = False
        except SQLAlchemyError as exc:
            logger.error("schema put persistence failed: %s", exc, exc_info=True)
            reasons.append(f"db_error:{type(exc).__name__}")

        # 등급·요소 변경 후 캐시 무효화 — 다음 추론부터 새 등급/요소 반영
        GradeRegistry.invalidate()
        FactorRegistry.invalidate()
        logger.info(
            "schema put done: requires_retraining=%s reasons=%s",
            requires, reasons,
        )
        return GradesPutResponse(
            version=self._version_tag(),
            requires_retraining=requires,
            reason="; ".join(reasons) if reasons else "no structural change",
            serving_impact=serving_impact,
        )
