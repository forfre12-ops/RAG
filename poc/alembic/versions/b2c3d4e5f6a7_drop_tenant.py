"""멀티테넌트 전면 제거 — tb_tenants DROP + 7테이블 tenant_id 컬럼/FK/인덱스 제거.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-06-25

2026-06-24 확정: Lloydk는 단일 고객사 엔진 — per-customer 격리는 KL 포털이
라우팅으로 전담(상류 보장). Lloydk 내부엔 tenant 경계가 불필요하므로
tenant_id 컬럼·FK·복합인덱스·UNIQUE 와 tb_tenants 테이블 자체를 제거한다.

제거 대상:
- tb_tenants 테이블 (FK depend 컬럼을 먼저 떼야 DROP 가능)
- tenant_id 컬럼 (FK 포함) 7테이블:
    tb_documents · tb_chunks · tb_classifications · tb_sample_documents ·
    tb_llm_usage · tb_audit_log · tb_guides
- tenant 선행 복합인덱스 8개:
    idx_doc_tenant_status · idx_doc_tenant_uploaded · idx_chunk_tenant ·
    idx_cls_tenant_status · idx_cls_tenant_classified · idx_sd_tenant ·
    idx_audit_tenant_occurred · idx_audit_tenant_action
- idx_doc_hash UNIQUE(tenant_id,file_hash) → UNIQUE(file_hash) 교체
- tb_guides UNIQUE(guide_id,version,tenant_id) → UNIQUE(guide_id,version) 교체
- ORM 잔존 prefix 인덱스 보충: idx_doc_status · idx_cls_status · idx_cls_classified

멱등: 전부 IF EXISTS / DO-EXCEPTION 가드 (재실행·부분상태 안전).
주의: tb_chunks·tb_audit_log·tb_llm_usage 는 RANGE PARTITION 부모.
  ALTER TABLE ... DROP COLUMN 은 PG가 모든 자식 파티션에 자동 전파(부모만 ALTER).
  인덱스도 부모 인덱스 DROP 이 자식 파티션 인덱스에 전파됨.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- 1) tenant 선행 복합인덱스 8개 DROP (멱등) ---
    for idx in (
        "idx_doc_tenant_status",
        "idx_doc_tenant_uploaded",
        "idx_chunk_tenant",
        "idx_cls_tenant_status",
        "idx_cls_tenant_classified",
        "idx_sd_tenant",
        "idx_audit_tenant_occurred",
        "idx_audit_tenant_action",
    ):
        op.execute(f"DROP INDEX IF EXISTS {idx}")

    # 잔존 prefix 인덱스 보충 — tenant 선두를 떼고 나머지 컬럼만으로 hot path 유지.
    # (ORM models.py 와 1:1 정합: idx_doc_status·idx_cls_status·idx_cls_classified)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_doc_status ON tb_documents (processing_status)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_cls_status ON tb_classifications (status)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_cls_classified ON tb_classifications (classified_at)"
    )

    # --- 2) idx_doc_hash UNIQUE: (tenant_id, file_hash) → (file_hash) ---
    op.execute("DROP INDEX IF EXISTS idx_doc_hash")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_doc_hash "
        "ON tb_documents (file_hash) WHERE file_hash IS NOT NULL"
    )

    # --- 3) tb_guides UNIQUE: (guide_id,version,tenant_id) → (guide_id,version) ---
    # 기존 tenant 차원 제거로 (guide_id,version) 중복이 생길 수 있어 max(id)만 남기고 선정리.
    op.execute(
        """
        DELETE FROM tb_guides a
        USING tb_guides b
        WHERE a.id < b.id
          AND a.guide_id = b.guide_id
          AND a.version  = b.version;
        """
    )
    op.execute(
        "ALTER TABLE tb_guides DROP CONSTRAINT IF EXISTS uq_guides_id_version_tenant"
    )
    op.execute("DROP INDEX IF EXISTS idx_guides_tenant_guide")
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'uq_guides_id_version'
            ) THEN
                ALTER TABLE tb_guides
                    ADD CONSTRAINT uq_guides_id_version UNIQUE (guide_id, version);
            END IF;
        END$$;
        """
    )

    # --- 4) tenant_id 컬럼/FK DROP — 7테이블 (FK는 컬럼 DROP에 함께 사라짐) ---
    # tb_tenants 를 참조하는 컬럼을 먼저 떼야 테이블 DROP 가능.
    for tbl in (
        "tb_documents",
        "tb_chunks",
        "tb_classifications",
        "tb_sample_documents",
        "tb_llm_usage",
        "tb_audit_log",
        "tb_guides",
    ):
        op.execute(f"ALTER TABLE {tbl} DROP COLUMN IF EXISTS tenant_id")

    # --- 5) tb_tenants 테이블 DROP ---
    op.execute("DROP TABLE IF EXISTS tb_tenants")


def downgrade() -> None:
    # 잔존 prefix 인덱스 제거 (tenant 복합인덱스로 대체될 것).
    op.execute("DROP INDEX IF EXISTS idx_doc_status")
    op.execute("DROP INDEX IF EXISTS idx_cls_status")
    op.execute("DROP INDEX IF EXISTS idx_cls_classified")

    # 역작업 — tenant 모델을 단일 'default' 테넌트로 복구(롤백 안전).
    # 1) tb_tenants 재생성 + default 행.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS tb_tenants (
            tenant_id            VARCHAR(50)  PRIMARY KEY,
            name                 VARCHAR(200) NOT NULL,
            rag_enabled          BOOLEAN      DEFAULT TRUE,
            active_model_version VARCHAR(50),
            api_key_hash         VARCHAR(128),
            metadata             JSONB        DEFAULT '{}'::jsonb,
            is_active            BOOLEAN      DEFAULT TRUE,
            created_at           TIMESTAMPTZ  DEFAULT now()
        )
        """
    )
    op.execute(
        "INSERT INTO tb_tenants (tenant_id, name) VALUES ('default', 'default') "
        "ON CONFLICT (tenant_id) DO NOTHING"
    )

    # 2) tenant_id 컬럼 재추가 (기존 행은 'default' 백필 → NOT NULL 승격).
    #    nullable 컬럼들(llm_usage·audit_log·guides는 원래 NOT NULL이 아니거나 default 보유).
    op.execute(
        "ALTER TABLE tb_documents ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(50)"
    )
    op.execute("UPDATE tb_documents SET tenant_id = 'default' WHERE tenant_id IS NULL")
    op.execute("ALTER TABLE tb_documents ALTER COLUMN tenant_id SET NOT NULL")
    op.execute(
        "ALTER TABLE tb_documents ADD CONSTRAINT tb_documents_tenant_id_fkey "
        "FOREIGN KEY (tenant_id) REFERENCES tb_tenants(tenant_id)"
    )

    op.execute("ALTER TABLE tb_chunks ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(50)")
    op.execute("UPDATE tb_chunks SET tenant_id = 'default' WHERE tenant_id IS NULL")
    op.execute("ALTER TABLE tb_chunks ALTER COLUMN tenant_id SET NOT NULL")

    op.execute(
        "ALTER TABLE tb_classifications ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(50)"
    )
    op.execute(
        "UPDATE tb_classifications SET tenant_id = 'default' WHERE tenant_id IS NULL"
    )
    op.execute("ALTER TABLE tb_classifications ALTER COLUMN tenant_id SET NOT NULL")
    op.execute(
        "ALTER TABLE tb_classifications ADD CONSTRAINT tb_classifications_tenant_id_fkey "
        "FOREIGN KEY (tenant_id) REFERENCES tb_tenants(tenant_id)"
    )

    op.execute(
        "ALTER TABLE tb_sample_documents ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(50)"
    )
    op.execute(
        "UPDATE tb_sample_documents SET tenant_id = 'default' WHERE tenant_id IS NULL"
    )
    op.execute("ALTER TABLE tb_sample_documents ALTER COLUMN tenant_id SET NOT NULL")
    op.execute(
        "ALTER TABLE tb_sample_documents ADD CONSTRAINT tb_sample_documents_tenant_id_fkey "
        "FOREIGN KEY (tenant_id) REFERENCES tb_tenants(tenant_id)"
    )

    # llm_usage.tenant_id 는 원래 nullable + FK.
    op.execute("ALTER TABLE tb_llm_usage ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(50)")
    op.execute(
        "ALTER TABLE tb_llm_usage ADD CONSTRAINT tb_llm_usage_tenant_id_fkey "
        "FOREIGN KEY (tenant_id) REFERENCES tb_tenants(tenant_id)"
    )

    # audit_log.tenant_id 는 원래 nullable, FK 없음.
    op.execute("ALTER TABLE tb_audit_log ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(50)")

    # guides.tenant_id 는 원래 NOT NULL DEFAULT 'default', FK 없음.
    op.execute(
        "ALTER TABLE tb_guides ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(50) "
        "NOT NULL DEFAULT 'default'"
    )

    # 3) idx_doc_hash 복구 — (file_hash) → (tenant_id, file_hash).
    op.execute("DROP INDEX IF EXISTS idx_doc_hash")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_doc_hash "
        "ON tb_documents (tenant_id, file_hash) WHERE file_hash IS NOT NULL"
    )

    # 4) guides UNIQUE 복구 — (guide_id,version) → (guide_id,version,tenant_id).
    op.execute("ALTER TABLE tb_guides DROP CONSTRAINT IF EXISTS uq_guides_id_version")
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'uq_guides_id_version_tenant'
            ) THEN
                ALTER TABLE tb_guides ADD CONSTRAINT uq_guides_id_version_tenant
                    UNIQUE (guide_id, version, tenant_id);
            END IF;
        END$$;
        """
    )

    # 5) tenant 선행 복합인덱스 8개 재생성.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_doc_tenant_status "
        "ON tb_documents (tenant_id, processing_status)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_doc_tenant_uploaded "
        "ON tb_documents (tenant_id, uploaded_at)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_chunk_tenant ON tb_chunks (tenant_id)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_cls_tenant_status "
        "ON tb_classifications (tenant_id, status)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_cls_tenant_classified "
        "ON tb_classifications (tenant_id, classified_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_sd_tenant ON tb_sample_documents (tenant_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_tenant_occurred "
        "ON tb_audit_log (tenant_id, occurred_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_tenant_action "
        "ON tb_audit_log (tenant_id, action, occurred_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_guides_tenant_guide "
        "ON tb_guides (tenant_id, guide_id)"
    )
