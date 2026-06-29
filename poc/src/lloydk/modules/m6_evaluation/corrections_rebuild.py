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

[#25] merge_into_train_jsonl은 holdout(val/test)와 doc_id/본문이 겹치는 교정 문서를
train append에서 강제 제외해 train↔holdout 평가 누수를 차단한다("파일 불변"만으로는
같은 doc_id가 양쪽에 존재 가능 → 누수). 등급별 cap(stratify)도 opt-in으로 지원한다.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

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
from lloydk.golden_tiers import is_human_reviewer
from lloydk.hygiene import normalize_text

logger = logging.getLogger(__name__)

# trainer._LABEL_LIST와 일치 — 4등급 스킴만 재학습 입력으로 허용.
_TRAINABLE_LABELS = ("TS", "S1", "S2", "S3")
_DEFAULT_MIN_CHARS = 10

# [admission 게이트] 교정이 학습 라벨로 편입되려면 분류 status가 '확정(finalized)'이어야 한다.
# confirmed(확정)·corrected(재라벨 확정)만 허용 — needs_second_review(이중검수 대기)·staging·
# needs_review는 사람의 최종 동의가 아직 없으므로 제외(미탐 라벨이 가중치에 박히는 것 차단).
_ADMISSIBLE_STATUSES = ("confirmed", "corrected")


def _correction_admissible(
    corrected_by: object, cls_status: object, *, require_human_confirmed: bool = True
) -> bool:
    """교정이 학습 라벨로 편입될 자격이 있는가 — admission 게이트(죽음의 나선 최약 고리 차단).

    require_human_confirmed=True(기본):
      ① 사람 검수자(머신/플레이스홀더 id 제외, golden_tiers.is_human_reviewer) AND
      ② 분류 status가 finalized(confirmed/corrected) — 이중검수 완료 또는 불요.
      → 러버스탬프 직전(needs_second_review)·미확정(staging)·머신(ai_assist/llm_judge 등) 교정은
        학습에서 제외. 제외분은 소비하지 않아 추후 확정되면 다음 tick에 재유입된다.
    False: 옛 동작(전부 허용) — 테스트/특수경로 전용.
    (순수 함수 — DB 불요, 단위테스트 가능.)
    """
    if not require_human_confirmed:
        return True
    if not is_human_reviewer(corrected_by):
        return False
    return str(cls_status or "") in _ADMISSIBLE_STATUSES


@dataclass
class RebuildResult:
    rows: list[dict] = field(default_factory=list)        # [{"text":.., "label":..}, ..]
    correction_ids: list[int] = field(default_factory=list)  # 실제 포함된 correction_id (소비 대상)
    # [#25] rows[i] 의 출처 문서 doc_id (rows와 같은 순서·길이). 홀드아웃 누수 차단을
    # doc_id 기준으로도 강제하기 위해 보관한다. 기본 빈 리스트 = 기존 호출부 동작 보존
    # (doc_id 미상이면 merge가 텍스트 기준 누수 차단으로 graceful degrade).
    doc_ids: list = field(default_factory=list)
    doc_count: int = 0
    skipped_no_text: int = 0
    skipped_bad_label: int = 0
    # [admission 게이트] 사람검수+finalized 조건 미통과로 학습 제외된 교정 수(머신/미확정).
    skipped_unadmitted: int = 0
    # [#25] merge_into_train_jsonl이 holdout 중복으로 train에서 제외한 교정 행 수
    # (병합 시점에 갱신). 0 = 제외 없음/미수행. 호출부가 제외 건수를 조회할 수 있게 노출.
    excluded_holdout: int = 0
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
    require_human_confirmed: bool = True,
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

            # classification_id → doc_id 매핑
            cls_ids = {c.classification_id for c in corrections}
            cls_stmt = select(Classification).where(
                Classification.classification_id.in_(cls_ids)
            )
            doc_by_cls: dict = {}
            status_by_cls: dict = {}  # [admission 게이트] 분류 status로 finalized 여부 판정
            for c in db.execute(cls_stmt).scalars():
                doc_by_cls[c.classification_id] = c.doc_id
                status_by_cls[c.classification_id] = c.status

            # 문서별: 최신 라벨(첫 등장 = corrected_at desc 정렬이라 최신) + 묶인 correction_id 전부
            doc_label: dict = {}
            doc_corr_ids: dict = {}
            skipped_bad_label = 0
            skipped_unadmitted = 0
            for corr in corrections:  # 이미 corrected_at desc
                doc_id = doc_by_cls.get(corr.classification_id)
                if doc_id is None:
                    continue  # 분류 없음
                # [admission 게이트] 무검수/머신 교정이 학습 라벨이 되는 것을 차단(죽음의 나선
                # 최약 고리). 사람 검수자 + finalized(confirmed/corrected)만 통과. 제외분은
                # 소비하지 않아(continue) 추후 확정되면 다음 tick에 재유입된다.
                if not _correction_admissible(
                    corr.corrected_by,
                    status_by_cls.get(corr.classification_id),
                    require_human_confirmed=require_human_confirmed,
                ):
                    skipped_unadmitted += 1
                    continue
                code = code_by_id.get(corr.corrected_level_id)
                if code not in label_set:
                    skipped_bad_label += 1
                    continue
                doc_corr_ids.setdefault(doc_id, []).append(corr.correction_id)
                if doc_id not in doc_label:  # 최신만 라벨로
                    doc_label[doc_id] = code

            rows: list[dict] = []
            row_doc_ids: list = []  # [#25] rows와 1:1 정렬 — 홀드아웃 doc_id 누수 차단용
            consumed_ids: list[int] = []
            skipped_no_text = 0
            for doc_id, label in doc_label.items():
                text = _reconstruct_text(db, doc_id, min_chars=min_chars)
                if text is None:
                    skipped_no_text += 1
                    continue  # 본문 없는 문서는 라벨을 날조하지 않음 + 소비도 안 함
                rows.append({"text": text, "label": label})
                row_doc_ids.append(doc_id)
                consumed_ids.extend(doc_corr_ids.get(doc_id, []))

            return RebuildResult(
                rows=rows,
                correction_ids=sorted(set(consumed_ids)),
                doc_ids=row_doc_ids,
                doc_count=len(rows),
                skipped_no_text=skipped_no_text,
                skipped_bad_label=skipped_bad_label,
                skipped_unadmitted=skipped_unadmitted,
                reason="ok" if rows else "no_rows_after_filter",
            )
    except SQLAlchemyError as exc:
        logger.debug("corrections rebuild skipped: %s", exc)
        return RebuildResult(reason=f"db_unavailable:{type(exc).__name__}")


# [#25] 홀드아웃 누수 차단 — 텍스트 정규화 키 (lloydk.hygiene 정본 위임).
# train↔holdout가 같은 문서를 서로 다른 공백/대소문자로 적어도 동일 본문으로 잡아 누수를 막는다.
# doc_id가 한쪽이라도 누락(None)이면 doc_id 매칭이 무력화되므로 텍스트 키가 안전망이다.
def _norm_text(text: object) -> str:
    return normalize_text(text, keep_spaces=True, lower=True)


def _load_holdout_keys(
    paths: Iterable[str | Path],
) -> tuple[set[str], set]:
    """홀드아웃(val/test) jsonl들에서 (정규화 텍스트 집합, doc_id 집합)을 읽는다.

    누락/깨진 파일·라인은 조용히 건너뛴다(홀드아웃이 없으면 빈 집합 → 차단 없음).
    doc_id 키 이름은 데이터 스키마(build_p1_holdout_split.py: "doc_id")를 따른다.
    """
    text_keys: set[str] = set()
    doc_ids: set = set()
    for p in paths:
        if not p:
            continue
        path = Path(p)
        if not path.exists():
            continue
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:  # 읽기 실패는 차단 불가 → 경고 후 무시(fail-open, 아래서 경고)
            logger.warning("holdout 읽기 실패(누수 차단 약화): %s (%s)", path, exc)
            continue
        for line in raw.splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            txt = row.get("text")
            if txt:
                text_keys.add(_norm_text(txt))
            did = row.get("doc_id")
            if did is not None:
                doc_ids.add(did)
    return text_keys, doc_ids


def _apply_grade_cap(
    rows: list[dict],
    doc_ids: list,
    *,
    per_grade_cap: int | None,
) -> tuple[list[dict], list]:
    """[#25-(2)] 등급별 cap/stratify — 보정이 특정 등급에 몰릴 때 train 분포 왜곡 완화.

    per_grade_cap=None(기본) → 캡 없음(동작 보존). 정수면 각 label당 최대 그 수만 남긴다
    (입력 순서 = 최신 교정 우선이므로 최신부터 채택). doc_ids는 rows와 정렬을 맞춰 함께 자른다.
    """
    if per_grade_cap is None or per_grade_cap <= 0:
        return rows, doc_ids
    kept_rows: list[dict] = []
    kept_dids: list = []
    seen: Counter = Counter()
    aligned_dids = doc_ids if len(doc_ids) == len(rows) else [None] * len(rows)
    for row, did in zip(rows, aligned_dids):
        label = row.get("label")
        if seen[label] >= per_grade_cap:
            continue
        seen[label] += 1
        kept_rows.append(row)
        kept_dids.append(did)
    return kept_rows, kept_dids


def merge_into_train_jsonl(
    base_train_path: str | Path | None,
    result: RebuildResult,
    out_path: str | Path,
    *,
    holdout_paths: Sequence[str | Path] | None = None,
    per_grade_cap: int | None = None,
) -> str | None:
    """기존 train.jsonl(base) + 교정 행을 합쳐 새 학습셋을 만든다(A2-① 반영).

    **train만** 증강한다 — val/test(홀드아웃)는 건드리지 않아 배포 게이트(deploy_gate)가
    교정 누출 없는 안정 홀드아웃에서 fnr_high를 측정하도록 보장한다(C-eval 정합).

    [#25] 단, "파일을 안 건드리는 것"만으로는 누수가 막히지 않는다 — 같은 doc_id/본문이
    holdout에도 있고 train에도 append되면 평가 누수다. 그래서 holdout_paths(val/test jsonl)를
    받아 그 (doc_id ∪ 정규화 텍스트) 집합과 겹치는 교정 행을 train append에서 **제외(enforce)**
    한다. 제외 건수는 로깅한다.
      - holdout_paths 미지정 → 기존처럼 동작하되 누수 차단 불가를 1회 경고(하위호환).
      - per_grade_cap: 선택적 등급별 상한(stratify). 기본 None=캡 없음(동작 보존).

    base가 없거나 비어 있으면 교정 행만으로 학습셋을 만든다(경고 수준 — 소량 학습 위험은
    호출부 책임). 교정 행(제외 후)도 없으면 None 반환(병합 불필요 → 호출부는 기본 경로 학습).
    """
    if not result.rows:
        return None

    # ── [#25] 등급별 cap/stratify(opt-in) — 누수 차단보다 먼저 적용해 캡 후 분포를 본다 ──
    rows, row_doc_ids = _apply_grade_cap(
        list(result.rows), list(result.doc_ids), per_grade_cap=per_grade_cap
    )

    # ── [#25] 홀드아웃 누수 차단(enforce): holdout과 겹치는 교정 행 제외 ──────────────
    if holdout_paths is None:
        # 하위호환: 미지정이면 기존 동작 유지. 단 누수 차단이 비활성임을 경고로 남긴다
        # (조용한 누수보다 시끄러운 경고가 안전).
        logger.warning(
            "merge_into_train_jsonl: holdout_paths 미지정 → train↔holdout 누수 차단 비활성. "
            "val/test 경로를 넘기면 중복 문서를 train append에서 제외한다.",
        )
        hold_texts: set[str] = set()
        hold_doc_ids: set = set()
    else:
        hold_texts, hold_doc_ids = _load_holdout_keys(holdout_paths)

    excluded = 0
    if hold_texts or hold_doc_ids:
        kept_rows: list[dict] = []
        aligned_dids = row_doc_ids if len(row_doc_ids) == len(rows) else [None] * len(rows)
        for row, did in zip(rows, aligned_dids):
            in_holdout = (did is not None and did in hold_doc_ids) or (
                _norm_text(row.get("text", "")) in hold_texts
            )
            if in_holdout:
                excluded += 1
                continue  # 홀드아웃과 겹치는 보정 문서는 train에 넣지 않는다(평가 누수 차단)
            kept_rows.append(row)
        rows = kept_rows

    # [#25] 제외 건수를 result에 기록(반환/조회 가능) + 로깅(반환·로깅 둘 다).
    try:
        result.excluded_holdout = excluded
    except Exception:  # noqa: BLE001 — result가 dataclass가 아니어도(mock 등) 머지는 진행
        pass
    if excluded:
        logger.warning(
            "[#25] holdout 누수 차단: 교정 %d행을 train append에서 제외(holdout 중복)", excluded
        )

    if not rows:
        # 캡/누수차단 후 남은 교정 행이 없으면 병합할 게 없다 → 호출부는 기본 경로 학습.
        logger.info(
            "merged train set: 제외 후 교정 행 0(excluded=%d) → 병합 생략(None)", excluded
        )
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
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    logger.info(
        "merged train set: base=%d + corrections=%d (excluded_holdout=%d) → %s",
        n_base, len(rows), excluded, out,
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
