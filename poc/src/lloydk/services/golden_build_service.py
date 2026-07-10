"""Golden build service — 통합 골든셋 빌더 비동기 잡 (G3b).

submit: 브로커 가용 시 Celery golden_build_task, 아니면 in-proc 동기 실행(폴백).
정본(classification_gold.jsonl)은 직접 변경하지 않고 run-스코프 후보 파일(build_<id>.jsonl,
uncertain_<id>.jsonl)에 쓴다. human_review 승격은 별개 경로(import_review_corrections,
지재원 관리자) — 두 검수 루프 분리 유지.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import tempfile
import uuid
from pathlib import Path
from typing import Callable, Optional

from lloydk.golden_builder import GoldenBuildResult, build_golden_set, make_label_fn
from lloydk.golden_review_html import render_review_html_from_jsonl
from lloydk.schemas.golden import (
    GoldenBuildRequest,
    GoldenBuildResponse,
    GoldenBuildStatus,
)
from lloydk.services.async_classify_service import _celery_dispatch_available
from lloydk.services.job_store import get_default_store

logger = logging.getLogger(__name__)

LabelFn = Callable[[str], "object"]

# ── 경로 샌드박스 (path traversal 차단) ──────────────────────────────────────
# corpus_dir/holdout_path/out_dir 은 요청 바디로 들어오는 파일시스템 경로다. 무검증이면
# "/etc/passwd" 읽기·"../../" 임의 위치 쓰기가 가능하다(인증된 admin/kl_backend 경로라도
# 최소권한 위반). 허용 루트(리포 datasets/ · 시스템 temp — pytest tmp_path 포함) 하위로만
# 제한한다. golden_build_service.py = poc/src/lloydk/services/... → parents[3] = poc 루트.
_POC_ROOT = Path(__file__).resolve().parents[3]
_ALLOWED_PATH_ROOTS = (
    (_POC_ROOT / "datasets").resolve(),
    Path(tempfile.gettempdir()).resolve(),
)


def _safe_path(raw: str | None) -> Path:
    """요청 경로를 허용 루트 하위로 제한. 벗어나면 ValueError(작업 자체를 거부)."""
    if not raw:
        raise ValueError("경로 미지정")
    p = Path(raw)
    resolved = (p if p.is_absolute() else _POC_ROOT / p).resolve()
    if not any(resolved.is_relative_to(base) for base in _ALLOWED_PATH_ROOTS):
        raise ValueError(
            f"허용 루트(datasets/ · temp) 밖 경로 거부: {raw}"
        )
    return resolved


class GoldenBuildService:
    def __init__(self):
        self.jobs = get_default_store()

    def submit(
        self,
        req: GoldenBuildRequest,
        *,
        label_fn: Optional[LabelFn] = None,
    ) -> GoldenBuildResponse:
        """빌드 잡 등록. label_fn 주입(테스트) 또는 브로커 미가용 시 in-proc 동기 실행."""
        job_id = uuid.uuid4()
        self.jobs.create(
            job_id,
            payload={
                # tenant 제거: 격리는 KL 포털 전담 — 골든 빌드 잡은 전역 네임스페이스.
                "kind": "golden_build",
                "source_type": req.source_type,
                "actor": req.actor.user_id,
                "submitted_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            },
        )
        # label_fn 주입 시엔 직렬화 불가하므로 항상 in-proc. 아니면 브로커 가용 시 Celery.
        if _celery_dispatch_available() and label_fn is None:
            try:
                from lloydk.workers.tasks import golden_build_task  # noqa: PLC0415

                golden_build_task.delay(req.model_dump(mode="json"), job_id=str(job_id))
                logger.info("golden_build enqueued to celery: job_id=%s", job_id)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "golden_build celery enqueue 실패 — in-proc 실행: job_id=%s",
                    job_id, exc_info=True,
                )
                self.run_build(req, job_id, label_fn=label_fn)
        else:
            self.run_build(req, job_id, label_fn=label_fn)

        return GoldenBuildResponse(golden_job_id=job_id, status_url=f"/golden/jobs/{job_id}")

    def run_build(
        self,
        req: GoldenBuildRequest,
        job_id: uuid.UUID,
        *,
        label_fn: Optional[LabelFn] = None,
    ) -> dict:
        """실제 빌드: 문서 로드 → build_golden_set → run-스코프 파일 출력 → JobStore 갱신."""
        self.jobs.update(job_id, status="running")
        try:
            docs = self._load_docs(req)
            holdout = self._load_holdout(req)
            lf = label_fn or make_label_fn(req.llm_provider, sensitive=req.sensitive)
            result = build_golden_set(
                docs,
                label_fn=lf,
                holdout_texts=holdout,
                min_self_consistency=req.min_self_consistency,
                require_evidence=req.require_evidence,
            )
            gold_path, unc_path = self._write_outputs(req, job_id, result)
            self.jobs.update(
                job_id,
                status="done",
                stats=result.stats,
                gold_count=len(result.gold),
                uncertain_count=len(result.uncertain),
                gold_path=str(gold_path),
                uncertain_path=str(unc_path),
            )
            logger.info(
                "golden_build done: job_id=%s gold=%d uncertain=%d dup=%d leaked=%d",
                job_id, len(result.gold), len(result.uncertain),
                result.dropped_duplicate, result.dropped_leaked,
            )
            return result.stats
        except Exception as exc:  # noqa: BLE001
            self.jobs.update(job_id, status="failed", error=f"{type(exc).__name__}: {exc}")
            logger.warning("golden_build 실패: job_id=%s err=%s", job_id, exc, exc_info=True)
            raise

    def get_status(self, job_id: uuid.UUID) -> Optional[GoldenBuildStatus]:
        job = self.jobs.get(job_id)
        if job is None:
            return None
        return GoldenBuildStatus(
            status=job.get("status", "queued"),
            stats=job.get("stats"),
            gold_count=job.get("gold_count"),
            uncertain_count=job.get("uncertain_count"),
            gold_path=job.get("gold_path"),
            uncertain_path=job.get("uncertain_path"),
            error=job.get("error"),
        )

    def render_review(self, job_id: uuid.UUID, *, title: str = "골든셋 후보 검토본") -> Optional[str]:
        """완료된 빌드 잡의 후보(build·uncertain)를 지재원 관리자 검수용 HTML로 렌더.

        JobStore에서 잡의 출력 경로를 찾아 render_review_html_from_jsonl로 렌더.
        잡이 없거나 출력 경로가 없으면 None. (지재원 관리자가 URL로 바로 검수)
        """
        job = self.jobs.get(job_id)
        if job is None:
            return None
        paths = [p for p in (job.get("gold_path"), job.get("uncertain_path")) if p]
        if not paths:
            return None
        return render_review_html_from_jsonl(paths, title=title, subtitle=f"job {job_id}")

    # ------------------------------------------------------------------ helpers
    def _load_docs(self, req: GoldenBuildRequest) -> list[dict]:
        if req.source_type == "inline":
            return list(req.docs)
        # corpus: jsonl 파일 또는 *.json 디렉토리 (허용 루트 하위로 제한)
        p = _safe_path(req.corpus_dir)
        if not p.exists():
            raise FileNotFoundError(f"corpus_dir 없음: {p}")
        rows: list[dict] = []
        if p.is_file():
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    rows.append(json.loads(line))
        else:
            for f in sorted(p.glob("*.json")):
                rows.append(json.loads(f.read_text(encoding="utf-8")))
        out: list[dict] = []
        for d in rows[: req.n]:
            text = d.get("text") or f"{d.get('title', '')}\n\n{d.get('body', '')}".strip()
            out.append({
                "doc_id": d.get("doc_id"),
                "text": text,
                "source": d.get("source", ""),
                "domain": d.get("domain", ""),
            })
        return out

    def _load_holdout(self, req: GoldenBuildRequest) -> Optional[list[str]]:
        if not req.holdout_path:
            return None
        p = _safe_path(req.holdout_path)
        if not p.exists():
            return None
        texts: list[str] = []
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                t = json.loads(line).get("text")
                if t:
                    texts.append(t)
        return texts

    def _write_outputs(
        self, req: GoldenBuildRequest, job_id: uuid.UUID, result: GoldenBuildResult,
    ) -> tuple[Path, Path]:
        out = _safe_path(req.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        gold_path = out / f"build_{job_id}.jsonl"
        unc_path = out / f"uncertain_{job_id}.jsonl"
        _write_jsonl(gold_path, [r.to_dict() for r in result.gold])
        _write_jsonl(unc_path, [r.to_dict() for r in result.uncertain])
        return gold_path, unc_path


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
