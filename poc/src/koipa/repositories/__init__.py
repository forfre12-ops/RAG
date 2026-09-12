"""Repository layer — SQLAlchemy ORM 단위 도메인 CRUD.

각 repo는 하나의 도메인을 담당하고, Session을 외부 주입받습니다.
트랜잭션 경계는 Service/route 레이어에서 session_scope로 제어합니다.
"""

from koipa.repositories.audit_repo import AuditRepo
from koipa.repositories.chunk_repo import ChunkRepo
from koipa.repositories.classify_repo import ClassifyRepo
from koipa.repositories.document_repo import DocumentRepo
from koipa.repositories.llm_usage_repo import LlmUsageRepo
from koipa.repositories.synth_repo import SynthRepo
from koipa.repositories.training_repo import TrainingRepo

__all__ = [
    "AuditRepo",
    "ChunkRepo",
    "ClassifyRepo",
    "DocumentRepo",
    "LlmUsageRepo",
    "SynthRepo",
    "TrainingRepo",
]
