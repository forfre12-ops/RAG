"""Schema admin service — /schema/grades GET·PUT.

설계:
- GET: classification_levels 테이블에서 현재 등급체계 조회 (DB 진실 소스)
- PUT: 확정된 TS/S1/S2/S3의 표시 메타데이터(명칭·설명·색·순서)만 수정한다.
       등급 코드 추가·삭제·변경은 모델 유무와 무관하게 409로 거부한다.
- 모두 best-effort: DB 미가용 시 기본 4등급 응답 / PUT은 warning.
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

# 2026-09-20에 확정한 단일 등급 체계. 학습기 라벨 목록
# (m4_training/trainer.py _LABEL_LIST)과도 같다. 이 경계는 경고나 재학습 안내가 아니라
# API가 강제하는 불변식이다. 새 등급 체계가 필요하면 별도 정책·모델·마이그레이션 출시로
# 다루며, 운영 중 PUT과 force로 바꾸지 않는다.
_FIXED_GRADE_CODES = frozenset(g.value for g in Grade)


class GradeSchemaBlocked(Exception):
    """확정된 4등급 체계를 깨는 변경 — API 가 409 로 돌려준다."""


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
) -> tuple[str, list[str]]:
    """PUT이 확정된 4개 코드 외의 집합을 만들지 못하게 한다.

    ``model_codes``와 ``model_name``은 기존 호출자와 복구 진단 호환을 위해 받지만,
    이제 정책 불변식을 우회시키는 근거가 될 수 없다. 모델이 없거나 메타를 못 읽는 경우도
    동일하게 차단해야 했던 것이 이전 force 경로의 구멍이었다.
    """
    del existing, model_codes, model_name
    if proposed != _FIXED_GRADE_CODES:
        raise GradeSchemaBlocked(
            "등급 코드 집합은 정책상 TS·S1·S2·S3 네 개로 고정되어 있습니다. "
            "PUT /schema/grades에서는 코드 추가·삭제·변경을 할 수 없습니다."
        )
    return "", []


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
        "schema metadata put enter: grades_count=%d actor=%s",
            len(req.grades), req.actor.user_id,
        )
        reasons: list[str] = []
        new_codes = {g.code for g in req.grades}
        # 집합 검증은 DB·서빙 모델 상태보다 먼저 수행한다. DB가 잠시 비가용하거나 모델이
        # 아직 로드되지 않았다는 이유로 새 등급이 저장되는 우회 경로를 만들지 않는다.
        if len(req.grades) != len(_FIXED_GRADE_CODES) or len(new_codes) != len(req.grades):
            raise GradeSchemaBlocked(
                "등급 코드는 TS·S1·S2·S3 네 개를 각각 한 번씩만 포함해야 합니다."
            )
        assess_grade_change(set(), new_codes, None)
        try:
            with session_scope() as db:
                existing_codes = {
                    lv.level_code
                    for lv in db.query(ClassificationLevel).filter(ClassificationLevel.is_active.is_(True)).all()
                }

                removed = existing_codes - new_codes
                if removed:
                    # 과거 force 우회로 남은 비정상 활성 코드만 이 경로로 정리한다.
                    reasons.append(f"legacy codes deactivated: {sorted(removed)}")

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
            "schema metadata put done: reasons=%s",
            reasons,
        )
        return GradesPutResponse(
            version=self._version_tag(),
            requires_retraining=False,
            reason="; ".join(reasons) if reasons else "fixed grade metadata updated",
            serving_impact="",
        )
