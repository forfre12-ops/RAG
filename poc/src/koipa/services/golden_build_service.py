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

from koipa.golden_builder import GoldenBuildResult, build_golden_set, make_label_fn
from koipa.golden_review_html import (
    render_review_html_from_jsonl,
    render_signoff_html_from_jsonl,
)
from koipa.golden_signoff import Signoff, merge_locked_records, promote_to_locked
from koipa.golden_tiers import (
    REJECTED_BY_REVIEWER,
    eval_readiness,
    human_reviewer_rejection_reason,
)
from koipa.schemas.golden import (
    GoldenBuildRequest,
    GoldenBuildResponse,
    GoldenBuildStatus,
    GoldenSignoffDecision,
)
from koipa.services.async_classify_service import _celery_dispatch_available
from koipa.services.job_store import get_default_store

logger = logging.getLogger(__name__)

LabelFn = Callable[[str], "object"]

# ── 경로 샌드박스 (path traversal 차단) ──────────────────────────────────────
# corpus_dir/holdout_path/out_dir 은 요청 바디로 들어오는 파일시스템 경로다. 무검증이면
# "/etc/passwd" 읽기·"../../" 임의 위치 쓰기가 가능하다(인증된 admin/kl_backend 경로라도
# 최소권한 위반). 허용 루트(리포 datasets/ · 시스템 temp — pytest tmp_path 포함) 하위로만
# 제한한다. golden_build_service.py = poc/src/koipa/services/... → parents[3] = poc 루트.
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
                from koipa.workers.tasks import golden_build_task  # noqa: PLC0415

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

    def register_build(
        self, build_path: str, *, actor_user_id: str
    ) -> "Optional[uuid.UUID]":
        """기존 build_*.jsonl(gold 후보)을 재라벨링 없이 'done' 골든 잡으로 등록.

        /golden/build 는 LLM 재라벨링을 하므로 큐레이트 슬레이트(실서명 스프린트 등)의 라벨이
        바뀐다. 이 경로는 파일을 그대로 잡의 gold_path 로 올려 signoff.html/POST signoff 가 그
        후보를 검수 화면에 연결하게 한다. 경로는 datasets/ 하위로 샌드박스(_safe_path). 파일
        없음/샌드박스 밖이면 None(호출부 404). LLM·게이트 미실행 — 순수 등록.
        """
        try:
            p = _safe_path(build_path)
        except ValueError:
            return None
        if not p.exists() or not p.is_file():
            return None
        from collections import Counter  # noqa: PLC0415

        rows = _read_jsonl(p)
        by_grade = Counter(r.get("label") for r in rows if r.get("label"))
        job_id = uuid.uuid4()
        self.jobs.create(
            job_id,
            payload={
                "kind": "golden_register",
                "actor": actor_user_id,
                "submitted_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            },
        )
        self.jobs.update(
            job_id,
            status="done",
            gold_path=str(p),
            gold_count=len(rows),
            uncertain_count=0,
            stats={"gold_by_grade": dict(by_grade), "source": "register"},
        )
        return job_id

    def corpus_summary(self, corpus_path: str) -> "Optional[dict]":
        """정본 골든셋의 구성 집계 — 읽기 전용.

        잡 목록(/golden/jobs)은 '무엇을 만들었나'만 보여줄 뿐, 골든셋이 지금 어떤 tier·등급·
        출처로 구성돼 있는지는 화면에서 답할 수가 없었다. 감리에서 "평가 정답이 어떻게
        만들어졌나"를 물으면 그 답이 여기다.

        tier 는 저장 컬럼이 아니라 label_source/review_status/서명 envelope 에서 파생되므로
        (golden_tiers 모듈 계약) 여기서도 같은 함수로 유도한다 — 별도 집계를 두면 드리프트한다.
        경로는 _safe_path 로 datasets/ 하위 샌드박스. 파일 없음/밖이면 None(호출부 404).
        """
        from collections import Counter  # noqa: PLC0415

        from koipa.golden_tiers import (  # noqa: PLC0415
            document_origin,
            is_real_locked_eval,
            tier_of,
        )

        try:
            p = _safe_path(corpus_path)
        except ValueError:
            return None
        if not p.exists() or not p.is_file():
            return None

        rows = _read_jsonl(p)
        by_tier: Counter[str] = Counter()
        by_grade: Counter[str] = Counter()
        by_origin: Counter[str] = Counter()
        by_label_source: Counter[str] = Counter()
        tier_grade: dict[str, Counter] = {}
        real_locked = 0

        for r in rows:
            t = tier_of(r)
            g = str(r.get("label") or "?")
            by_tier[t] += 1
            by_grade[g] += 1
            by_origin[document_origin(r)] += 1
            by_label_source[str(r.get("label_source") or "(없음)")] += 1
            tier_grade.setdefault(t, Counter())[g] += 1
            if is_real_locked_eval(r):
                real_locked += 1

        return {
            "path": str(p.relative_to(_POC_ROOT)) if p.is_relative_to(_POC_ROOT) else str(p),
            "total": len(rows),
            "by_tier": dict(by_tier),
            "by_grade": dict(by_grade),
            "by_origin": dict(by_origin),
            "by_label_source": dict(by_label_source),
            "tier_by_grade": {k: dict(v) for k, v in tier_grade.items()},
            # locked 중에서도 '실문서 출처'까지 갖춘 것만 real 평가정답으로 집계된다
            # (합성 본문에 서명이 붙어도 일반화 근거가 되지 못한다 — golden_tiers 계약).
            "real_locked_eval": real_locked,
        }

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
            self._dropped_no_text = 0
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
            # 본문 없어 제외된 입력을 stats 에 노출 — 무음 소멸 방지(입력 수가 안 맞는 이유가 보이게).
            if self._dropped_no_text:
                result.stats["dropped_no_text"] = self._dropped_no_text
            # 도메인→등급 shortcut 누출 가시화(플래그+로그 전용, 하드-드롭 없음, 실패-안전).
            # gold(=학습 silver 후보)가 '도메인이 등급을 결정'하는 shortcut을 얼마나 담는지
            # stats에 verdict로 남겨 승격/검수 판단에 노출한다(무음 통과 방지).
            leak = self._domain_leakage_verdict(result.gold)
            if leak is not None:
                result.stats["domain_leakage_gate"] = leak
                if leak.get("blocked"):
                    logger.warning(
                        "golden_build 누출경고: job_id=%s 도메인->등급 shortcut 의심(%s) "
                        "— 플래그+로그만(드롭 안 함). 승격/검수 시 주의.",
                        job_id, leak.get("reason"),
                    )
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

    def signoff_preflight(
        self, job_id: uuid.UUID, *, reviewer_id: str = "", publish: bool = False
    ) -> dict | None:
        """서명하기 **전에** 무엇이 막을지 알려준다. 부작용 0.

        왜 필요한가 — 종전에는 실패가 **제출한 뒤에야** 드러났다. 검수자가 120건을 다 고르고
        제출을 눌러야 "이 이름으로는 서명할 수 없습니다" 나 "publish 경로가 없습니다" 를
        봤다. 결정이 화면에 남으니 다시 하지는 않지만, 그 시점에 알 이유가 없다.

        blocking 은 **실제로 POST 를 실패시키는 것만** 담는다. 경고를 blocking 에 섞으면
        제출 버튼이 근거 없이 잠긴다.

        ⚠ 서버는 브라우저의 미제출 결정을 볼 수 없다. 여기 담는 것은 '서버가 아는 상태'
          뿐이고, 화면이 자기 미결정 수를 더해 보여준다.
        """
        st = self.get_status(job_id)
        if st is None:
            return None

        blocking: list[dict] = []
        warnings: list[dict] = []

        status = getattr(st, "status", None)
        if status != "done":
            blocking.append({
                "code": "job_not_done",
                "message": f"골든 빌드가 아직 끝나지 않았습니다(status={status}).",
                "detail": getattr(st, "error", None) or "",
            })

        job = self.jobs.get(job_id) or {}
        gold_path = job.get("gold_path") or ""
        candidates: list[dict] = []
        if not gold_path or not Path(gold_path).exists():
            blocking.append({
                "code": "gold_path_missing",
                "message": "서명할 후보 파일이 없습니다. 잡을 다시 등록해야 합니다.",
                "detail": gold_path,
            })
        else:
            candidates = _read_jsonl(Path(gold_path))
            if not candidates:
                blocking.append({
                    "code": "no_candidates",
                    "message": "후보가 0건입니다 — 서명할 대상이 없습니다.",
                    "detail": gold_path,
                })

        # 이미 처리된 것 — 남은 건수를 정확히 내려면 '거부' 도 빼야 한다(B3 에서 남기기 시작).
        base = Path(gold_path).parent if gold_path else None
        locked_ids = {
            r.get("doc_id")
            for r in (_read_jsonl(base / f"locked_{job_id}.jsonl") if base else [])
        }
        rejected_ids = {
            r.get("doc_id")
            for r in (_read_jsonl(base / f"rejected_{job_id}.jsonl") if base else [])
        }
        done_ids = locked_ids | rejected_ids
        remaining = sum(1 for c in candidates if c.get("doc_id") not in done_ids)

        # 서명자 — 오늘부터 신원은 로그인 쿠키(JWT sub)에서 온다. '화면에 미리 채워진 이름'
        # 이 아니라 **실제로 서명할 사람**을 검사한다.
        reject_reason = human_reviewer_rejection_reason(reviewer_id) if reviewer_id else ""
        if reviewer_id and reject_reason:
            blocking.append({
                "code": "reviewer_rejected",
                "message": "이 계정으로는 서명할 수 없습니다.",
                "detail": reject_reason,
            })

        from koipa.config import settings  # noqa: PLC0415
        live_path = str(getattr(settings, "locked_eval_jsonl", "") or "")
        if publish and not live_path:
            warnings.append({
                "code": "publish_path_missing",
                "message": "라이브 반영을 요청해도 반영되지 않습니다.",
                "detail": "LOCKED_EVAL_JSONL 미설정 — 배포 게이트가 읽는 경로가 없습니다.",
            })
        if remaining == 0 and candidates:
            warnings.append({
                "code": "nothing_remaining",
                "message": "이 잡에서 아직 판단하지 않은 후보가 없습니다.",
                "detail": f"승격 {len(locked_ids)}건 · 거부 {len(rejected_ids)}건",
            })

        existing_live = (
            _read_jsonl(Path(live_path)) if live_path and Path(live_path).exists() else []
        )
        return {
            "job_id": str(job_id),
            "ok": not blocking,
            "blocking": blocking,
            "warnings": warnings,
            "candidates": {
                "total": len(candidates),
                "already_locked": len(locked_ids),
                "already_rejected": len(rejected_ids),
                "remaining": remaining,
            },
            "reviewer": {
                "reviewer_id": reviewer_id,
                "will_be_rejected": bool(reject_reason),
                "reason": reject_reason,
            },
            "publish": {"live_path": live_path, "configured": bool(live_path)},
            "readiness": eval_readiness(existing_live),
        }

    def render_review(
        self, job_id: uuid.UUID, *, title: str = "골든셋 후보 검토본", signoff_url: str = ""
    ) -> Optional[str]:
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
        from koipa.config import settings  # noqa: PLC0415 — 모듈 상단 미임포트(지연 로드 관례)
        return render_review_html_from_jsonl(
            paths, title=title, subtitle=f"job {job_id}",
            profile=getattr(settings, "deploy_profile", None),   # nav 배포 주체 배지
            # 서명된 형제 주소는 **API 층이 계산해서 넘긴다** — ?t= 비밀키를 아는 곳이 거기다.
            signoff_url=signoff_url,
        )

    def render_signoff(
        self, job_id: uuid.UUID, *, title: str = "골든셋 검수 · 서명", review_url: str = ""
    ) -> Optional[str]:
        """빌드 잡의 gold 후보(gold_candidate)를 화면 서명용 인터랙티브 HTML로 렌더.

        render_review(보기 전용)와 달리 각 후보에 승인/등급변경/거부 폼을 붙이고, 제출 시
        POST /golden/jobs/{id}/signoff 로 결정을 보낸다. gold 후보만 대상(uncertain 은
        아직 승격 후보가 아님). 잡/경로 없으면 None.
        """
        job = self.jobs.get(job_id)
        if job is None:
            return None
        gold_path = job.get("gold_path")
        if not gold_path:
            return None
        # [통합 2026-08-18] 합의 미달(uncertain) 후보도 함께 읽는다. 종전에는 이걸 보려고
        # 검토본(review.html)이라는 화면이 따로 있었다. 서명 대상이 아니므로 렌더러가
        # 결정 폼 없이 보기 전용으로 그린다.
        paths = [p for p in (gold_path, job.get("uncertain_path")) if p]
        from koipa.config import settings  # noqa: PLC0415
        return render_signoff_html_from_jsonl(
            paths,
            job_id=str(job_id),
            post_url=f"/api/v1/golden/jobs/{job_id}/signoff",
            title=title,
            profile=getattr(settings, "deploy_profile", None),   # nav 배포 주체 배지
            review_url=review_url,
            # 화면이 '등급별 몇 건 남았나' 를 정확히 말하려면 게이트와 같은 값을 써야 한다.
            min_per_grade=int(getattr(settings, "deploy_gate_min_locked_per_grade", 5) or 0),
        )

    # 신원 관련 인자가 이 호출에서 사라진 경위 — 지우면 같은 실수가 되돌아온다.
    #
    # [C1 2026-08-17] 기본 검수자 주입을 끊었다. 설정값(signoff_default_reviewer)은 지우지
    #   않는다 — golden_tiers.is_human_reviewer 가 그 값과 같은 이름을 거부하는 데 쓰기
    #   때문이다(DEF-2026-53). 설정을 비우면 `if default_rid and ...` 의 앞 조건이 거짓이
    #   되어 거부 자체가 사라진다.
    #
    #   실측(223, 2026-08-17): SIGNOFF_DEFAULT_REVIEWER=hong.gildong 이 켜져 있고
    #   locked_gold_eval 20건이 전원 그 이름 · 19건이 같은 마이크로초(…T14:42:14.904855)
    #   서명이었다. 화면이 채워 준 이름을 그대로 제출한 결과다 — 개별 검수 행위가 아니다.
    #
    # [C2 2026-08-17] 이어서 입력칸 자체를 없앴다. 이름을 손으로 적을 수 있는 한 원장에
    #   남는 서명자는 자칭이다 — auth_mode=both 에서 공유 API Key 가 먼저 통과하면
    #   resolve_actor_user_id 가 클라이언트 값을 그대로 돌려주기 때문이다(confirm.py:53).
    #   이제 신원은 로그인 쿠키(JWT sub)에서만 온다.
    #
    #   공유 키 프리필(signoff_prefill_api_key)도 함께 제거했다. 켜져 있으면 공유 관리자
    #   키가 서명 페이지 HTML 본문에 박혀 나간다 — 223 의 .env.jjw:27 에 켜져 있었다.

    def apply_signoff(
        self,
        job_id: uuid.UUID,
        decisions: "list[GoldenSignoffDecision]",
        *,
        reviewer_id: str,
        publish: bool = False,
        dry_run: bool = False,
    ) -> Optional[dict]:
        """검수 결정(승인/변경/거부)을 골든 후보에 적용해 locked_gold_eval 로 승격.

        골든셋 검수의 서명 캡처 단계 — golden_signoff.promote_to_locked 계약 그대로.
        후보는 잡의 gold_path(build_<id>.jsonl)에서 로드한다. 정본(classification_gold.jsonl)은
        건드리지 않는다. run-스코프 locked_<id>.jsonl 은 항상 기록(감사), 라이브 readiness
        읽기경로(settings.locked_eval_jsonl)는 publish=True 일 때만 dedup 병합(배포 게이트 소비).

        reviewer_id 는 호출부(엔드포인트)가 인증 신원으로 확정해 넘긴다(위조 차단). 머신
        reviewer 는 promote_to_locked 내부 is_human_reviewer 가 거부(전건 machine_reviewer).

        반환: {locked, rejected, locked_by_grade, rejected_reasons, readiness, published,
        run_locked_path}. 잡/경로 없으면 None.
        """
        job = self.jobs.get(job_id)
        if job is None:
            return None
        gold_path = job.get("gold_path")
        if not gold_path or not Path(gold_path).exists():
            return None

        candidates = _read_jsonl(Path(gold_path))
        by_doc = {c.get("doc_id"): c for c in candidates}

        signed_at = dt.datetime.now(dt.timezone.utc).isoformat()
        signoffs: list[Signoff] = []
        rejected_records: list[dict] = []
        decided_ids: set = set()
        for d in decisions:
            cand = by_doc.get(d.doc_id)
            if cand is None:
                continue  # 후보에 없는 doc_id 는 서명 불가(팬텀 방지) — 무시
            decided_ids.add(d.doc_id)
            if d.decision == "reject":
                # [B3·E2-6] 거부는 서명을 만들지 않지만 **기록은 남긴다.**
                # 종전에는 아무 파일에도 남지 않아 (a) 다음 세션에서 '미결정'과 구분되지
                # 않았고 (b) 그 후보가 계속 학습에 쓰였다.
                rejected_records.append({
                    **cand,
                    "review_status": REJECTED_BY_REVIEWER,
                    "signoff_rejected": {
                        "reviewer_id": reviewer_id,
                        "rejected_at": signed_at,
                        "note": d.note or "",
                    },
                })
                continue
            if d.decision == "change":
                if not d.grade:
                    continue  # 변경인데 등급 미지정 — 방어적 skip(스키마가 1차 방지)
                grade = d.grade
            else:  # approve — 제안등급(후보 label) 유지
                grade = cand.get("label")
            signoffs.append(
                Signoff(
                    doc_id=d.doc_id, reviewer_id=reviewer_id, grade=grade,
                    signed_at=signed_at, note=d.note,
                )
            )

        # 결정된 후보만 승격 대상으로(미결정 후보가 rejected 로 잡히는 노이즈 차단).
        subset = [c for c in candidates if c.get("doc_id") in decided_ids]
        res = promote_to_locked(subset, signoffs)

        # run-스코프 감사 기록(정본·라이브 무변경) — 항상. 같은 잡을 여러 세션에 나눠 서명하는 게
        # 정상 흐름(대량 후보)이므로 덮어쓰기 대신 기존 승격분과 doc_id dedup 누적(라이브 경로와 동일
        # 규율) — 세션1 서명이 세션2 제출로 유실되지 않게.
        # [E2-5] dry_run 은 **쓰기만** 건너뛴다. 판정·집계는 그대로 돌려, 검수자가 실제로
        # 고른 결정이 무엇을 만들지 제출 전에 같은 응답 모양으로 볼 수 있게 한다.
        run_locked_path = Path(gold_path).parent / f"locked_{job_id}.jsonl"
        if not dry_run:
            _atomic_write_jsonl(
                run_locked_path, merge_locked_records(_read_jsonl(run_locked_path), res.locked)
            )

        # [B3·E2-6] 거부분도 같은 누적 규율로 남긴다. 이게 없으면 다음 세션에서 '아직 안 본
        # 것'과 '보고 거부한 것'을 구분할 수 없어 남은 건수가 항상 과대값이 된다.
        run_rejected_path = Path(gold_path).parent / f"rejected_{job_id}.jsonl"
        locked_ids = {r.get("doc_id") for r in res.locked}
        rejected_merged = _merge_rejected_records(
            _read_jsonl(run_rejected_path), rejected_records, locked_ids
        )
        if not dry_run and (rejected_merged or run_rejected_path.exists()):
            _atomic_write_jsonl(run_rejected_path, rejected_merged)

        # 라이브 readiness 읽기경로 — publish 병합 대상 + readiness union 계산.
        from koipa.config import settings  # noqa: PLC0415

        live_path = getattr(settings, "locked_eval_jsonl", "") or ""
        existing_live = (
            _read_jsonl(Path(live_path))
            if live_path and Path(live_path).exists()
            else []
        )
        merged = merge_locked_records(existing_live, res.locked)

        published = False
        publish_note = None
        if publish:
            # publish 요청이 라이브 경로에 반영되지 못하는 두 경우를 조용한 no-op 대신 명시(리뷰 ⑥).
            if not res.locked:
                publish_note = "publish 요청됐으나 승격 locked 0건 — 반영 대상 없음"
            elif not live_path:
                publish_note = (
                    "publish 요청됐으나 LOCKED_EVAL_JSONL 미설정 — 배포 게이트 미반영. "
                    "프로파일 env 에 locked_eval_jsonl 경로를 설정하세요"
                )
            elif dry_run:
                publish_note = "dry_run — 라이브 경로에 쓰지 않았습니다(미리보기)"
            else:
                _atomic_write_jsonl(Path(live_path), merged)
                published = True

        return {
            "locked": len(res.locked),
            "rejected": len(res.rejected),
            "locked_by_grade": res.stats.get("locked_by_grade", {}),
            "rejected_reasons": res.stats.get("rejected_reasons", {}),
            # published=False 면 '라이브+이번 승격' 미리보기(라이브는 실제 무변경).
            "readiness": eval_readiness(merged),
            "published": published,
            "publish_note": publish_note,
            "run_locked_path": str(run_locked_path),
            # 이번 제출까지 누적된 거부 건수 — 화면이 '남은 건수' 를 정확히 낼 수 있게.
            "rejected_recorded": len(rejected_merged),
            "run_rejected_path": str(run_rejected_path),
            "dry_run": dry_run,
        }

    # ------------------------------------------------------------------ helpers
    def _load_docs(self, req: GoldenBuildRequest) -> list[dict]:
        if req.source_type == "inline":
            # 본문 키 정규화 + 빈 본문 제거. 종전에는 req.docs 를 그대로 넘겨, 빌더가 읽는
            # `text` 대신 `content`/`body` 로 보낸 문서가 **아무 카운터에도 안 잡히고 조용히
            # 사라졌다**(입력 2 → gold 0·uncertain 0·dropped 0). 어느 단계에서 없어졌는지
            # 화면으로 알 길이 없어 "게이트 전량 탈락"으로 오독된다(2026-08-02 실측).
            out: list[dict] = []
            self._dropped_no_text = 0
            for d in req.docs:
                text = str(
                    d.get("text") or d.get("content") or d.get("body") or ""
                ).strip()
                if not text:
                    self._dropped_no_text += 1
                    continue
                out.append({**d, "text": text})
            if self._dropped_no_text:
                logger.warning(
                    "golden_build: 본문 없는 inline 문서 %d건 제외(text/content/body 전부 빈 값)",
                    self._dropped_no_text,
                )
            return out
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

    def _domain_leakage_verdict(self, gold_records) -> "Optional[dict]":
        """도메인->등급 shortcut 누출 판정(순수·실패-안전). verdict dict 또는 None.

        check_domain_leakage_gate.domain_leakage_verdict + analyze_golden_run.domain_leakage
        (기존 CLI/빌드 게이트와 **동일 출처**)를 런타임 import로 재사용한다 — 지표 로직을
        서비스에 복제하지 않아 임계·정의 divergence를 방지. text 중복 dedup(dropped_leaked)과는
        별개 축(도메인→등급 shortcut)이다. 계산 실패는 빌드를 막지 않고 warning으로만 남긴다.
        """
        try:
            rows = [r.to_dict() if hasattr(r, "to_dict") else r for r in gold_records]
            if not rows:
                return None
            import sys  # noqa: PLC0415
            scripts_dir = _POC_ROOT / "scripts"
            if str(scripts_dir) not in sys.path:
                sys.path.insert(0, str(scripts_dir))
            from analyze_golden_run import domain_leakage  # noqa: PLC0415
            from check_domain_leakage_gate import domain_leakage_verdict  # noqa: PLC0415

            return domain_leakage_verdict(domain_leakage(rows))
        except Exception:  # noqa: BLE001
            logger.warning(
                "domain_leakage 게이트 계산 실패 — 스킵(빌드 계속)", exc_info=True
            )
            return None

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


def _merge_rejected_records(
    existing: list[dict], new: list[dict], locked_ids: set
) -> list[dict]:
    """거부 기록 누적 — doc_id 기준 최신 우선, 승격된 것은 뺀다.

    ⚠ 마지막 조건이 핵심이다. 검수자가 거부했다가 마음을 바꿔 승인하면, 거부 기록이 남아
      있는 한 그 문서는 tier_of 에서 영원히 held 다. 승격 목록에 있는 doc_id 는 거부에서
      지운다 — 최신 판단이 이긴다.
    """
    merged: dict[str, dict] = {}
    for r in list(existing) + list(new):
        doc_id = r.get("doc_id")
        if doc_id:
            merged[doc_id] = r
    return [r for doc_id, r in sorted(merged.items()) if doc_id not in locked_ids]


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(ln)
        for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]


def _atomic_write_jsonl(path: Path, rows: list[dict]) -> None:
    """원자적 jsonl 기록(tmp→replace) — 라이브 readiness 읽기경로가 부분기록으로 깨지지 않게.

    배포 게이트/readiness 게이지가 이 파일을 읽으므로 (run_golden_split_signoff._write_jsonl
    과 동일 규율) 원자적 교체로 반쪽 상태 노출을 막는다.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    tmp.replace(path)
