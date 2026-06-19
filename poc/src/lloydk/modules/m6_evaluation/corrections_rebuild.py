"""교정(corrections) → 라벨된 학습데이터 재빌드 — A2-① (doc/36 본개발 #1).

검수자 교정이 학습에 **반영되지 않은 채** consumed로 마크되면 데이터가 유실된다
(기존 train_classifier_task: spec_kwargs=None → 기본 datasets/labeled만 학습, 교정은
랜덤 run_id로 소비). 이 모듈은 unconsumed corrections를 `{text, label}` JSONL로 복원해
재학습 입력으로 쓰고, **실제로 학습셋에 포함된 correction_id만** 반환해 호출부가 그
id들만 소비하도록 한다(반영 없이 소비 = 유실, 을 원천 차단).

정답 라벨 = 해당 분류의 **가장 최신 correction의 corrected_level_code** (검수자 최종 동의).
본문 복원 우선순위: Chunk.content(전문, chunk_index 순) → Document.text_preview(≤2000자).
둘 다 없으면 그 문서는 건너뛴다(라벨을 본문 없이 날조하지 않음, fail-SECURE).

순수 DB 조회 — 모델·GPU 불요. DB 미가용/빈 결과는 빈 RebuildResult로 graceful degrade.
4등급 스킴(TS/S1/S2/S3) 외 커스텀 등급 교정은 trainer(_LABEL2ID)가 모르므로 제외한다.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from lloydk.db import session_scope
from lloydk.db.models import (
    Chunk,
    Classification,
    ClassificationLevel,
    Correction,
    Document,
)

logger = logging.getLogger(__name__)

# trainer._LABEL_LIST와 일치 — 4등급 스킴만 재학습 입력으로 허용.
_TRAINABLE_LABELS = ("TS", "S1", "S2", "S3")
_DEFAULT_MIN_CHARS = 10


@dataclass
class RebuildResult:
    rows: list[dict] = field(default_factory=list)        # [{"text":.., "label":..}, ..]
    correction_ids: list[int] = field(default_factory=list)  # 실제 포함된 correction_id (소비 대상)
    doc_count: int = 0
    skipped_no_text: int = 0
    skipped_bad_label: int = 0
    reason: str = ""

    @property
    def row_count(self) -> int:
        return len(self.rows)


def _reconstruct_text(db, doc_id, *, min_chars: int) -> str | None:
    """문서 본문 복원 — Chunk.content(전문) 우선, 없으면 text_preview.

    None 반환 = 학습에 쓸 만한 본문 없음(건너뜀). min_chars 미만도 None.
    """
    chunks = list(
        db.execute(
            select(Chunk.content)
            .where(Chunk.doc_id == doc_id)
            .order_by(Chunk.chunk_index)
        ).scalars()
    )
    if chunks:
        text = "\n".join(c for c in chunks if c)
        if len(text.strip()) >= min_chars:
            return text
    # 폴백: text_preview (≤2000자)
    preview = db.execute(
        select(Document.text_preview).where(Document.doc_id == doc_id)
    ).scalar_one_or_none()
    if preview and len(preview.strip()) >= min_chars:
        return preview
    return None


def build_labeled_rows_from_corrections(
    *,
    only_unconsumed: bool = True,
    min_chars: int = _DEFAULT_MIN_CHARS,
    trainable_labels: Sequence[str] = _TRAINABLE_LABELS,
    tenant_id: str | None = None,
) -> RebuildResult:
    """unconsumed corrections를 {text,label} 행으로 복원.

    문서별로 가장 최신 correction을 정답으로 채택(검수자 최종 동의). 한 문서에 여러
    correction이 있어도 라벨 행은 1개, correction_ids에는 그 문서에 묶인 모든 unconsumed
    correction_id를 담아(전부 소비) 다음 tick 재유입을 막는다.

    DB 미가용/빈 결과 → 빈 RebuildResult(reason 명시).
    """
    label_set = set(trainable_labels)
    try:
        with session_scope() as db:
            code_by_id = {
                lv.level_id: lv.level_code
                for lv in db.execute(select(ClassificationLevel)).scalars()
            }

            # 최신 교정 우선 — corrected_at desc + correction_id desc(보조).
            # PostgreSQL now()는 트랜잭션 내 고정값이라 같은 트랜잭션/동시각 교정은 corrected_at이
            # 동일할 수 있다. 단조증가 correction_id를 2차 키로 써 '진짜 마지막' 교정을 결정한다
            # (보조키 없으면 동시각 교정에서 옛 등급이 정답으로 잡혀 미탐 라벨 유입 가능).
            corr_stmt = (
                select(Correction)
                .order_by(Correction.corrected_at.desc(), Correction.correction_id.desc())
            )
            if only_unconsumed:
                corr_stmt = corr_stmt.where(Correction.consumed_in_run.is_(None))
            corrections = list(db.execute(corr_stmt).scalars())
            if not corrections:
                return RebuildResult(reason="no_unconsumed_corrections")

            # classification_id → doc_id (+ tenant) 매핑
            cls_ids = {c.classification_id for c in corrections}
            cls_stmt = select(Classification).where(
                Classification.classification_id.in_(cls_ids)
            )
            if tenant_id:
                cls_stmt = cls_stmt.where(Classification.tenant_id == tenant_id)
            doc_by_cls = {
                c.classification_id: c.doc_id
                for c in db.execute(cls_stmt).scalars()
            }

            # 문서별: 최신 라벨(첫 등장 = corrected_at desc 정렬이라 최신) + 묶인 correction_id 전부
            doc_label: dict = {}
            doc_corr_ids: dict = {}
            skipped_bad_label = 0
            for corr in corrections:  # 이미 corrected_at desc
                doc_id = doc_by_cls.get(corr.classification_id)
                if doc_id is None:
                    continue  # tenant 스코프 밖 또는 분류 없음
                code = code_by_id.get(corr.corrected_level_id)
                if code not in label_set:
                    skipped_bad_label += 1
                    continue
                doc_corr_ids.setdefault(doc_id, []).append(corr.correction_id)
                if doc_id not in doc_label:  # 최신만 라벨로
                    doc_label[doc_id] = code

            rows: list[dict] = []
            consumed_ids: list[int] = []
            skipped_no_text = 0
            for doc_id, label in doc_label.items():
                text = _reconstruct_text(db, doc_id, min_chars=min_chars)
                if text is None:
                    skipped_no_text += 1
                    continue  # 본문 없는 문서는 라벨을 날조하지 않음 + 소비도 안 함
                rows.append({"text": text, "label": label})
                consumed_ids.extend(doc_corr_ids.get(doc_id, []))

            return RebuildResult(
                rows=rows,
                correction_ids=sorted(set(consumed_ids)),
                doc_count=len(rows),
                skipped_no_text=skipped_no_text,
                skipped_bad_label=skipped_bad_label,
                reason="ok" if rows else "no_rows_after_filter",
            )
    except SQLAlchemyError as exc:
        logger.debug("corrections rebuild skipped: %s", exc)
        return RebuildResult(reason=f"db_unavailable:{type(exc).__name__}")


def merge_into_train_jsonl(
    base_train_path: str | Path | None,
    result: RebuildResult,
    out_path: str | Path,
) -> str | None:
    """기존 train.jsonl(base) + 교정 행을 합쳐 새 학습셋을 만든다(A2-① 반영).

    **train만** 증강한다 — val/test(홀드아웃)는 건드리지 않아 배포 게이트(deploy_gate)가
    교정 누출 없는 안정 홀드아웃에서 fnr_high를 측정하도록 보장한다(C-eval 정합).

    base가 없거나 비어 있으면 교정 행만으로 학습셋을 만든다(경고 수준 — 소량 학습 위험은
    호출부 책임). 교정 행도 없으면 None 반환(병합 불필요 → 호출부는 기본 경로 학습).
    """
    if not result.rows:
        return None
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    n_base = 0
    with out.open("w", encoding="utf-8") as f:
        if base_train_path:
            base = Path(base_train_path)
            if base.exists():
                for line in base.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        f.write(line + "\n")
                        n_base += 1
        for row in result.rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    logger.info(
        "merged train set: base=%d + corrections=%d → %s",
        n_base, len(result.rows), out,
    )
    return str(out)


def write_labeled_jsonl(result: RebuildResult, out_path: str | Path) -> str:
    """RebuildResult.rows를 JSONL로 기록(append 아님, 덮어쓰기). 경로 문자열 반환.

    상위 디렉토리 자동 생성. 행이 없으면 빈 파일을 만들지 않고 경로만 반환(빈 학습 방지는
    호출부 책임 — 보통 build 결과 row_count==0이면 학습 자체를 skip).
    """
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for row in result.rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return str(out)
