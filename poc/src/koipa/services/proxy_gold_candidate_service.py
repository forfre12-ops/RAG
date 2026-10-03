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
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import AbstractSet, Any, Callable, Iterator
from uuid import uuid4

from koipa.jsonl_lines import dumps_line, split_lines

logger = logging.getLogger(__name__)

_POC_ROOT =Path(__file__).resolve().parents[3]
_DEFAULT_ROOT = _POC_ROOT / "datasets" / "proxy_gold" / "single_document_candidates"
_LEDGER_NAME = "candidate_decisions.jsonl"
# 콘솔 결정에서 승격된 사람 서명 평가정답. 후보·원장과 같은 폴더에 둔다 — 잡 단위로
# 갈리는 locked_<job_id>.jsonl 과 달리 콘솔 후보 풀은 하나뿐이라 파일도 하나다.
_LOCKED_LEDGER_NAME = "locked_console_review.jsonl"

# 후보 목록 캐시 — 키에 (루트·파일수·최신 mtime·전체 바이트·원장 mtime·원장 바이트) 가
# 들어 있어 파일이 하나라도 바뀌면 자동 무효화된다. 캐시가 없으면 매 요청 30MB 본문을
# 다시 읽고 해시한다. 크기를 함께 보는 이유는 _scan() 주석에 있다 — mtime 만으로는
# 파일시스템 시계 해상도(223 실측 4ms) 안에서 일어난 두 번째 기록을 놓친다.
# 값 = (후보 행, doc_id → 메타데이터의 candidate_status). 뒤의 것은 후보 행에 없는 값이다 —
# 행의 status 는 결정이 있으면 결정의 것으로 덮인다. 숨김 검수(golden_reviewer_access)가 "내가
# 아직 결정하지 않은 문서"의 원래 상태를 보이려면 필요하다. 행에 새 키를 얹지 않고 캐시 옆에 둔 것은
# 행이 모든 응답에 그대로 나가기 때문이다(키를 얹으면 손잡이를 끈 응답도 바뀐다).
#
# [2026-09-25] 후보가 3,598건(파일 7천 개)이 되자 이 캐시 구조가 무너졌다 — 실측(Docker Desktop
# 바인드 마운트): ① 캐시 키를 만드는 디렉터리 전수 stat 이 **요청마다** 3.6초, ② 결정 한 번(원장이
# 바뀜)마다 캐시가 통째로 무효화돼 다음 요청이 7천 파일을 다시 읽고 해시하는 데 **30초**. 결정
# 하나 저장하고 다음 화면을 여는 데 30초가 걸리고, gunicorn 타임아웃(60초)에 닿을 수도 있었다.
# 그래서 셋으로 나눈다:
#   _BASE            파일에서 온 부분(행 본문·해시·메타). 파일이 바뀐 행만 다시 만든다.
#   _CANDIDATE_CACHE 기반 + 원장의 결정 덮어쓰기. 원장이 바뀌면 이것만 다시 만든다(밀리초).
#   디렉터리 재조회  _SCAN_TTL_SECONDS 마다 한 번(그 사이엔 디렉터리 mtime 한 번만 본다).
_SCAN_TTL_SECONDS = 30.0
_LOAD_THREADS = 16


class _BaseSnapshot:
    """파일에서 온 후보 행(결정 덮어쓰기 전). meta 파일명 → 행."""

    __slots__ = ("sigs", "rows", "status_of", "order", "revision_metas", "at", "dir_mtime", "version",
                 "base_status")

    def __init__(self) -> None:
        self.sigs: dict[str, tuple[int, int]] = {}
        self.rows: dict[str, dict[str, Any]] = {}
        self.status_of: dict[str, str] = {}
        self.order: list[str] = []
        self.revision_metas: set[str] = set()
        self.at = 0.0
        self.dir_mtime = 0
        self.version = 0
        self.base_status: dict[str, str] = {}


_BASE: dict[str, _BaseSnapshot] = {}
_BASE_LOCK = threading.RLock()


class _CandidateCache(dict):
    """결정 덮어쓴 후보 캐시. `.clear()` 가 기반 캐시(_BASE)까지 비운다.

    시험과 운영 도구가 "재시작"을 흉내 낼 때 이 한 줄만 부르므로, 파생 캐시가 남아 옛 파일 내용을
    돌려주지 않게 여기서 함께 비운다. 내부 세대 정리는 `dict.clear(_CANDIDATE_CACHE)` 를 쓴다.
    """

    def clear(self) -> None:  # noqa: D401
        super().clear()
        with _BASE_LOCK:
            _BASE.clear()
        _QUALITY_MEMO.clear()
        _EXPOSURE_MEMO.clear()


_CANDIDATE_CACHE: dict[tuple, tuple[list[dict[str, Any]], dict[str, str]]] = _CandidateCache()
# 품질 지표 메모 — 후보 목록 객체(캐시 리스트 자체)가 같으면 결과도 같다. 요청마다 3천 건 본문을
# 정규식으로 훑는 데 0.5초가 든다.
_QUALITY_MEMO: dict[tuple[int, str], tuple[list[dict[str, Any]], dict[str, Any]]] = {}
# 본문 등급 노출 검사 결과 — 키 (본문 sha256, 실문서 여부). 결정이 바뀌어도 본문은 안 바뀌므로 결정 때마다
# 3천 건 본문을 정규식으로 다시 훑을 이유가 없다(실측: 결정 직후 첫 목록 조회가 0.55초였다).
_EXPOSURE_MEMO: dict[tuple[str, bool], bool] = {}

# 요청자별 시야(2026-09-21 숨김 검수). 후보 행 목록 / 원장 이벤트 목록을 받아 **그 요청자가 봐도 되는 것**으로
# 바꿔 돌려주는 함수다. 서비스는 이 함수를 집계·필터보다 **앞에** 끼울 뿐 내용은 모른다(내용은
# golden_reviewer_access). None 이면 종전 그대로다.
CandidateView = Callable[[list[dict[str, Any]]], list[dict[str, Any]]]
EventView = Callable[[list[dict[str, Any]]], list[dict[str, Any]]]
# 목록 검색어(query)가 후보 행에 맞는지 정하는 함수 (행, 소문자로 접은 검색어) → 일치 여부. None 이면 종전 규칙
# (doc_id·title 부분 일치)이다. 숨김 검수(golden_reviewer_access)가 doc_id·제목에 든 등급 코드를 캐지 못하게 바꿔 끼운다.
QueryMatch = Callable[[dict[str, Any], str], bool]


def _default_query_match(row: dict[str, Any], needle: str) -> bool:
    return needle in row["doc_id"].lower() or needle in row["title"].lower()
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
        department: str | None = None, info_type: str | None = None,
        visible_doc_ids: AbstractSet[str] | None = None,
        view: CandidateView | None = None,
        query_match: QueryMatch | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> dict[str, Any]:
        # [2026-09-25] limit/offset — 후보가 3,598건이 되자 한 응답이 3.6MB 였다(화면이 매번 통째로
        # 받아 3천 줄을 그렸다). None = 종전처럼 전부(다른 호출자 호환). total·summary·batch_summary·
        # available_batches 는 **잘라내기 전** 기준이다 — 쪽을 넘겨도 숫자가 흔들리면 안 된다.
        all_candidates = self._candidates()
        # [2026-09-21] 검수자 배정 강제 — 볼 수 있는 문서로 **먼저** 좁힌다. 그 뒤의 모든 계산
        # (목록·summary·품질·batch_summary·available_batches)이 이 좁혀진 집합 위에서 돌기
        # 때문에, 배정 안 된 문서의 등급 분포·배치 이름·건수가 집계로 새지 않는다.
        # None = 제한 없음(관리자·강제 꺼짐). 빈 집합은 "아무것도 안 보임"이다 — 둘을 뭉치지 말 것.
        if visible_doc_ids is not None:
            all_candidates = [c for c in all_candidates if c["doc_id"] in visible_doc_ids]
        # [2026-09-21] 숨김 검수 — 행을 **요청자의 시야로 바꾼 뒤에** 걸러 세야 한다. 상태 필터·폐기 제외·
        # 요약·batch_summary 가 모두 행의 status 를 읽으므로, 나중에 바꾸면 "다른 검수자가 폐기한 문서가
        # 내 목록에서 사라진다" · "?status=approved_proxy 로 남이 결정한 문서만 좁혀진다" 가 그대로 남는다.
        # None = 종전 그대로(관리자·강제 꺼짐).
        if view is not None:
            all_candidates = view(all_candidates)
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
        if department:
            candidates = [c for c in candidates if c["department"] == department]
        if info_type:
            candidates = [c for c in candidates if c["info_type"] == info_type]
        # [검수 배치] 콘솔 전체가 306건인데 이번 검수 대상은 그중 120건이다. 표식이
        # 없으면 검수자가 어느 문서를 봐야 하는지 알 수 없다(실측 2026-08-14: 적재만
        # 해 놓고 배포했으면 검수자가 306건 앞에서 멈췄을 자리다).
        if review_batch:
            candidates = [c for c in candidates if c.get("review_batch") == review_batch]
        if query:
            needle = query.strip().lower()
            if needle:
                match = query_match or _default_query_match
                candidates = [c for c in candidates if match(c, needle)]
        candidates.sort(key=lambda c: c["doc_id"])
        # 화면은 목록 응답에 실린 summary 로 KPI·품질 지표를 그린다(별도 /summary 를 안 부른다).
        # quality 를 여기 빼먹으면 품질 패널이 "지표를 낼 수 없습니다"로만 뜬다(실측).
        # [2026-09-25] 배치를 골랐으면 KPI·품질도 그 배치 기준이다. 종전에는 KPI 를 원장 전량으로 고정해서
        # (B1-3: 상태·등급 필터마다 숫자가 흔들리면 안 된다는 이유) 이번 회차 1,731건을 골라 놓고도 화면 카드에는
        # "전체 후보 3,598건 · 미확정 3,598건"이 떠 사용자가 "왜 3,598건이냐"고 되물었다. 배치는 필터가 아니라
        # 범위(어느 회차를 검수하나)라서 히어로의 진행률(batch_summary)과 같은 기준으로 맞춘다. 원장 전량은
        # ledger_total 로 함께 준다 — 화면이 "원장 전량 N건 중"으로 밝힌다.
        kpi_base = all_candidates
        kpi_scope = "all" if visible_doc_ids is None else "assigned"
        if review_batch:
            kpi_base = [c for c in all_candidates if c.get("review_batch") == review_batch]
            kpi_scope = "batch"
        embedded = self._summary(kpi_base)
        if kpi_base is all_candidates:
            embedded["quality"] = self._quality_memo(all_candidates)
        else:
            embedded["quality"] = self._quality_memo(kpi_base, base=all_candidates, tag=review_batch or "")
        embedded["ledger_total"] = len(all_candidates)
        # [B1-3] KPI(summary)는 **원장 전량** 기준으로 둔다 — 화면 상단 숫자가 필터마다
        # 흔들리면 무엇을 세는 값인지 알 수 없다. 대신 목록 건수(total)와 다르다는 사실을
        # 응답에 적어, 화면이 "전체 306 / 목록 300 (폐기 6 제외)" 처럼 읽히게 한다.
        # 배정 강제로 좁혀진 요청이면 "all"(원장 전량)이 거짓이 된다 — 기준을 정직하게 적는다.
        embedded["scope"] = kpi_scope
        page = candidates if limit is None else candidates[max(0, offset): max(0, offset) + limit]
        return {
            "total": len(candidates),
            "offset": max(0, offset) if limit is not None else 0,
            "limit": limit,
            "returned": len(page),
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
            "candidates": [{k: v for k, v in c.items() if k != "text"} for c in page],
        }

    def _quality_memo(
        self, candidates: list[dict[str, Any]], *, base: list[dict[str, Any]] | None = None, tag: str = "",
    ) -> dict[str, Any]:
        """`_quality` 를 후보 목록 객체 단위로 기억한다. 캐시된 목록 그 자체(관리자·필터 없음)일
        때만 맞는다 — 배정·숨김으로 걸러진 요청은 매번 새 리스트라 그냥 계산한다.

        base·tag — 배치로 좁힌 부분집합은 요청마다 새 리스트라 identity 로 못 알아본다. 그 부분집합이
        나온 원본 리스트(base)와 배치 이름(tag)으로 기억한다(원본이 바뀌면 identity 가 달라져 자동 무효)."""
        ref = candidates if base is None else base
        key = (id(ref), tag)
        hit = _QUALITY_MEMO.get(key)
        if hit is not None and hit[0] is ref:
            return hit[1]
        quality = self._quality(candidates)
        if len(_QUALITY_MEMO) > 8:
            _QUALITY_MEMO.clear()
        _QUALITY_MEMO[key] = (ref, quality)
        return quality

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

    def summary(
        self, *, visible_doc_ids: AbstractSet[str] | None = None, view: CandidateView | None = None,
    ) -> dict[str, Any]:
        candidates = self._candidates()
        if visible_doc_ids is not None:     # 의미는 list_candidates 주석과 같다
            candidates = [c for c in candidates if c["doc_id"] in visible_doc_ids]
        if view is not None:
            candidates = view(candidates)
        out = self._summary(candidates)
        out["quality"] = self._quality_memo(candidates)
        return out

    def category_stats(
        self, *, review_batch: str | None = None,
        visible_doc_ids: AbstractSet[str] | None = None, view: CandidateView | None = None,
    ) -> dict[str, Any]:
        """부서×정보유형×등급별 건수 — admin.html "데이터 생성 현황"이 쓴다.

        department/info_type 이 없는 후보(보강 전 옛 적재분)는 "미분류"로 묶는다 — 조용히
        빠뜨리면 "가이드별 합계"가 전체 후보 수보다 작아져 어디로 샜는지 알 수 없다.
        """
        candidates = self._candidates()
        if review_batch:
            candidates = [c for c in candidates if c.get("review_batch") == review_batch]
        if visible_doc_ids is not None:
            candidates = [c for c in candidates if c["doc_id"] in visible_doc_ids]
        if view is not None:
            candidates = view(candidates)

        counts: dict[tuple[str, str, str], int] = {}
        for c in candidates:
            dept = c.get("department") or "미분류"
            info = c.get("info_type") or "미분류"
            grade = c.get("final_grade") or c.get("proposed_grade") or "미정"
            key = (dept, info, grade)
            counts[key] = counts.get(key, 0) + 1

        rows = [
            {"department": dept, "info_type": info, "grade": grade, "count": n}
            for (dept, info, grade), n in sorted(counts.items())
        ]
        return {
            "total": len(candidates),
            "rows": rows,
            "departments": sorted({r["department"] for r in rows}),
            "info_types": sorted({r["info_type"] for r in rows}),
        }

    def export_rows(
        self, *, doc_ids: list[str] | None = None,
        status: str | None = None, grade: str | None = None, origin: str | None = None,
        review_batch: str | None = None, department: str | None = None, info_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """엑셀 내보내기용 평평한 행 하나당 문서 하나. doc_ids 가 있으면(선택 내보내기) 그
        문서들만, 없으면 필터로 좁힌 전체(필터 내보내기)를 쓴다 — 둘 중 하나다.

        admin.html 에서만 쓴다(검수자 배정·블라인드를 거치지 않고 항상 전체 시야) — 관리자는
        원래 제한이 없으므로 여기서 따로 좁힐 이유가 없다.
        """
        if doc_ids is not None:
            wanted = set(doc_ids)
            candidates = [c for c in self._candidates() if c["doc_id"] in wanted]
        else:
            listing = self.list_candidates(
                status=status, grade=grade, origin=origin, review_batch=review_batch,
                department=department, info_type=info_type, limit=None,
            )
            candidates = listing["candidates"]

        rows: list[dict[str, Any]] = []
        for c in candidates:
            decision = c.get("latest_decision") or {}
            rows.append({
                "doc_id": c["doc_id"],
                "department": c.get("department") or "",
                "info_type": c.get("info_type") or "",
                "status": c.get("status") or "",
                "proposed_grade": c.get("proposed_grade") or "",
                "final_grade": c.get("final_grade") or "",
                "reviewer": decision.get("actor_id") or "",
                "reason": decision.get("reason") or "",
                "decided_at": decision.get("decided_at") or "",
            })
        return rows

    def review_batch_index(self) -> dict[str, str | None]:
        """doc_id → review_batch(표식 없으면 None). 본문·등급은 싣지 않는다.

        검수 배정이 배치 단위로도 걸리므로(golden_reviewer_access) 배치가 어느 문서를 덮는지
        알아야 한다. 후보 목록 캐시를 그대로 읽으므로 추가 디렉터리 스캔이 없다.
        """
        return {c["doc_id"]: c.get("review_batch") for c in self._candidates()}

    def ledger_rows(self) -> list[dict[str, Any]]:
        """결정 원장의 모든 줄을 원장 순서로 읽는다(JSON 으로 읽히지 않는 줄은 건너뜀). 읽기 전용."""
        rows: list[dict[str, Any]] = []
        if self.ledger_path.exists():
            for line in split_lines(self.ledger_path.read_text(encoding="utf-8")):
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return rows

    def recent_decisions(
        self, limit: int = 100, *,
        visible_doc_ids: AbstractSet[str] | None = None,
        view: EventView | None = None,
    ) -> dict[str, Any]:
        """결정 원장 최근 기록 — 보류·폐기·번복까지 그대로 보인다.

        화면에는 문서 하나를 골라야 이력이 보였다. 보류·폐기가 왜 그렇게 됐는지 훑어보려면
        문서를 일일이 열어야 해서, 감사 목적으로는 쓸 수 없었다.

        [2026-09-21] 검수자 접근 통제용 두 인자(모두 기본값 = 종전 동작).
          visible_doc_ids  이 문서의 이벤트만 센다. total·by_action 도 **걸러낸 뒤** 기준이다 —
                           걸러내기 전 값을 싣으면 배정 안 된 문서의 활동량이 새고, limit 을 먼저
                           자르면 남의 이벤트가 내 이벤트를 밀어낸다.
          view             이벤트 목록(원장 순서, 위 필터 적용 뒤)을 요청자가 봐도 되는 이벤트로 바꾼다
                           (숨김 검수: 자기 이벤트만, 그것도 M 입력은 자기가 적은 값으로만).
                           **집계(total·by_action)와 limit 은 바꾼 뒤** 기준이다.
        """
        events = self.ledger_rows()
        if visible_doc_ids is not None:
            events = [e for e in events if str(e.get("doc_id") or "") in visible_doc_ids]
        if view is not None:
            events = view(events)
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

        def _exposed(c: dict[str, Any]) -> bool:
            is_real = bool(c.get("is_actual_document"))
            sha = c.get("document_sha256")
            if not sha:                                  # 행을 손으로 만든 호출(시험 등) — 기억하지 않는다
                return _exposes_grade(c.get("text") or "", is_real=is_real)
            key = (str(sha), is_real)
            hit = _EXPOSURE_MEMO.get(key)
            if hit is None:
                hit = _exposes_grade(c.get("text") or "", is_real=is_real)
                _EXPOSURE_MEMO[key] = hit
            return hit

        exposed = sum(1 for _ln, _g, c in graded if _exposed(c))
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
                self._expire_scan()      # 메타가 바뀌었다 — 다음 요청이 그 행만 다시 만든다
            with self.ledger_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(dumps_line(event, sort_keys=True) + "\n")
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
            self._expire_scan()          # 메타가 바뀌었다 — 다음 요청이 그 행만 다시 만든다
            with self.ledger_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(dumps_line(event, sort_keys=True) + "\n")
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
            raise ValueError("extracted text is too short; source file review is required")

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
        self._expire_scan()              # 새 후보 파일이 생겼다 — 다음 요청이 그 행을 추가한다
        candidate = self.get_candidate(doc_id)
        assert candidate is not None
        return candidate

    def _scan(self) -> tuple[dict[str, tuple[int, int]], list[str], dict[str, list[str]]]:
        """디렉터리를 **한 번만** 훑어 (파일별 서명, metadata 목록, doc_id→본문파일) 을 만든다.

        종전에는 후보마다 `glob(f"{doc_id}_*.md")` 를 돌려 2,440개 엔트리 디렉터리를 272번
        재스캔했고(바인드 마운트에서 O(N×M)), 목록 응답에서 버릴 본문 30MB 를 매 요청 읽었다.
        실측: /golden/candidates 와 /summary 가 **120초 타임아웃**.

        서명 = 파일별 (mtime_ns, 바이트 크기). 크기를 함께 보는 이유 — 파일시스템 시계 해상도가
        낮으면(223 실측 4ms) mtime 만으로는 같은 틱 안의 두 번째 기록을 놓친다.
        [2026-09-25] 이 훑기는 후보 3,598건(파일 7천 개)에서 바인드 마운트 기준 3.6초다. 그래서
        요청마다 부르지 않고 `_base_snapshot()` 이 TTL 로 묶는다.
        """
        metas: list[str] = []
        docs: dict[str, list[str]] = {}
        sigs: dict[str, tuple[int, int]] = {}
        with os.scandir(self.root) as it:
            for entry in it:
                if not entry.is_file():
                    continue
                name = entry.name
                try:
                    stat = entry.stat()
                    sigs[name] = (stat.st_mtime_ns, stat.st_size)
                except OSError:
                    sigs[name] = (0, 0)
                if name.endswith(".metadata.json"):
                    metas.append(name)
                elif name.endswith(".md") and "_" in name:
                    docs.setdefault(name.split("_", 1)[0], []).append(name)
        metas.sort()
        return sigs, metas, docs

    def _ledger_key(self) -> tuple[int, int]:
        """원장의 (mtime_ns, 크기). 원장은 append-only 라 크기가 반드시 커진다 — 시계 해상도와 무관하다."""
        try:
            stat = self.ledger_path.stat()
            return stat.st_mtime_ns, stat.st_size
        except OSError:
            return 0, -1

    def _expire_scan(self) -> None:
        """이 프로세스가 후보 파일을 직접 썼을 때 다음 요청이 곧바로 다시 훑게 한다(TTL 을 건너뜀)."""
        with _BASE_LOCK:
            snap = _BASE.get(str(self.root))
            if snap is not None:
                snap.at = 0.0

    def _candidates(self) -> list[dict[str, Any]]:
        return self._load_candidates()[0]

    def base_status_index(self) -> dict[str, str]:
        """doc_id → 메타데이터의 candidate_status(결정이 덮기 **전**의 상태). 후보 행에는 없는 값이다.

        숨김 검수(golden_reviewer_access)가 쓴다 — 다른 검수자가 결정한 문서가 내가 아직 결정하지 않은
        문서로 보이려면 그 문서의 원래 상태를 알아야 한다. 후보 캐시와 같은 키로 함께 만들어져 어긋나지 않는다.
        """
        return self._load_candidates()[1]

    def _base_snapshot(self) -> _BaseSnapshot:
        """파일에서 온 후보 행. 파일이 바뀐 행만 다시 만들고, 디렉터리 재조회는 TTL 로 묶는다."""
        root = str(self.root)
        try:
            dir_mtime = self.root.stat().st_mtime_ns
        except OSError:
            dir_mtime = 0
        snap = _BASE.get(root)
        if (snap is not None and snap.dir_mtime == dir_mtime
                and time.monotonic() - snap.at < _SCAN_TTL_SECONDS):
            return snap
        with _BASE_LOCK:
            snap = _BASE.get(root)
            now = time.monotonic()
            if (snap is not None and snap.dir_mtime == dir_mtime
                    and now - snap.at < _SCAN_TTL_SECONDS):
                return snap
            sigs, metas, docs = self._scan()
            snap = self._reconcile(snap, sigs, metas, docs, dir_mtime, now)
            _BASE[root] = snap
            return snap

    def _reconcile(
        self, old: _BaseSnapshot | None, sigs: dict[str, tuple[int, int]], metas: list[str],
        docs: dict[str, list[str]], dir_mtime: int, now: float,
    ) -> _BaseSnapshot:
        """새로 훑은 결과를 이전 스냅샷과 견줘, 파일이 바뀐 후보의 행만 다시 만든다."""
        if old is None:
            snap = _BaseSnapshot()
            todo = list(metas)
        else:
            changed = {n for n, s in sigs.items() if old.sigs.get(n) != s}
            changed |= set(old.sigs) - set(sigs)
            if not changed:
                old.at, old.dir_mtime = now, dir_mtime
                return old
            meta_set = set(metas)
            gone_metas = [m for m in old.rows if m not in meta_set]
            suffix = ".metadata.json"
            prefixes = {n.split("_", 1)[0] for n in changed if n.endswith(".md") and "_" in n}
            todo = [
                m for m in metas
                if m in changed                        # 메타가 새로 생기거나 바뀜
                or m[: -len(suffix)] in prefixes       # 그 후보의 본문 파일이 바뀜
                or m not in old.rows                   # 전에 건너뛴 것 — 다시 시도
                or m in old.revision_metas             # 개정본 경로는 하위 폴더일 수 있어 서명을 못 봄
            ]
            if not todo and not gone_metas:
                # 바뀐 것은 원장·잠금 파일뿐이다(결정마다 바뀐다) — 후보 행은 그대로라 버전도 안 올린다.
                old.sigs, old.at, old.dir_mtime = sigs, now, dir_mtime
                return old
            snap = _BaseSnapshot()
            snap.rows = dict(old.rows)
            snap.status_of = dict(old.status_of)
            snap.revision_metas = set(old.revision_metas)
            snap.version = old.version
            for gone in gone_metas:
                snap.rows.pop(gone, None)
                snap.status_of.pop(gone, None)
                snap.revision_metas.discard(gone)
        results = self._build_rows(todo, docs)
        for meta_name, built in zip(todo, results):
            if built is None:
                snap.rows.pop(meta_name, None)
                snap.status_of.pop(meta_name, None)
                snap.revision_metas.discard(meta_name)
                continue
            row, base_status, has_revision = built
            snap.rows[meta_name] = row
            snap.status_of[meta_name] = base_status
            if has_revision:
                snap.revision_metas.add(meta_name)
            else:
                snap.revision_metas.discard(meta_name)
        snap.sigs = sigs
        snap.order = [m for m in metas if m in snap.rows]
        snap.base_status = {snap.rows[m]["doc_id"]: snap.status_of[m] for m in snap.order}
        snap.at, snap.dir_mtime = now, dir_mtime
        snap.version += 1
        return snap

    def _build_rows(self, metas: list[str], docs_by_id: dict[str, list[str]]) -> list:
        """메타 파일 여러 개를 병렬로 읽어 행으로 만든다 — 파일 하나당 지연이 큰 바인드 마운트에서
        직렬로 읽으면 3,598건에 30초가 걸렸다."""
        if not metas:
            return []
        if len(metas) < 8:
            return [self._build_row(m, docs_by_id) for m in metas]
        with ThreadPoolExecutor(max_workers=_LOAD_THREADS) as pool:
            return list(pool.map(lambda m: self._build_row(m, docs_by_id), metas))

    def _load_candidates(self) -> tuple[list[dict[str, Any]], dict[str, str]]:
        if not self.root.exists():
            return [], {}
        snap = self._base_snapshot()
        key = (str(self.root), snap.version, self._ledger_key())
        cached = _CANDIDATE_CACHE.get(key)
        if cached is not None:
            return cached
        latest = self._latest_decisions()
        rows = []
        for meta_name in snap.order:
            base = snap.rows[meta_name]
            decision = latest.get(base["doc_id"], {})
            row = dict(base)
            # 결정이 덮는 네 칸. 기반 행에는 자리표시만 있고(키 순서를 지키려고) 여기서 채운다.
            row["final_grade"] = decision.get("final_grade")
            row["status"] = decision.get("status") or snap.status_of[meta_name]
            row["latest_decision"] = decision or None
            row["grade_fixed"] = bool(decision.get("final_grade"))
            rows.append(row)
        result = (rows, dict(snap.base_status))
        # 폭주를 막기 위해 최근 몇 세대만 유지한다.
        if len(_CANDIDATE_CACHE) > 4:
            dict.clear(_CANDIDATE_CACHE)
        _CANDIDATE_CACHE[key] = result
        return result

    def _build_row(
        self, meta_name: str, docs_by_id: dict[str, list[str]],
    ) -> tuple[dict[str, Any], str, bool] | None:
        """메타 파일 하나 → (기반 행, 메타의 candidate_status, 개정본 경로 사용 여부). 건너뛰면 None."""
        meta_path = self.root / meta_name
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            # 후보가 목록에서 조용히 사라지던 자리. 검수자는 "왜 안 보이지"를
            # 알 방법이 없었다. 건너뛰는 동작은 그대로 두고 사실만 남긴다.
            logger.warning("골든 후보 건너뜀 — 메타를 읽지 못함: %s (%s: %s)",
                           meta_name, type(exc).__name__, exc)
            return None
        doc_id = str(meta.get("doc_id") or "")
        if not doc_id:
            logger.warning("골든 후보 건너뜀 — 메타에 doc_id 가 없음: %s", meta_name)
            return None
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
                return None
            source = self.root / names[0]
        try:
            text = source.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("골든 후보 건너뜀 — 본문을 읽지 못함: doc_id=%s path=%s (%s)",
                           doc_id, source.name, type(exc).__name__)
            return None
        base_status = str(meta.get("candidate_status") or "proposed")
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
        row = {
            "doc_id": doc_id,
            # [2026-09-27] document_type 는 "사실우선 모의문서(R7)" 처럼 생성 회차 표식이지
            # 문서별 제목이 아니다 — 사실우선 1~9차 배치 1,711건이 title 값 9종(R1~R9)뿐이라
            # 목록에서 문서를 구분할 수 없었다(실측: 실치설치 리허설 콘솔 화면). 본문 첫 줄은
            # 문서마다 실제 제목이라(예: "법무실 새 식구를 위한 안내 — 분쟁 비용 자료 편") 별도
            # title 메타 필드를 우선 쓴다. 없는 후보(옛 배치)는 document_type 로 그대로 물러난다.
            "title": str(meta.get("title") or meta.get("document_type") or source.stem),
            "proposed_grade": proposed,
            "proposed_grade_basis": proposed_basis,
            "final_grade": None,                    # 결정 덮어쓰기에서 채운다(_load_candidates)
            "status": base_status,                  # 위와 같다
            "document_origin": document_origin,
            # [2026-10-02] 부서·정보유형 — 가이드별 생성 현황·카테고리 조회(admin.html)가 쓴다.
            # 원본 생성 manifest 에는 이미 분리된 필드로 있었는데 적재 스크립트가 표시용 문자열
            # (document_type)에 뭉쳐 넣기만 하고 따로는 안 날랐다. 없는 후보(이 필드가 없던
            # 적재분)는 None — 조회·통계에서는 "미분류"로 묶인다.
            "department": str(meta.get("department") or "") or None,
            "info_type": str(meta.get("info_type") or "") or None,
            "requires_manual_audit": bool(meta.get("requires_manual_audit")),
            # 검수 배치 표식. 전달본 단위로 묶어 목록을 좁힌다.
            "review_batch": str(meta.get("review_batch") or "") or None,
            "claim_scope": str(meta.get("claim_scope") or ""),
            "document_path": document_path,
            "content_revision": str(meta.get("content_revision") or "v1"),
            "characters": len(text),
            "document_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "latest_decision": None,                # 결정 덮어쓰기에서 채운다
            "grade_fixed": False,                   # 위와 같다
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
        }
        return row, base_status, bool(revision)

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
        for line in split_lines(self.ledger_path.read_text(encoding="utf-8")):
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
        for line in split_lines(self.ledger_path.read_text(encoding="utf-8")):
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(row.get("doc_id") or "") == doc_id:
                events.append(row)
        return events
