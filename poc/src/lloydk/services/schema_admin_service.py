"""Schema admin service — /schema/grades GET·PUT.

설계:
- GET: classification_levels 테이블에서 현재 등급체계 조회 (DB 진실 소스)
- PUT: 등급 추가/수정 (level_code 매치로 upsert), 등급 삭제는 is_active=FALSE.
       재학습 필요성은 '등급 수가 변경되거나 신규 코드 추가' 시 True.
- 모두 best-effort: DB 미가용 시 기본 4등급 응답 / PUT은 warning.
"""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy.exc import SQLAlchemyError

from lloydk.db import session_scope
from lloydk.db.models import ClassificationLevel
from lloydk.schemas.common import GradeDefinition, GradeRegistry
from lloydk.schemas.schema_admin import (
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


class SchemaAdminService:
    @staticmethod
    def _version_tag() -> str:
        return dt.datetime.now(dt.timezone.utc).strftime("v%Y%m%d-%H%M%S")

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
        try:
            with session_scope() as db:
                existing_codes = {
                    lv.level_code
                    for lv in db.query(ClassificationLevel).filter(ClassificationLevel.is_active.is_(True)).all()
                }
                new_codes = {g.code for g in req.grades}

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

        # 등급체계 변경 후 GradeRegistry 캐시 무효화 — 다음 추론부터 새 등급 반영
        GradeRegistry.invalidate()
        logger.info(
            "schema put done: requires_retraining=%s reasons=%s",
            requires, reasons,
        )
        return GradesPutResponse(
            version=self._version_tag(),
            requires_retraining=requires,
            reason="; ".join(reasons) if reasons else "no structural change",
        )
