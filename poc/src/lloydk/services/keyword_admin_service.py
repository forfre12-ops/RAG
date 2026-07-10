"""태깅 키워드 관리 서비스 — /admin/keywords CRUD (FUN-023).

목록/추가/수정/비활성(is_active)을 tb_level_keywords(ORM LevelKeyword)에 대해 수행하고,
변경 후 서빙 룰엔진을 핫리로드(ClassifyService.reload_rules)해 재기동 없이 반영한다.

설계 정합:
- ORM만 사용(raw SQL 무접두 테이블명 버그 회피 — seed_keywords.py 잠복버그와 동일 계열 방지).
- 등급/요소 코드는 활성 tb_classification_levels·tb_evaluation_factors 로 id 해석. 레거시 4요소
  코드는 to_canonical_factor 로 정본(S·V·M)에 매핑(seeds 300+ 재태깅 없이 정합).
- 삭제는 소프트(is_active=false) — FK RESTRICT·감사 보존과 정합.
- DB 미가용: 목록은 best-effort(빈 목록+warning), 변경은 KeywordAdminError(503)로 명시 실패.
"""

from __future__ import annotations

import logging

from sqlalchemy.exc import SQLAlchemyError

from lloydk.db import session_scope
from lloydk.db.models import ClassificationLevel, EvaluationFactor, LevelKeyword
from lloydk.modules.m3_labeling.seeds import to_canonical_factor
from lloydk.schemas.keyword_admin import (
    KeywordCreateRequest,
    KeywordItem,
    KeywordListResponse,
    KeywordMutationResponse,
    KeywordUpdateRequest,
)

logger = logging.getLogger(__name__)


class KeywordAdminError(Exception):
    """서비스→라우터 오류 신호(HTTP status + detail). 라우터가 HTTPException 으로 변환."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


# ── 순수 헬퍼 (DB 무관, 단위 테스트 용이) ──────────────────────────────────────
def resolve_grade_id(level_code_to_id: dict[str, int], grade: str) -> int:
    """등급 코드 → level_id. 활성 등급이 아니면 400."""
    lid = level_code_to_id.get(grade)
    if lid is None:
        raise KeywordAdminError(
            400, f"unknown or inactive grade code: {grade!r} (활성 등급: {sorted(level_code_to_id)})"
        )
    return lid


def resolve_factor_id(
    factor_code_to_id: dict[str, int], factor: str | None
) -> tuple[int | None, list[str]]:
    """평가요소 코드 → factor_id. 미제공이면 (None, []). 제공됐으나 미해석이면
    (None, [warning]) — factor_id 는 nullable 이라 치명적 아님(룰엔진이 기본요소로 흡수)."""
    if not factor:
        return None, []
    fid = factor_code_to_id.get(factor)
    if fid is None:
        fid = factor_code_to_id.get(to_canonical_factor(factor))
    if fid is None:
        return None, [f"unknown factor code {factor!r} — stored as NULL (룰엔진 기본요소로 흡수)"]
    return fid, []


def to_item(kw: LevelKeyword, grade_code: str, factor_code: str | None) -> KeywordItem:
    return KeywordItem(
        keyword_id=kw.keyword_id,
        grade=grade_code,
        keyword=kw.keyword,
        pattern_type=kw.pattern_type,
        factor=factor_code,
        weight=float(kw.weight if kw.weight is not None else 1.0),
        is_active=bool(kw.is_active),
    )


class KeywordAdminService:
    # ── 조회 ──────────────────────────────────────────────────────────────────
    def list(
        self,
        grade: str | None = None,
        factor: str | None = None,
        active_only: bool = True,
        limit: int = 500,
        offset: int = 0,
    ) -> KeywordListResponse:
        try:
            with session_scope() as db:
                id_to_grade = {
                    lv.level_id: lv.level_code for lv in db.query(ClassificationLevel).all()
                }
                grade_to_id = {code: lid for lid, code in id_to_grade.items()}
                id_to_factor = {
                    f.factor_id: f.factor_code for f in db.query(EvaluationFactor).all()
                }
                factor_to_id = {code: fid for fid, code in id_to_factor.items()}

                q = db.query(LevelKeyword)
                if active_only:
                    q = q.filter(LevelKeyword.is_active.is_(True))
                if grade:
                    lid = grade_to_id.get(grade)
                    if lid is None:
                        return KeywordListResponse(
                            keywords=[], count=0, warnings=[f"unknown grade code: {grade}"]
                        )
                    q = q.filter(LevelKeyword.level_id == lid)
                if factor:
                    fid, _w = resolve_factor_id(factor_to_id, factor)
                    if fid is None:
                        return KeywordListResponse(
                            keywords=[], count=0, warnings=[f"unknown factor code: {factor}"]
                        )
                    q = q.filter(LevelKeyword.factor_id == fid)

                rows = (
                    q.order_by(LevelKeyword.level_id, LevelKeyword.keyword_id)
                    .offset(max(offset, 0))
                    .limit(max(min(limit, 2000), 1))
                    .all()
                )
                items = [
                    to_item(
                        kw,
                        id_to_grade.get(kw.level_id, "?"),
                        id_to_factor.get(kw.factor_id) if kw.factor_id else None,
                    )
                    for kw in rows
                ]
                return KeywordListResponse(keywords=items, count=len(items))
        except SQLAlchemyError as exc:
            logger.warning("keyword list db unavailable: %s", exc)
            return KeywordListResponse(
                keywords=[], count=0, warnings=[f"db unavailable: {type(exc).__name__}"]
            )

    # ── 추가 ──────────────────────────────────────────────────────────────────
    def create(self, req: KeywordCreateRequest) -> KeywordMutationResponse:
        try:
            with session_scope() as db:
                grade_to_id, _id_to_grade = self._grade_maps(db)
                factor_to_id, id_to_factor = self._factor_maps(db)
                level_id = resolve_grade_id(grade_to_id, req.grade)
                factor_id, warns = resolve_factor_id(factor_to_id, req.factor)
                kw = LevelKeyword(
                    level_id=level_id,
                    keyword=req.keyword.strip(),
                    pattern_type=req.pattern_type,
                    factor_id=factor_id,
                    weight=req.weight,
                    source=f"admin:{req.actor.user_id}"[:30],
                    is_active=True,
                )
                db.add(kw)
                db.flush()  # keyword_id 채우기
                item = to_item(kw, req.grade, id_to_factor.get(factor_id) if factor_id else None)
                kid = kw.keyword_id
        except KeywordAdminError:
            raise
        except SQLAlchemyError as exc:
            logger.error("keyword create db failed: %s", exc, exc_info=True)
            raise KeywordAdminError(503, f"db unavailable: {type(exc).__name__}") from exc

        reloaded, seed_count = self._reload_serving()
        return KeywordMutationResponse(
            keyword_id=kid, action="created", item=item,
            serving_reloaded=reloaded, seed_count=seed_count, note="; ".join(warns),
        )

    # ── 수정·비활성 ────────────────────────────────────────────────────────────
    def update(self, keyword_id: int, req: KeywordUpdateRequest) -> KeywordMutationResponse:
        try:
            with session_scope() as db:
                kw = (
                    db.query(LevelKeyword)
                    .filter(LevelKeyword.keyword_id == keyword_id)
                    .one_or_none()
                )
                if kw is None:
                    raise KeywordAdminError(404, f"keyword_id {keyword_id} not found")

                _grade_to_id, id_to_grade = self._grade_maps(db)
                factor_to_id, id_to_factor = self._factor_maps(db)

                changed: list[str] = []
                warns: list[str] = []
                if req.keyword is not None:
                    kw.keyword = req.keyword.strip()
                    changed.append("keyword")
                if req.pattern_type is not None:
                    kw.pattern_type = req.pattern_type
                    changed.append("pattern_type")
                if req.factor is not None:
                    fid, w = resolve_factor_id(factor_to_id, req.factor)
                    kw.factor_id = fid
                    warns += w
                    changed.append("factor")
                if req.weight is not None:
                    kw.weight = req.weight
                    changed.append("weight")
                if req.is_active is not None:
                    kw.is_active = req.is_active
                    changed.append("is_active")

                db.flush()
                item = to_item(
                    kw,
                    id_to_grade.get(kw.level_id, "?"),
                    id_to_factor.get(kw.factor_id) if kw.factor_id else None,
                )
                kid = kw.keyword_id
                action = "deactivated" if req.is_active is False else "updated"
                if not changed:
                    warns.append("no fields changed")
        except KeywordAdminError:
            raise
        except SQLAlchemyError as exc:
            logger.error("keyword update db failed: %s", exc, exc_info=True)
            raise KeywordAdminError(503, f"db unavailable: {type(exc).__name__}") from exc

        reloaded, seed_count = self._reload_serving()
        return KeywordMutationResponse(
            keyword_id=kid, action=action, item=item,
            serving_reloaded=reloaded, seed_count=seed_count, note="; ".join(warns),
        )

    # ── 내부 ──────────────────────────────────────────────────────────────────
    @staticmethod
    def _grade_maps(db) -> tuple[dict[str, int], dict[int, str]]:
        levels = (
            db.query(ClassificationLevel)
            .filter(ClassificationLevel.is_active.is_(True))
            .all()
        )
        code_to_id = {lv.level_code: lv.level_id for lv in levels}
        id_to_code = {lv.level_id: lv.level_code for lv in levels}
        return code_to_id, id_to_code

    @staticmethod
    def _factor_maps(db) -> tuple[dict[str, int], dict[int, str]]:
        factors = (
            db.query(EvaluationFactor)
            .filter(EvaluationFactor.is_active.is_(True))
            .all()
        )
        code_to_id = {f.factor_code: f.factor_id for f in factors}
        id_to_code = {f.factor_id: f.factor_code for f in factors}
        return code_to_id, id_to_code

    @staticmethod
    def _reload_serving() -> tuple[bool, int | None]:
        """서빙 룰엔진을 DB 기준으로 핫리로드(best-effort). 실패해도 변경은 이미 커밋됨."""
        try:
            from lloydk.services.classify_service import ClassifyService  # noqa: PLC0415

            info = ClassifyService.get_instance().reload_rules()
            return bool(info.get("reloaded")), info.get("seed_count")
        except Exception as exc:  # noqa: BLE001
            logger.warning("keyword mutation: serving rule reload skipped: %s", exc)
            return False, None
