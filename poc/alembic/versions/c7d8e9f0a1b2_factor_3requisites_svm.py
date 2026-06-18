"""factor model → 정본 3요건(S·V·M) B안 곱셈식 정합.

변경:
- weight 합계=1.0 트리거 제거 (B안은 곱셈식 등급=S×V×M — 가산 가중치 미사용)
- 정본 3요건(SECRECY/VALUE/MANAGEMENT) seed + 레거시 4요소 비활성화(is_active=FALSE, FK 보존)
- level_keywords / document_factor_scores / classification_evidence 의 factor_id 레거시→정본 remap
- document_factor_scores.score CHECK 0~5 → 0~2 (S/V/M 각 0·1·2)

근거: doc/22 v2 §3·§5, 영업비밀 등급분류 가이드 11~12p (B안)
주의: 적용/검증은 인프라(Postgres) 필요 — `make infra-up && alembic upgrade head`.
      downgrade는 lossy(ECONOMIC_VALUE·LEAK_IMPACT→VALUE 병합 역산 불가) — 비활성 해제 수준만 복원.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "c7d8e9f0a1b2"
down_revision: Union[str, Sequence[str], None] = "f1e2d3c4b5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 레거시 4요소 factor_code → 정본 3요건 (seeds.LEGACY_FACTOR_ALIAS와 동일)
_REMAP: list[tuple[str, str]] = [
    ("NON_PUBLICITY", "SECRECY"),
    ("ECONOMIC_VALUE", "VALUE"),
    ("LEAK_IMPACT", "VALUE"),       # 유출영향도는 VALUE로 흡수(R4)
    ("MANAGEMENT_LEVEL", "MANAGEMENT"),
]
_FK_TABLES = ("level_keywords", "document_factor_scores", "classification_evidence")
_LEGACY_CODES = ("ECONOMIC_VALUE", "NON_PUBLICITY", "MANAGEMENT_LEVEL", "LEAK_IMPACT")


def upgrade() -> None:
    # 1) weight 합계=1.0 트리거 제거 (먼저 — 이후 factor 변경이 sum=1.0 위반으로 막히지 않게)
    op.execute("DROP TRIGGER IF EXISTS trg_factor_weights_sum ON evaluation_factors;")
    op.execute("DROP FUNCTION IF EXISTS check_factor_weights_sum();")

    # 2) 정본 3요건 seed (weight 미사용 — 곱셈식. KeyError 방지용 1.0)
    op.execute(
        """
        INSERT INTO evaluation_factors (factor_code, factor_name, weight, is_active) VALUES
          ('SECRECY',    '비공지성(S)',      1.0, TRUE),
          ('VALUE',      '경제적 유용성(V)', 1.0, TRUE),
          ('MANAGEMENT', '비밀관리성(M)',    1.0, TRUE)
        ON CONFLICT (factor_code) DO UPDATE SET is_active = TRUE;
        """
    )

    # 3) FK remap: 레거시 factor_id → 정본 factor_id
    for legacy, canon in _REMAP:
        for tbl in _FK_TABLES:
            op.execute(
                f"""
                UPDATE {tbl} SET factor_id =
                    (SELECT factor_id FROM evaluation_factors WHERE factor_code = '{canon}')
                WHERE factor_id =
                    (SELECT factor_id FROM evaluation_factors WHERE factor_code = '{legacy}');
                """
            )

    # 4) 레거시 4요소 비활성화 (삭제 시 FK RESTRICT 위반 → 비활성으로 레지스트리에서 제외)
    legacy_in = ", ".join(f"'{c}'" for c in _LEGACY_CODES)
    op.execute(
        f"UPDATE evaluation_factors SET is_active = FALSE WHERE factor_code IN ({legacy_in});"
    )

    # 5) document_factor_scores.score CHECK 0~5 → 0~2 (S/V/M 각 0·1·2)
    #    컬럼 타입(DECIMAL)은 유지 — 값 제약만 변경(비침습).
    op.execute(
        "ALTER TABLE document_factor_scores DROP CONSTRAINT IF EXISTS document_factor_scores_score_check;"
    )
    op.execute(
        "ALTER TABLE document_factor_scores ADD CONSTRAINT ck_dfs_score_0_2 "
        "CHECK (score >= 0 AND score <= 2);"
    )


def downgrade() -> None:
    # lossy: 정본→레거시 factor_id 역remap은 모호(VALUE←ECONOMIC_VALUE|LEAK_IMPACT)하므로 생략.
    # CHECK·활성상태·트리거만 원복.
    op.execute(
        "ALTER TABLE document_factor_scores DROP CONSTRAINT IF EXISTS ck_dfs_score_0_2;"
    )
    op.execute(
        "ALTER TABLE document_factor_scores ADD CONSTRAINT document_factor_scores_score_check "
        "CHECK (score >= 0 AND score <= 5);"
    )
    legacy_in = ", ".join(f"'{c}'" for c in _LEGACY_CODES)
    op.execute(
        f"UPDATE evaluation_factors SET is_active = TRUE WHERE factor_code IN ({legacy_in});"
    )
    op.execute(
        "UPDATE evaluation_factors SET is_active = FALSE "
        "WHERE factor_code IN ('SECRECY', 'VALUE', 'MANAGEMENT');"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION check_factor_weights_sum() RETURNS TRIGGER AS $$
        DECLARE total DECIMAL(4,3);
        BEGIN
          SELECT COALESCE(SUM(weight), 0) INTO total FROM evaluation_factors WHERE is_active = TRUE;
          IF ABS(total - 1.0) > 0.01 THEN
            RAISE EXCEPTION 'evaluation_factors active weight sum must be 1.0 (current: %)', total;
          END IF;
          RETURN NULL;
        END; $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER trg_factor_weights_sum AFTER INSERT OR UPDATE OR DELETE "
        "ON evaluation_factors DEFERRABLE INITIALLY DEFERRED "
        "FOR EACH ROW EXECUTE FUNCTION check_factor_weights_sum();"
    )
