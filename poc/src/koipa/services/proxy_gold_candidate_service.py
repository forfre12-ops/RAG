"""Synthetic proxy-gold candidate inventory and administrative decision ledger.

This service deliberately keeps curated synthetic candidates separate from the
real-document golden corpus.  An administrative approval produces only
``approved_proxy``; the decision ledger never becomes an evaluation record on
its own.  Promotion to ``locked_gold_eval`` is a separate, explicit step —
``promote_decisions_to_locked`` projects the ledger through
``golden_signoff.promote_to_locked``; see ``koipa.console_signoff``.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import logging
import os
import re
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4


logger = logging.getLogger(__name__)

_POC_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_ROOT = _POC_ROOT / "datasets" / "proxy_gold" / "single_document_candidates"
_LEDGER_NAME = "candidate_decisions.jsonl"
# 콘솔 결정에서 승격된 사람 서명 평가정답. 후보·원장과 같은 폴더에 둔다 — 잡 단위로
# 갈리는 locked_<job_id>.jsonl 과 달리 콘솔 후보 풀은 하나뿐이라 파일도 하나다.
_LOCKED_LEDGER_NAME = "locked_console_review.jsonl"

# 후보 목록 캐시 — 키에 (루트·파일수·최신 mtime·전체 바이트·원장 mtime·원장 바이트) 가
# 들어 있어 파일이 하나라도 바뀌면 자동 무효화된다. 캐시가 없으면 매 요청 30MB 본문을
# 다시 읽고 해시한다. 크기를 함께 보는 이유는 _scan() 주석에 있다 — mtime 만으로는
# 파일시스템 시계 해상도(223 실측 4ms) 안에서 일어난 두 번째 기록을 놓친다.
_CANDIDATE_CACHE: dict[tuple, list[dict[str, Any]]] = {}
_VALID_GRADES = {"TS", "S1", "S2", "S3"}
# [B2 2026-08-18] exclude = '검수 대상 아님'. deferred(나중에 볼 것)·discarded(폐기)와
# 다른 제3의 종결이다 — 등급을 정하지도, 문서를 버리지도 않고 이번 검수 범위에서만 뺀다.
# ⚠ 이 상태는 학습에 대해 아무 말도 하지 않는다. 콘솔 status 를 읽는 학습 경로가 아직
#   없기 때문이다(B3). 화면 문구에서 '학습 제외' 라고 쓰면 근거 없는 주장이 된다.
_VALID_ACTIONS = {"approve", "change", "defer", "reject", "discard", "reopen", "exclude"}
# 본문에 등급 문자열이 그대로 남아 있으면 검수자가 읽기 전에 답을 본다.
#
# [2026-09-05] **출처별로 어휘가 다르다.** 종전에는 이 좁은 정규식 하나였는데, 합성
# 생성물은 한국어로 등급을 말한다("본 문서는 1급 비밀로 분류된 자료입니다"). 실측
# rag_corpus_v2 720건에서 이 식이 잡는 것은 192건뿐이고 실제로는 569건이 등급을 말한다.
#
# 그런데 **실문서에서는 넓히면 안 된다.** 실문서에 찍힌 "대외비"는 검수자가 봐야 하는
# 문서의 일부이고 비밀관리성(M) 판단의 근거다(rule_engine._MANAGEMENT_MARKING_TERMS 가
# 점수로 쓴다). 생성기가 지어낸 것과 원본에 찍혀 있던 것은 성격이 다르다.
#
#   합성 후보  generator.FORBIDDEN_GRADE_TERMS 전부 — 프롬프트가 금지한 것을 어겼다는 뜻
#   실문서     등급 코드(TS·S1·S2·S3)만 — 문서에 그 코드가 있으면 라벨이 새어 든 것이다
_GRADE_TOKEN = re.compile(r"\b(TS|S1|S2|S3)\b")


def _exposes_grade(text: str, *, is_real: bool) -> bool:
    """검수자가 읽기 전에 답을 보게 되는가 — 출처에 따라 어휘를 달리한다."""
    if is_real:
        # 전각 표기(ＴＳ·Ｓ１)도 접어서 본다 — 한국 공문서·구형 한글 문서에 섞인다.
        # 검사용 접기일 뿐 본문은 그대로다(synth_quality._fold_for_match 와 같은 규약).
        from koipa.services.synth_quality import _fold_for_match  # noqa: PLC0415

        return bool(_GRADE_TOKEN.search(_fold_for_match(text)))
    from koipa.services.synth_quality import _exposes_grade_token  # noqa: PLC0415

    return _exposes_grade_token(text)
_DOCUMENT_ORIGINS = {"uploaded_document", "public_real", "organization_real"}


@contextlib.contextmanager
def _exclusive_ledger_lock(lock_path: Path) -> Iterator[None]:
    """Cross-process advisory lock for the small append-only decision ledger."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "a+b")
    try:
        handle.seek(0)
        handle.write(b"0")
        handle.flush()
        if os.name == "nt":
            import msvcrt  # type: ignore[attr-defined]
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            if os.name != "nt":
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            # Windows releases msvcrt byte-range locks on handle close.  Explicit
            # LK_UNLCK has proved unreliable after buffered writes on Windows.
        finally:
            handle.close()


def _merged_provenance(meta: dict) -> dict:
    """metadata 의 출처 정보를 한 모양으로 합친다.

    provenance dict 가 정본이다. 없거나 비어 있으면 top-level 의 옛 키를 끌어올린다.
    끌어올린 것은 `origin="legacy_top_level"` 로 표시해, 어디서 온 값인지 화면·감사에서
    구분할 수 있게 한다. **status 는 함부로 'recorded' 로 올리지 않는다** - 사용 권한
    근거(authorization_basis)가 없으면 출처만 있는 상태이기 때문이다.
    """
    prov = dict(meta.get("provenance") or {})
    if prov.get("status") == "recorded":
        return prov
    legacy_src = str(meta.get("source_reference") or "").strip()
    legacy_basis = str(meta.get("authorization_basis") or "").strip()
    if not (legacy_src or legacy_basis):
        return prov
    prov.setdefault("source_reference", legacy_src)
    if legacy_basis:
        prov.setdefault("authorization_basis", legacy_basis)
    prov.setdefault("origin", "legacy_top_level")
    # 둘 다 있어야 기록으로 친다 - 출처만 있고 권한 근거가 없으면 미완이다.
    if prov.get("source_reference") and prov.get("authorization_basis"):
        prov.setdefault("status", "recorded")
    else:
        prov.setdefault("status", "partial")
    return prov


def _management_view(meta: dict) -> dict[str, Any]:
    """저장된 M 입력 + 그것이 만드는 상태를 한 모양으로. 판정은 rule_engine 이 유일 기준."""
    from koipa.modules.m3_labeling.rule_engine import (  # noqa: PLC0415
        management_from_metadata,
    )
    mgmt = dict(meta.get("management") or {})
    marking = mgmt.get("security_marking")
    scope = mgmt.get("access_scope")
    state, level, reason = management_from_metadata(marking, scope)
    return {
        "security_marking": marking,
        "access_scope": scope,
        "state": state,        # present | proven_absent | unknown
        "level": level,        # 2 | 1 | 0 | None
        "reason": reason,
        "recorded_by": mgmt.get("recorded_by"),
        "recorded_at": mgmt.get("recorded_at"),
    }


PROVENANCE_RECORDED = "recorded"


def _provenance_status(is_actual_intake: bool, source_reference: str, authorization_basis: str) -> str:
    """출처 기록 상태. 한 자리에서만 정한다 — 세는 곳마다 다르면 집계가 어긋난다.

        not_declared  실문서 인테이크가 아니다(합성·일반 업로드) → 출처 개념이 없다
        recorded      원천 위치·사용 권한 근거가 **둘 다** 있다
        partial       하나만 있다
        pending       둘 다 없다

    [2026-08-31] 이 값은 더 이상 **게이트가 아니다.** 등급 확정도 평가 정답지 승격도
    막지 않는다(발주처 지시). 남은 쓰임은 보고다 — 실문서 중 출처를 아직 못 적은 것이
    몇 건인지 세는 것.
    """
    if not is_actual_intake:
        return "not_declared"
    if source_reference and authorization_basis:
        return PROVENANCE_RECORDED
    if source_reference or authorization_basis:
        return "partial"
    return "pending"


# 검수 큐에서 빠지는 상태들 — 목록 기본 조회·품질 집계에서 뺀다(B1·B2).
# 문자열을 여기저기 박아 두면 한 곳만 고쳐도 나머지가 어긋난다.
DISCARDED_STATUS = "discarded"
OUT_OF_SCOPE_STATUS = "out_of_scope"      # 검수 대상 아님(B2)
# 검수가 끝나 큐에서 빠지는 집합. deferred(보류)는 여기 없다 — 다시 볼 것이기 때문이다.
QUEUE_EXCLUDED_STATUSES = frozenset({DISCARDED_STATUS, OUT_OF_SCOPE_STATUS})

# [E1-2] 배치가 "끝났나" 를 판정하는 집합 — 코드에 녹여 두면 세는 곳마다 달라진다.
#
# ⚠ deferred(보류)를 **종결에 넣는다.** 보류는 사유를 적어야 하는 결정이고, 검수자가 그
#   문서에 대해 할 수 있는 판단을 이미 한 상태다. 미완료로 세면 보류가 1건이라도 남는 순간
#   그 배치는 영원히 100% 가 되지 않아 검수 회차를 닫을 수 없다.
#   대신 batch_summary 에 deferred 수를 따로 실어, '보류를 안고 닫았다' 가 보이게 한다.
TERMINAL_REVIEW_STATUSES = frozenset({
    "approved_proxy", "grade_fixed_unlocked", "deferred",
    DISCARDED_STATUS, OUT_OF_SCOPE_STATUS,
})


def normalize_doc_id(doc_id: str) -> str:
    """전달본 doc_id 를 콘솔 doc_id 로 맞춘다.

    전달본  GOLD-CAND-TS-ENG-053_적층공정_공정조건표
    콘솔    GOLD-CAND-TS-ENG-053

    ⚠ 무조건 첫 '_' 앞을 취하면 안 된다. 'GOLD-' 로 시작하는 것만 잘라 낸다 —
      업로드 문서(GOLD-UPL-* 이외의 형식)까지 자르면 서로 다른 문서가 한 id 로 뭉친다.
    이 규칙이 시험 파일에만 있어서(test_review_batch_filter.py) 배치 집계·preflight 가
    같은 규칙 위에 설 수 없었다. 운영 코드로 올린다.
    """
    doc_id = str(doc_id or "").strip()
    return doc_id.split("_")[0] if doc_id.startswith("GOLD-") else doc_id


class ProxyGoldCandidateService:
    """Read synthetic candidates and record append-only manager decisions."""

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root).resolve() if root else _DEFAULT_ROOT.resolve()
        self.ledger_path = self.root / _LEDGER_NAME
        self.lock_path = self.root / f"{_LEDGER_NAME}.lock"
        self.locked_path = self.root / _LOCKED_LEDGER_NAME

    def list_candidates(
        self, *, status: str | None = None, grade: str | None = None,
        origin: str | None = None, query: str | None = None,
        review_batch: str | None = None,
    ) -> dict[str, Any]:
        all_candidates = self._candidates()
        # [B1-1] 기본 조회에서 폐기(discarded)를 뺀다. 폐기는 검수가 끝난 항목인데 목록에
        # 남아 있으면 검수자에게 계속 할 일로 보인다.
        # ⚠ 원장은 그대로다 — status="discarded" 로 명시하면 전부 나온다(조회 가능 = 보존).
        # 부수효과 하나: 종전에는 필터가 하나도 없으면 candidates 가 캐시 리스트 **그 자체**라
        # 아래 sort 가 캐시를 제자리에서 뒤집었다. 이제 항상 새 리스트라 그 일이 없다.
        if status:
            candidates = [c for c in all_candidates if c["status"] == status]
        else:
            candidates = [c for c in all_candidates
                          if c["status"] not in QUEUE_EXCLUDED_STATUSES]
        if grade:
            candidates = [c for c in candidates if c["final_grade"] == grade or c["proposed_grade"] == grade]
        if origin:
            candidates = [c for c in candidates if c["document_origin"] == origin]
        # [검수 배치] 콘솔 전체가 306건인데 이번 검수 대상은 그중 120건이다. 표식이
        # 없으면 검수자가 어느 문서를 봐야 하는지 알 수 없다(실측 2026-08-14: 적재만
        # 해 놓고 배포했으면 검수자가 306건 앞에서 멈췄을 자리다).
        if review_batch:
            candidates = [c for c in candidates if c.get("review_batch") == review_batch]
        if query:
            needle = query.strip().lower()
            if needle:
                candidates = [c for c in candidates if needle in c["doc_id"].lower() or needle in c["title"].lower()]
        candidates.sort(key=lambda c: c["doc_id"])
        # 화면은 목록 응답에 실린 summary 로 KPI·품질 지표를 그린다(별도 /summary 를 안 부른다).
        # quality 를 여기 빼먹으면 품질 패널이 "지표를 낼 수 없습니다"로만 뜬다(실측).
        embedded = self._summary(all_candidates)
        embedded["quality"] = self._quality(all_candidates)
        # [B1-3] KPI(summary)는 **원장 전량** 기준으로 둔다 — 화면 상단 숫자가 필터마다
        # 흔들리면 무엇을 세는 값인지 알 수 없다. 대신 목록 건수(total)와 다르다는 사실을
        # 응답에 적어, 화면이 "전체 306 / 목록 300 (폐기 6 제외)" 처럼 읽히게 한다.
        embedded["scope"] = "all"
        return {
            "total": len(candidates),
            "listed_excludes_discarded": status is None,
            "summary": embedded,
            # [E1-1] 필터된 집합 기준 집계 — **새 키**로 둔다. 기존 summary 는 화면 KPI
            # 카드가 쓰고 있어 의미를 바꾸면 상단 숫자가 필터마다 흔들린다.
            # 추가 디렉터리 스캔 없이 이미 읽은 리스트에서 센다.
            "batch_summary": self._batch_summary(candidates),
            # [2026-08-24] 화면이 배치 표식을 **데이터에서** 알게 한다. 종전에는 화면이
            # 자유입력 칸 하나였고, 툴팁에 "지금 서버의 후보에는 배치 값이 들어 있지 않아
            # 무엇을 넣어도 0건" 이라는 문장이 박혀 있었다. 그 문장은 사실이 아니었다 —
            # 실측 2026-08-24(223): 후보 115건이 review_batch="kl-ff5a822c" 를 달고 있다.
            # 화면이 서버 상태를 **문장으로 단정하면** 데이터가 바뀌어도 문장은 안 바뀐다.
            # 원장 전량 기준으로 세므로 상태·등급 필터를 어떻게 걸어도 목록이 흔들리지 않는다.
            "available_batches": self._available_batches(all_candidates),
            "candidates": [{k: v for k, v in c.items() if k != "text"} for c in candidates],
        }

    @staticmethod
    def _available_batches(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """원장에 실제로 존재하는 검수 배치 표식과 그 후보 수(배치명 오름차순).

        배치 표식이 없는 후보는 세지 않는다 — 목록에 "(없음)" 항목을 만들면 그것이
        배치인 줄 알고 고르게 된다. 표식이 하나도 없으면 빈 목록이고, 그때는 화면이
        "이 서버에는 배치 표식이 없습니다" 를 **이 응답을 근거로** 말한다.
        """
        counter = Counter(
            b for c in candidates if (b := str(c.get("review_batch") or "").strip())
        )
        return [{"review_batch": b, "total": n} for b, n in sorted(counter.items())]

    def summary(self) -> dict[str, Any]:
        candidates = self._candidates()
        out = self._summary(candidates)
        out["quality"] = self._quality(candidates)
        return out

    def recent_decisions(self, limit: int = 100) -> dict[str, Any]:
        """결정 원장 최근 기록 — 보류·폐기·번복까지 그대로 보인다.

        화면에는 문서 하나를 골라야 이력이 보였다. 보류·폐기가 왜 그렇게 됐는지 훑어보려면
        문서를 일일이 열어야 해서, 감사 목적으로는 쓸 수 없었다.
        """
        events: list[dict[str, Any]] = []
        if self.ledger_path.exists():
            for line in self.ledger_path.read_text(encoding="utf-8").splitlines():
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        by_action = Counter(str(e.get("action") or "") for e in events)
        return {
            "total": len(events),
            "by_action": dict(sorted(by_action.items())),
            "events": list(reversed(events))[: max(1, min(int(limit), 500))],
        }

    @staticmethod
    def _quality(candidates: list[dict[str, Any]]) -> dict[str, Any]:
        """골든셋 자체의 건강도 — 등급을 맞히는 데 본문 말고 다른 단서가 섞였는지 본다.

        · length_only_1nn: 문서 **길이만** 보고 등급을 맞히는 비율. 무작위(0.25)를 크게 넘으면
          길이가 등급의 대리변수라는 뜻이고, 그 셋으로 잰 정확도는 부풀려진다.
          (실측: 기존 777건 패키지 0.793 — 짧으면 고등급 / 길면 S3)
        · grade_token_exposed: 본문에 자기 등급 문자열이 남아 있는 건수. 검수자가 문서를 읽기
          전에 답을 보면 검수가 검증이 아니라 확인 절차가 된다.
        """
        # [B1-2] 폐기한 문서를 품질 모수에서 뺀다.
        # discard 는 final_grade 만 None 으로 되돌리고 proposed_grade(intended_label)는 남긴다.
        # 그래서 종전에는 폐기 문서가 길이누출·등급노출·등급균형·실문서비율에 계속 잡혔다 —
        # 골든셋에서 뺀 문서가 그 골든셋의 건강도를 계속 좌우한 셈이다.
        live = [c for c in candidates if c.get("status") not in QUEUE_EXCLUDED_STATUSES]
        graded = [
            (len(c.get("text") or ""), c.get("final_grade") or c.get("proposed_grade"), c)
            for c in live
        ]
        graded = [(ln, g, c) for ln, g, c in graded if g in _VALID_GRADES and ln > 0]
        if not graded:
            return {"documents": 0}

        pairs = sorted((ln, g) for ln, g, _c in graded)
        hit = 0
        for i, (ln, lab) in enumerate(pairs):
            best, best_d = None, None
            for j in (i - 1, i + 1):
                if 0 <= j < len(pairs):
                    d = abs(pairs[j][0] - ln)
                    if best_d is None or d < best_d:
                        best_d, best = d, pairs[j][1]
            hit += best == lab
        leak = round(hit / len(pairs), 3)

        exposed = sum(
            1 for _ln, _g, c in graded
            if _exposes_grade(c.get("text") or "",
                              is_real=bool(c.get("is_actual_document")))
        )
        per_grade: dict[str, dict[str, int]] = {}
        for g in sorted(_VALID_GRADES):
            v = sorted(ln for ln, gg, _c in graded if gg == g)
            if v:
                per_grade[g] = {"n": len(v), "min": v[0], "p50": v[len(v) // 2], "max": v[-1]}
        counts = [d["n"] for d in per_grade.values()]
        allv = sorted(ln for ln, _g, _c in graded)
        real = sum(1 for _ln, _g, c in graded if c.get("is_actual_document"))
        return {
            "documents": len(graded),
            "length": {"min": allv[0], "p50": allv[len(allv) // 2], "max": allv[-1]},
            "length_by_grade": per_grade,
            "length_only_1nn": leak,
            "length_only_random": 0.25,
            "grade_token_exposed": exposed,
            "real_documents": real,
            "real_ratio": round(real / len(graded), 3),
            "grade_balance_ratio": round(max(counts) / max(min(counts), 1), 2) if counts else None,
        }

    def get_candidate(self, doc_id: str) -> dict[str, Any] | None:
        candidate = next((c for c in self._candidates() if c["doc_id"] == doc_id), None)
        if candidate is not None:
            candidate = dict(candidate)
            candidate["decision_history"] = self._history(doc_id)
        return candidate

    def decide(
        self,
        *,
        doc_id: str,
        action: str,
        actor_id: str,
        grade: str | None = None,
        reason: str = "",
        security_marking: str | None = None,
        access_scope: str | None = None,
    ) -> dict[str, Any] | None:
        """검수 결정을 원장에 남긴다. 비밀관리성(M) 입력도 여기서 함께 받는다.

        M 을 결정과 같은 이벤트에 싣는 이유 — M 은 등급 결정의 **입력**이지 부수 기록이
        아니다. 사유(reason)와 같은 줄에 있어야 "왜 이 등급인지" 가 나중에 재구성된다.
        출처(provenance)를 별도 경로로 뺀 것과는 반대 이유다 — 그쪽은 "등급은 그대로
        두고 출처만 기록" 을 표현해야 해서 분리했다(record_provenance docstring).
        """
        if action not in _VALID_ACTIONS:
            raise ValueError("unsupported decision action")
        # ICD §3.2·§3.3 의 허용값인가. 목록은 rule_engine 한 곳에서만 정한다 — 여기에
        # 따로 적어 두면 규약이 바뀔 때 두 곳이 조용히 어긋난다.
        from koipa.modules.m3_labeling.rule_engine import (  # noqa: PLC0415
            _ICD_MARKINGS, _ICD_SCOPES, management_from_metadata,
        )
        if security_marking is not None and security_marking not in _ICD_MARKINGS:
            raise ValueError(f"unsupported security_marking: {security_marking}")
        if access_scope is not None and access_scope not in _ICD_SCOPES:
            raise ValueError(f"unsupported access_scope: {access_scope}")
        reason = reason.strip()
        if action in {"change", "defer", "reject", "discard", "exclude"} and not reason:
            raise ValueError("reason is required for change, defer, reject, discard, and exclude")
        if action == "change" and grade not in _VALID_GRADES:
            raise ValueError("grade is required for change")
        if action != "change" and grade is not None:
            raise ValueError("grade is allowed only for change")
        candidate = self.get_candidate(doc_id)
        if candidate is None:
            return None
        is_synthetic = candidate["document_origin"] == "synthetic"
        if action == "approve" and (not is_synthetic or candidate["proposed_grade"] not in _VALID_GRADES):
            raise ValueError("approve is allowed only for a synthetic candidate with a proposed grade")

        # [2026-08-31] 출처 기록은 등급 확정을 **막지 않는다** — 발주처(지재원) 지시.
        #
        # 종전(2026-08-23)에는 실문서에 원천 위치·사용 권한 근거가 둘 다 없으면 등급 확정
        # 자체를 거부했다. 걷는 이유는 등급의 근거가 검수자의 판단이지 출처 칸이 아니기
        # 때문이다. 출처는 "이 문서를 어디서 가져왔나"를 나중에 답하기 위한 별개 기록이고,
        # 요구사항 추적표에도 이 요구는 없다(RTM 에 '권한' 0건 · 2026-08-31 확인).
        #
        # 막지 않는 대신 **결정 시점의 출처 상태를 원장에 함께 남긴다.** 안 세면 "출처 없이
        # 확정된 것이 몇 건인가"에 답할 수 없고, 그건 감리에서 실제로 받는 질문이다.
        #
        # 상용 LLM 반출 차단은 이 값과 무관하다 — golden_tiers.may_send_to_commercial_llm
        # 은 document_origin 만 본다. 이 완화로 반출 경계가 넓어지지 않는다.
        provenance_at_decision = None
        if candidate.get("is_actual_document"):
            provenance_at_decision = (candidate.get("provenance") or {}).get("status") or "pending"

        final_grade = candidate["proposed_grade"] if action == "approve" else grade
        status = {
            "approve": "approved_proxy",  # synthetic only (guarded above)
            "change": "approved_proxy" if is_synthetic else "grade_fixed_unlocked",
            "defer": "deferred",
            "reject": "discarded",        # legacy API action retained
            "discard": "discarded",
            "reopen": "proposed" if is_synthetic else "under_review",
            "exclude": OUT_OF_SCOPE_STATUS,
        }[action]
        # 등급을 확정하지 않는 전이는 final_grade 를 반드시 비운다 — 남겨 두면 '확정 아님'
        # 인데 등급이 있는 레코드가 생겨 집계가 어긋난다.
        if action in {"defer", "reject", "discard", "reopen", "exclude"}:
            final_grade = None
        event = {
            "schema_version": 1,
            "event_id": str(uuid4()),
            "doc_id": doc_id,
            "action": action,
            "status": status,
            "proposed_grade": candidate["proposed_grade"],
            "final_grade": final_grade,
            "reason": reason,
            "actor_id": actor_id,
            "decided_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "document_sha256": candidate["document_sha256"],
            "document_origin": candidate["document_origin"],
            "claim_scope": candidate["claim_scope"],
            # 실문서만 값이 있다(합성은 None). 등급 확정을 막지는 않지만 사후에 셀 수 있게 남긴다.
            "provenance_at_decision": provenance_at_decision,
        }

        # 비밀관리성(M). 이번 결정에서 준 값만 덮고 나머지는 이전 값을 잇는다 — 한 칸만
        # 고치러 들어온 검수자가 다른 칸을 지우게 되면 M 이 조용히 바뀐다.
        meta_path = self.root / f"{doc_id}.metadata.json"
        meta: dict[str, Any] = {}
        mgmt_after: dict[str, Any] | None = None
        if (security_marking is not None or access_scope is not None) and meta_path.is_file():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            prev_mgmt = dict(meta.get("management") or {})
            mgmt_after = dict(prev_mgmt)
            if security_marking is not None:
                mgmt_after["security_marking"] = security_marking
            if access_scope is not None:
                mgmt_after["access_scope"] = access_scope
            m_state, m_level, m_reason = management_from_metadata(
                mgmt_after.get("security_marking"), mgmt_after.get("access_scope")
            )
            mgmt_after.update(
                state=m_state, level=m_level, reason=m_reason,
                recorded_by=actor_id,
                recorded_at=dt.datetime.now(dt.timezone.utc).isoformat(),
            )
            meta["management"] = mgmt_after
            # 감사에서 되짚을 수 있게 이전 값도 남긴다(record_provenance 와 같은 규칙).
            event["management_before"] = prev_mgmt
            event["management_after"] = mgmt_after

        self.root.mkdir(parents=True, exist_ok=True)
        with _exclusive_ledger_lock(self.lock_path):
            if mgmt_after is not None:
                meta_path.write_text(
                    json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )
            with self.ledger_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        return self.get_candidate(doc_id)

    def promote_decisions_to_locked(
        self,
        *,
        publish: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """등급을 확정한 콘솔 결정을 사람 서명(locked_gold_eval)으로 승격한다.

        **이 메서드가 있기 전까지 콘솔 검수는 평가정답을 한 건도 만들지 못했다**(2026-09-09
        실측). decide() 는 원장에 이벤트만 남기고 label_source 를 쓰지 않았고, 그 원장을
        tier 로 넘기는 코드가 어디에도 없었다. 배선의 전말은 koipa.console_signoff 참조.

        원장을 **투영**한다 — decide() 의 쓰기 경로는 건드리지 않는다. 그래서:

          · 이미 쌓인 결정도 소급 승격된다(서버에 남아 있는 검수 이력이 그대로 살아난다).
          · 여러 번 돌려도 결과가 같다(doc_id dedup 누적 — apply_signoff 와 같은 규율).
          · reviewer_id 는 **결정을 내린 검수자**(원장 actor_id = 포털 JWT sub)다.
            승격을 실행한 관리자가 아니다. 머신·플레이스홀더는 promote_to_locked 내부
            is_human_reviewer 가 거부한다(신원 위조 차단).

        publish=False(기본)면 후보 폴더의 locked_console_review.jsonl 에만 누적하고
        라이브 readiness 읽기경로(settings.locked_eval_jsonl)는 건드리지 않는다 —
        apply_signoff 와 같은 계약이다. dry_run 은 **쓰기만** 건너뛰고 집계는 그대로 돌려,
        무엇이 승격될지를 같은 응답 모양으로 미리 보여준다.
        """
        from koipa.config import settings  # noqa: PLC0415
        from koipa.console_signoff import build_promotion_inputs  # noqa: PLC0415
        from koipa.golden_signoff import (  # noqa: PLC0415
            merge_locked_records, promote_to_locked,
        )
        from koipa.golden_tiers import is_real_locked_eval  # noqa: PLC0415
        from koipa.services.golden_build_service import (  # noqa: PLC0415
            _atomic_write_jsonl, _read_jsonl,
        )

        candidates = self._candidates()
        records, signoffs = build_promotion_inputs(candidates)
        result = promote_to_locked(records, signoffs)

        accumulated = merge_locked_records(_read_jsonl(self.locked_path), result.locked)
        # 승격한 뒤에 검수자가 **판단을 물린** 문서는 평가정답에서 뺀다.
        #
        # 원장은 append-only 라 승격 뒤에도 보류·폐기·범위밖·재검토가 얼마든지 올라온다.
        # 누적만 하면 검수자가 "이건 아니다" 라고 되돌린 문서가 평가정답으로 남는다 —
        # apply_signoff 가 _merge_rejected_records 로 막는 것과 같은 자리다.
        # 후보 폴더에서 사라진 doc_id 는 건드리지 않는다(판단을 물린 것이 아니라 알 수 없는
        # 것이다). 지금 후보로 보이는데 승격 대상이 아닌 것만 뺀다.
        promoted_ids = {r["doc_id"] for r in records}
        withdrawn = {
            str(c.get("doc_id")) for c in candidates
            if str(c.get("doc_id")) not in promoted_ids
        }
        accumulated = [r for r in accumulated if r.get("doc_id") not in withdrawn]
        if not dry_run and (accumulated or self.locked_path.exists()):
            _atomic_write_jsonl(self.locked_path, accumulated)

        live_path = str(getattr(settings, "locked_eval_jsonl", "") or "")
        published = False
        publish_note = None
        if publish:
            # 반영되지 못하는 경우를 조용한 no-op 로 두지 않는다 — 실행한 사람이 화면에서
            # 바로 알아야 한다(apply_signoff 가 같은 이유로 같은 문구를 쓴다).
            if not result.locked:
                publish_note = "publish 요청됐으나 승격 locked 0건 — 반영 대상 없음"
            elif not live_path:
                publish_note = (
                    "publish 요청됐으나 LOCKED_EVAL_JSONL 미설정 — 배포 게이트 미반영. "
                    "프로파일 env 에 locked_eval_jsonl 경로를 설정하세요"
                )
            elif dry_run:
                publish_note = "dry_run — 라이브 경로에 쓰지 않았습니다(미리보기)"
            else:
                _atomic_write_jsonl(
                    Path(live_path),
                    merge_locked_records(_read_jsonl(Path(live_path)), result.locked),
                )
                published = True

        return {
            "candidates": len(candidates),
            "promotable": len(records),
            "locked": len(result.locked),
            "rejected": len(result.rejected),
            "locked_by_grade": result.stats["locked_by_grade"],
            "rejected_reasons": result.stats["rejected_reasons"],
            # 실문서 평가정답 — 합성 본문 서명은 locked tier 에는 들어가지만 이 수에는
            # 안 잡힌다(is_real_locked_eval). 감리 회신의 '실문서 몇 건' 이 이 값이다.
            "real_locked": sum(1 for r in accumulated if is_real_locked_eval(r)),
            "locked_total": len(accumulated),
            "locked_path": str(self.locked_path),
            "published": published,
            "publish_note": publish_note,
            "dry_run": bool(dry_run),
        }

    def record_provenance(
        self,
        *,
        doc_id: str,
        source_reference: str,
        authorization_basis: str,
        actor_id: str,
        reason: str = "",
    ) -> dict[str, Any] | None:
        """이미 올라간 실문서에 **출처를 나중에 기록한다.**

        왜 결정 API 와 분리했나. `decide()` 는 action 마다 status 를 정하는 표를 갖고 있어
        (approve→approved_proxy, change→grade_fixed_unlocked …), 결정에 출처를 얹으면
        **"등급은 그대로 두고 출처만 기록" 을 표현할 수 없다.** 별도 경로로 둔다.

        실측 2026-08-17(223): 실문서 74건 중 62건이 출처를 갖고 있었으나 적재 스크립트가
        metadata top-level 에 써서 화면에서 사라졌고, 그 62건에는 **사용 권한 근거가 없다.**
        그 62건을 완결하려면 사람이 권한 근거를 채워야 하는데 그 경로가 없었다.

        ⚠ 원장에 event_kind="provenance" 로 남긴다. 결정 이벤트가 아니므로
          `_latest_decisions` 가 걸러낸다 - 안 걸러내면 등급 확정이 이 줄로 덮인다.
        ⚠ 합성 후보는 대상이 아니다. 출처·권한 근거는 실문서에만 뜻이 있다.
        """
        source_reference = (source_reference or "").strip()
        authorization_basis = (authorization_basis or "").strip()
        # [2026-08-31] 아는 만큼만 적어도 저장된다 — 종전에는 둘 다 없으면 거부했다.
        # 출처가 등급 확정을 막지 않게 된 이상, 반쪽 기록을 거부해 봐야 아무것도 안 남을 뿐이다.
        # 둘 다 비었을 때만 거부한다(빈 저장은 원장에 뜻 없는 줄을 남긴다).
        if not source_reference and not authorization_basis:
            raise ValueError("source_reference or authorization_basis is required")
        candidate = self.get_candidate(doc_id)
        if candidate is None:
            return None
        if not candidate.get("is_actual_document"):
            raise ValueError("provenance is recorded for actual documents only")

        meta_path = self.root / f"{doc_id}.metadata.json"
        if not meta_path.is_file():
            raise ValueError("candidate metadata not found")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        prev = dict(meta.get("provenance") or {})
        meta["provenance"] = {
            "source_reference": source_reference,
            "authorization_basis": authorization_basis,
            "status": _provenance_status(True, source_reference, authorization_basis),
            "origin": "console_record",
            "recorded_by": actor_id,
            "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        event = {
            "schema_version": 1,
            "event_kind": "provenance",
            "event_id": str(uuid4()),
            "doc_id": doc_id,
            # 결정 이벤트와 같은 자리에 쌓이므로 결정 필드(final_grade·status)는 넣지 않는다.
            # 넣으면 나중에 누가 이 줄을 결정으로 읽을 수 있다.
            "action": "record_provenance",
            "reason": reason.strip(),
            "actor_id": actor_id,
            "decided_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "document_sha256": candidate["document_sha256"],
            "document_origin": candidate["document_origin"],
            # 무엇이 어떻게 바뀌었는지 - 감사에서 되짚을 수 있게 이전 값도 남긴다.
            "provenance_before": prev,
            "provenance_after": meta["provenance"],
        }
        self.root.mkdir(parents=True, exist_ok=True)
        with _exclusive_ledger_lock(self.lock_path):
            meta_path.write_text(
                json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            with self.ledger_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        return self.get_candidate(doc_id)

    def create_uploaded_candidate(
        self, *, filename: str, content: bytes, actor_id: str,
        document_origin: str = "uploaded_document", source_reference: str = "",
        authorization_basis: str = "", title: str = "",
    ) -> dict[str, Any]:
        """Store an uploaded document as an ungraded review item.

        ``public_real`` and ``organization_real`` are *intake* origins, not a
        locked-gold claim.  They require an explicit source reference and usage
        authorization so real documents cannot silently enter under the generic
        upload label.  Upload never sends content to an LLM.
        """
        if not content:
            raise ValueError("empty file")
        document_origin = document_origin.strip()
        if document_origin not in _DOCUMENT_ORIGINS:
            raise ValueError("unsupported document origin")
        source_reference = source_reference.strip()
        authorization_basis = authorization_basis.strip()
        is_actual_intake = document_origin in {"public_real", "organization_real"}
        # [2026-08-23] 출처·권한 근거는 업로드에서 강제하지 않는다.
        # [2026-08-31] 옮겨 갔던 게이트(decide · promote_to_locked)도 발주처 지시로 걷었다 —
        # 이제 출처는 어느 자리에서도 막지 않는다. 남은 것은 기록과 집계뿐이다.
        # 강제가 현관에만 있고 목적지에는 없어서, 실제로 일어난 일은 평가셋 보호가 아니라 등록
        # 실패였다 — 223 실측 2026-08-17: 실문서 74건 중 62건이 권한 근거 없이 미완으로 남았다.
        # 후보 등록 자체는 해가 없다(후보는 평가 정답지가 아니고, locked 승격은 사람 서명이다).
        safe_name = Path(filename or "uploaded_document").name
        suffix = Path(safe_name).suffix.lower()
        if not suffix:
            raise ValueError("filename extension is required")
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp:
            temp.write(content)
            temp_path = Path(temp.name)
        try:
            from koipa.modules.m2_preprocess.extractor import extract  # noqa: PLC0415
            extracted = extract(temp_path)
        finally:
            temp_path.unlink(missing_ok=True)
        if extracted.error:
            raise ValueError(f"extraction failed: {extracted.error}")
        text = (extracted.text or "").strip()
        if len(text) < 80:
            raise ValueError("extracted text is too short; scan/OCR or source file review is required")

        doc_id = f"GOLD-UPL-{uuid4().hex[:12].upper()}"
        safe_stem = re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", Path(safe_name).stem).strip("._") or "uploaded"
        title = title.strip() or safe_stem
        source_dir = self.root / "uploaded_originals"
        source_path = source_dir / f"{doc_id}_{safe_stem}{suffix}"
        markdown_path = self.root / f"{doc_id}_{safe_stem}.md"
        meta_path = self.root / f"{doc_id}.metadata.json"
        raw_hash = hashlib.sha256(content).hexdigest()
        metadata = {
            "doc_id": doc_id,
            "intended_label": None,
            "document_origin": document_origin,
            "document_type": title,
            "authoring_method": "operator_upload",
            "requires_manual_audit": True,
            "candidate_status": "under_review",
            "uploaded_by": actor_id,
            "uploaded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "source_filename": safe_name,
            "source_file_sha256": raw_hash,
            "provenance": {
                "source_reference": source_reference or None,
                "authorization_basis": authorization_basis or None,
                "recorded_by": actor_id,
                "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                # 업로드가 두 칸을 강제하지 않게 되었으므로 status 를 **실제 입력으로**
                # 정한다. 종전에는 intake 이기만 하면 무조건 "recorded" 를 적었다 - 빈
                # 값에도 기록됐다고 적히면 등급 확정 게이트가 그냥 통과한다.
                "status": _provenance_status(
                    is_actual_intake, source_reference, authorization_basis
                ),
            },
            "extraction": {
                "method": extracted.method,
                "quality": extracted.quality,
                "ocr_used": extracted.ocr_used,
                "pages_processed": extracted.pages,
                "pages_total": extracted.total_pages,
                "warnings": extracted.warnings,
                "table_coverage": extracted.table_coverage,
            },
            "claim_scope": (
                "actual-document intake with recorded provenance; awaiting human review; "
                "not locked gold and not a claim of operational accuracy"
                if is_actual_intake else
                "operator-uploaded document awaiting provenance and human review; "
                "not locked gold and not a claim of operational accuracy"
            ),
        }
        self.root.mkdir(parents=True, exist_ok=True)
        source_dir.mkdir(parents=True, exist_ok=True)
        with _exclusive_ledger_lock(self.lock_path):
            if source_path.exists() or markdown_path.exists() or meta_path.exists():
                raise RuntimeError("generated upload identifier collision")
            source_path.write_bytes(content)
            markdown_path.write_text(text + "\n", encoding="utf-8")
            meta_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        candidate = self.get_candidate(doc_id)
        assert candidate is not None
        return candidate

    def _scan(self) -> tuple[tuple, list[str], dict[str, list[str]]]:
        """디렉터리를 **한 번만** 훑어 (캐시키, metadata 목록, doc_id→본문파일) 을 만든다.

        종전에는 후보마다 `glob(f"{doc_id}_*.md")` 를 돌려 2,440개 엔트리 디렉터리를 272번
        재스캔했고(바인드 마운트에서 O(N×M)), 목록 응답에서 버릴 본문 30MB 를 매 요청 읽었다.
        실측: /golden/candidates 와 /summary 가 **120초 타임아웃**.
        """
        metas: list[str] = []
        docs: dict[str, list[str]] = {}
        newest = 0
        count = 0
        # [2026-08-31] 캐시 키에 **바이트 크기**를 함께 넣는다 — mtime 만으로는 부족하다.
        #
        # 실측(223 · 30회 반복): 등급 확정 직후 보류를 걸면 30회 중 11회가 옛 상태를
        # 돌려줬다. 원인은 이 컨테이너 파일시스템의 mtime 해상도가 4ms 라는 것이다.
        # 두 기록이 같은 4ms 틱에 떨어지면 ledger_mtime 이 같아 캐시 키가 충돌하고,
        # 원장에는 defer 가 정확히 적혀 있는데 화면은 확정 상태를 그대로 보여준다.
        # (간격 0 이던 횟수 11 = 상태가 안 바뀐 횟수 11 로 정확히 일치했다)
        #
        # 원장은 append-only 라 **크기가 반드시 커진다.** 크기를 키에 넣으면 시계 해상도와
        # 무관해진다. 메타 수정도 대개 크기를 바꾸므로 mtime 과 함께 쓰면 더 촘촘해진다.
        # 비용은 없다 — stat() 은 이미 부르고 있고 st_size 를 같이 읽을 뿐이다.
        total_size = 0
        with os.scandir(self.root) as it:
            for entry in it:
                if not entry.is_file():
                    continue
                name = entry.name
                count += 1
                try:
                    stat = entry.stat()
                    mtime, size = stat.st_mtime_ns, stat.st_size
                except OSError:
                    mtime, size = 0, 0
                if mtime > newest:
                    newest = mtime
                total_size += size
                if name.endswith(".metadata.json"):
                    metas.append(name)
                elif name.endswith(".md") and "_" in name:
                    docs.setdefault(name.split("_", 1)[0], []).append(name)
        metas.sort()
        ledger_mtime = 0
        ledger_size = -1
        try:
            ledger_stat = self.ledger_path.stat()
            ledger_mtime, ledger_size = ledger_stat.st_mtime_ns, ledger_stat.st_size
        except OSError:
            pass
        return (
            str(self.root), count, newest, total_size, ledger_mtime, ledger_size,
        ), metas, docs

    def _candidates(self) -> list[dict[str, Any]]:
        if not self.root.exists():
            return []
        cache_key, metas, docs_by_id = self._scan()
        cached = _CANDIDATE_CACHE.get(cache_key)
        if cached is not None:
            return cached
        latest = self._latest_decisions()
        rows: list[dict[str, Any]] = []
        for meta_name in metas:
            meta_path = self.root / meta_name
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                # 후보가 목록에서 조용히 사라지던 자리. 검수자는 "왜 안 보이지"를
                # 알 방법이 없었다. 건너뛰는 동작은 그대로 두고 사실만 남긴다.
                logger.warning("골든 후보 건너뜀 — 메타를 읽지 못함: %s (%s: %s)",
                               meta_name, type(exc).__name__, exc)
                continue
            doc_id = str(meta.get("doc_id") or "")
            if not doc_id:
                logger.warning("골든 후보 건너뜀 — 메타에 doc_id 가 없음: %s", meta_name)
                continue
            revision = str(meta.get("content_revision_path") or "").strip()
            revision_path = (self.root / revision).resolve() if revision else None
            if revision_path and revision_path.is_relative_to(self.root) and revision_path.is_file():
                source = revision_path
            else:
                names = docs_by_id.get(doc_id) or []
                if len(names) != 1:
                    # 본문 파일을 하나로 특정하지 못하면 화면에서 사라진다.
                    # 0건이면 없는 것이고, 2건 이상이면 어느 것인지 못 정한 것이다.
                    logger.warning(
                        "골든 후보 건너뜀 — 본문 파일 특정 실패: doc_id=%s 후보 %d건",
                        doc_id, len(names),
                    )
                    continue
                source = self.root / names[0]
            try:
                text = source.read_text(encoding="utf-8")
            except OSError as exc:
                logger.warning("골든 후보 건너뜀 — 본문을 읽지 못함: doc_id=%s path=%s (%s)",
                               doc_id, source.name, type(exc).__name__)
                continue
            decision = latest.get(doc_id, {})
            document_origin = str(meta.get("document_origin") or "unknown")
            proposed = str(meta.get("intended_label") or "") or None
            proposed_basis = None
            if proposed is None and document_origin == "public_real":
                proposed = "S3"
                proposed_basis = "public source recorded; human confirmation pending"
            try:
                document_path = str(source.relative_to(_POC_ROOT))
            except ValueError:
                # 테스트/운영 도구가 별도 루트를 주입한 경우에도 목록 자체는 제공한다.
                document_path = str(source)
            rows.append({
                "doc_id": doc_id,
                "title": str(meta.get("document_type") or source.stem),
                "proposed_grade": proposed,
                "proposed_grade_basis": proposed_basis,
                "final_grade": decision.get("final_grade"),
                "status": decision.get("status") or str(meta.get("candidate_status") or "proposed"),
                "document_origin": document_origin,
                "requires_manual_audit": bool(meta.get("requires_manual_audit")),
                # 검수 배치 표식. 전달본 단위로 묶어 목록을 좁힌다.
                "review_batch": str(meta.get("review_batch") or "") or None,
                "claim_scope": str(meta.get("claim_scope") or ""),
                "document_path": document_path,
                "content_revision": str(meta.get("content_revision") or "v1"),
                "characters": len(text),
                "document_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "latest_decision": decision or None,
                "grade_fixed": bool(decision.get("final_grade")),
                "extraction": meta.get("extraction"),
                # [E3a-5 2026-08-17] 출처가 **두 자리**에 있다. 읽는 쪽이 한 자리만 봐서
                # 실제로는 기록된 것이 "없음" 으로 보였다.
                #   provenance dict          업로드 API 경로가 쓰는 자리 (12건)
                #   metadata top-level       적재 스크립트가 쓴 자리 (62건)
                #     load_kl_review_pool_to_console.py:205 `"source_reference": r.get("source")`
                # 실측(223, 2026-08-17): 실문서 74건 중 62건이 top-level 에만 있었고
                # 전부 실제 값이 있었다("판례(2000+)" 등). 데이터가 없던 것이 아니다.
                # 자리를 합쳐서 읽는다 - 원본 파일은 안 건드린다(적재 스크립트는 E3a-7 에서 고친다).
                "provenance": _merged_provenance(meta),
                "source_file_sha256": str(meta.get("source_file_sha256") or "") or None,
                # [2026-08-23] 비밀관리성(M) — 검수 화면이 현재 값과 그 결과를 함께 보여준다.
                # state 를 같이 싣는 이유: "확인 안 됨(unknown)" 과 "전 임직원 열람
                # (proven_absent)" 은 M 을 정반대로 만드는데, 값만 보내면 화면이 그 차이를
                # 다시 계산해야 하고 그러면 판정식이 두 곳에 생긴다.
                "management": _management_view(meta),
                "is_actual_document": document_origin in {"public_real", "organization_real"},
                "text": text,
            })
        # 캐시키에 (파일수·최신 mtime·원장 mtime) 이 들어 있어 문서 추가·수정·결정 기록이
        # 생기면 자동으로 무효화된다. 폭주를 막기 위해 최근 몇 세대만 유지한다.
        if len(_CANDIDATE_CACHE) > 4:
            _CANDIDATE_CACHE.clear()
        _CANDIDATE_CACHE[cache_key] = rows
        return rows

    @staticmethod
    def _summary(candidates: list[dict[str, Any]]) -> dict[str, Any]:
        actual = [c for c in candidates if c["is_actual_document"]]
        return {
            "total": len(candidates),
            "fixed": sum(1 for c in candidates if c["grade_fixed"]),
            # [B2] 검수가 끝난 것은 '미확정' 에서 뺀다. 남겨 두면 화면 상단 '등급 미확정 N건'
            # 이 영원히 안 줄어 검수자에게 끝나지 않는 할 일로 보인다.
            "unfixed": sum(1 for c in candidates
                           if not c["grade_fixed"] and c["status"] not in QUEUE_EXCLUDED_STATUSES),
            "deferred": sum(1 for c in candidates if c["status"] == "deferred"),
            "discarded": sum(1 for c in candidates if c["status"] == DISCARDED_STATUS),
            "out_of_scope": sum(1 for c in candidates if c["status"] == OUT_OF_SCOPE_STATUS),
            "by_status": dict(sorted(Counter(c["status"] for c in candidates).items())),
            "by_origin": dict(sorted(Counter(c["document_origin"] for c in candidates).items())),
            "by_final_grade": dict(sorted(Counter(c["final_grade"] for c in candidates if c["final_grade"]).items())),
            "by_proposed_grade": dict(sorted(Counter(c["proposed_grade"] or "unassigned" for c in candidates).items())),
            "actual_document_intake": len(actual),
            "actual_provenance_recorded": sum(
                1 for c in actual if c.get("provenance", {}).get("status") == "recorded"
            ),
            # [E3a-5] 출처는 있는데 사용 권한 근거가 비어 완결되지 않은 것. 이 수가 보이지
            # 않으면 "기록 12건" 만 보고 나머지 62건에 아무 정보도 없다고 오해한다.
            "actual_provenance_partial": sum(
                1 for c in actual if c.get("provenance", {}).get("status") == "partial"
            ),
            # 옛 자리(metadata top-level)에서 끌어올린 것 — 적재 스크립트 교정(E3a-7) 전에
            # 들어온 분량이라, 이 수가 0 이 되면 이관이 끝난 것이다.
            "actual_provenance_legacy": sum(
                1 for c in actual if c.get("provenance", {}).get("origin") == "legacy_top_level"
            ),
            "actual_grade_fixed_unlocked": sum(
                1 for c in actual if c["status"] == "grade_fixed_unlocked"
            ),
        }

    @staticmethod
    def _batch_summary(candidates: list[dict[str, Any]]) -> dict[str, Any]:
        """이 목록(=이번 검수 배치) 기준 진행률.

        종전에는 집계가 항상 전량 기준이라, 120건짜리 회차만 걸러 봐도 진행률은 306건
        기준으로 나왔다. 검수자가 "내 회차가 끝났나" 를 화면에서 알 수 없었다.
        """
        terminal = sum(1 for c in candidates if c["status"] in TERMINAL_REVIEW_STATUSES)
        return {
            "total": len(candidates),
            "terminal": terminal,
            "pending": len(candidates) - terminal,
            # 보류는 종결로 세지만, 안고 닫았다는 사실이 보여야 한다.
            "deferred": sum(1 for c in candidates if c["status"] == "deferred"),
            "by_status": dict(sorted(Counter(c["status"] for c in candidates).items())),
            "by_final_grade": dict(sorted(
                Counter(c["final_grade"] for c in candidates if c["final_grade"]).items())),
        }
    def _latest_decisions(self) -> dict[str, dict[str, Any]]:
        if not self.ledger_path.exists():
            return {}
        latest: dict[str, dict[str, Any]] = {}
        for line in self.ledger_path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            doc_id = str(row.get("doc_id") or "")
            if not doc_id:
                continue
            # [E3a-2 2026-08-17] **결정 이벤트만** 현재 상태로 친다.
            # 종전에는 doc_id 별 마지막 줄을 무조건 썼다. 그 줄에서 final_grade·status·
            # grade_fixed 를 뽑아 목록·KPI 를 만들기 때문에, 결정이 아닌 이벤트(출처 기록 등)를
            # 원장에 남기면 **등급 확정이 통째로 지워진다** - 화면의 '확정' 수가 줄어든다.
            # 원장은 append-only 라 이벤트 종류가 늘어난다. 종류를 안 가리면 마지막 줄의
            # 성격에 따라 상태가 오락가락한다.
            if str(row.get("event_kind") or "decision") != "decision":
                continue
            latest[doc_id] = row
        return latest

    def _history(self, doc_id: str) -> list[dict[str, Any]]:
        if not self.ledger_path.exists():
            return []
        events: list[dict[str, Any]] = []
        for line in self.ledger_path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(row.get("doc_id") or "") == doc_id:
                events.append(row)
        return events
