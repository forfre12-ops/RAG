"""골든셋 검수자 접근 통제 — 전문가별 검수 배정 + 제안 등급 숨김 (2026-09-21).

왜 만들었나. 2026-09-20 보호원 협의에서 골든셋을 외부 전문가(변호사·교수·포렌식)가 우리 검수
사이트로 검수하기로 확정됐다. 그런데 종전 코드는 두 가지가 독립 검수에 맞지 않았다.

  1. reviewer 역할이면 **모든 문서**의 목록·상세·결정에 닿는다(목록 필터 review_batch 는 화면용).
  2. 검수 화면이 제안 등급과 '제안 등급 그대로 확정' 버튼을 보여 준다. 앵커링(먼저 본 값에 판단이
     쏠리는 현상)이 생기면 두 전문가가 같은 답을 낸 것이 독립 판정이라고 말할 수 없다.

두 손잡이(config.py) — 둘 다 **기본 False = 종전 동작 그대로**.

  golden_reviewer_assignment_enforced   reviewer 는 자기에게 배정된 문서만 본다
  golden_review_blind_enforced          reviewer 는 제안 등급·근거와 **자기 자신의 결정에서 온 정보만** 본다
                                        (남의 결정·M 입력·신원·"남이 결정했다"는 사실은 서버가 뺀다 — BlindView)

관리자(admin)·kl_backend·system 역할은 손잡이와 무관하게 **항상 전체**를 본다.

저장은 DB 가 아니라 결정 원장과 같은 폴더의 append-only 파일 `candidate_assignments.jsonl` 이다
(같은 잠금 방식). 배정을 바꿔도 기존 원장·후보 파일·필드는 건드리지 않는다.

이 모듈에서 **강제가 일어나는 자리는 두 곳뿐**이다 — `resolve_scope`(누가 무엇을 볼 수 있나)와
`enforcement_active`(손잡이가 켜져 있나). 라우트는 이 결과를 받아 쓰기만 하므로, 시험이 이 둘을
무력화해 보면 격리 시험이 정말 격리를 잠그는지 확인할 수 있다(test_golden_reviewer_assignment).

숨김의 방식(2026-09-21 독립 검증 뒤 고침). 저장된 값(후보 행의 status·management, 이벤트의
management_before/after)에는 남의 결정이 섞여 있어, 키를 하나씩 막으면 새 통로가 남는다. 그래서 검수자마다
**원장을 자기 이벤트만으로 다시 재생한 시야**(BlindView)를 만들고, 서비스가 그 시야를 집계·필터 **앞에** 끼운다
(list_candidates·summary 의 view, recent_decisions 의 view). 원장·후보 파일·저장 형식은 읽기만 한다.

별칭 ID 계층(2026-09-22). 위 숨김이 제안 등급을 가려도 **doc_id 자체가 답을 말하는** 문서가 있다 — 실제 후보 풀
1,067건 중 988건의 doc_id 가 GOLD-B1-S1-036 처럼 등급 코드를 품고(그 코드는 988/988건이 제안 등급과 일치한다),
목록 정렬이 doc_id 순이라 **줄 위치만으로도 등급 묶음이 갈린다**(이웃 문서가 같은 제안 등급인 비율 0.9606, 라벨을
섞으면 0.2608 — 2026-09-22 측정, scratchpad/i/measure_pool.json). 그래서 숨김이 켜진 동안 검수자에게 나가는 모든
doc_id 를 서버가 **불투명 별칭**(`RV-` + 16자)으로 바꾼다:

  별칭 = 'RV-' + Crockford-base32( HMAC-SHA256(솔트, NFC(doc_id)) )[:16]
  솔트 = 후보 폴더의 `reviewer_alias.salt` — 처음 필요할 때 무작위 32바이트로 원자적으로 만들고(동시 생성 경합에서는
         하나만 살아남고 나머지는 그것을 읽는다) 재시작해도 같은 별칭이 나온다. 설정 키·토큰을 새로 요구하지 않는다.

별칭을 만드는 자리는 두 곳뿐이다 — `alias_for`(값을 만듦)와 `AliasMap`(풀 전체 기준 충돌 검사·역해석). 경로의
{doc_id} 에는 **별칭만** 받고 그 검수자에게 보이는 문서 안에서만 실 doc_id 로 푼다(`ReviewerScope.resolve_doc_id`) —
실 doc_id 를 넣으면 없는 문서와 같은 404 라 존재 여부·대응을 캐지 못한다. 솔트를 읽거나 만들지 못하거나 풀 안에서
별칭이 충돌하면 조용히 넘기지 않고 숨김 검수자의 요청을 503 으로 거절한다(fail-closed). 결정 원장·배정 원장·후보 파일
형식은 그대로다(원장에는 계속 실 doc_id 가 적힌다).
"""
from __future__ import annotations

import datetime as dt
import errno
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import AbstractSet, Any, Iterable, Mapping
from uuid import uuid4

from koipa.config import settings
from koipa.jsonl_lines import dumps_line
# 같은 잠금 방식을 그대로 쓴다 — 결정 원장이 이미 Windows(msvcrt)·POSIX(flock) 양쪽에서 검증한
# 잠금이다. 잠금 파일만 원장별로 따로 둔다(배정을 바꾸는 동안 결정 기록이 기다릴 이유가 없다).
from koipa.services.proxy_gold_candidate_service import (
    CandidateView, QueryMatch, _exclusive_ledger_lock, _exposes_grade, _management_view,
)

logger = logging.getLogger(__name__)

ASSIGNMENT_LEDGER_NAME = "candidate_assignments.jsonl"

# 후보 화면·목록이 항상 전체를 보는 역할. 여기에 없는 역할(= reviewer)이 제한 대상이다.
# '제한할 역할'을 나열하지 않고 '제한 안 할 역할'을 나열한다 — 나중에 새 역할이 이 라우트들에
# 들어와도 기본이 제한이라 조용히 전체를 보게 되는 일이 없다(fail-closed).
PRIVILEGED_ROLES = frozenset({"admin", "kl_backend", "system"})

EVENT_ASSIGN = "assign"
EVENT_UNASSIGN = "unassign"


# ── 숨김 모드의 분류표 ────────────────────────────────────────────────────────
# 원칙(2026-09-21 독립 검증 뒤 고침): **숨김 켠 검수자에게는 자기 자신의 결정에서 온 정보만 보인다.**
# 다른 검수자의 결정·M 입력·신원, 그리고 "다른 검수자가 결정했다"는 사실 자체가 응답 어디에도 없어야 한다 —
# 이벤트 안의 management_before, 문서의 status·grade_fixed, 집계의 fixed·by_status 까지 같은 원칙이다.
#
# 후보·이벤트·집계의 **모든 필드는 아래 넷 중 정확히 하나**에 속한다. tests/test_golden_blind_review.py 가
# 실제 응답의 키를 이 표와 대조한다 — 새 키가 생기면 "어느 쪽인가"를 정하기 전에는 시험이 실패한다.
# 그리고 응답을 만들 때는 이 표를 **허용 목록**으로 쓴다(표에 없는 키는 나가지 않는다 — fail-closed).
#
#   HIDDEN  서버가 뺀다. 제안·정답을 직접 말하거나 다른 검수자를 드러낸다.
#   OWN     그 검수자 **자신의 결정만으로 다시 계산한** 값이다. 저장된 값이 아니다(BlindView).
#   ALIASED 문서를 가리키는 값이라 서버가 **별칭으로 바꿔** 내보낸다(2026-09-22). 실 doc_id 는 응답 어디에도 없다.
#   KEPT    문서 자체의 사실(본문·출처·글자 수)이라 서버가 값을 바꿀 수 없고, 결정과 무관하다.
#           ⚠ 남긴다고 안전하다는 뜻이 아니다(한계는 test_known_limits_* 와 작업 보고서 risks).
#
# 별칭 대상은 후보 풀 전 필드를 훑은 측정으로 정했다(2026-09-22, 후보 1,067건·문자열 필드 전수 —
# scratchpad/i/measure_pool.json). 등급 코드(TS·S1·S2·S3)가 독립 낱말로 나오는 필드:
#   doc_id 988건(전부 제안 등급과 같은 코드) · title 1건(GOLD-UPL-…, 'real-S3-ipo-ksensor') ·
#   document_path 989건·proposed_grade(이미 HIDDEN) · 본문 text 는 정의에 따라 0건(`\b`)~964건(한글이 붙은 'S3로').
#   claim_scope·provenance.*·extraction.*·review_batch·management.*·content_revision 은 0건이다.
#   본문에 든 964건은 전부 'S3로 재검토할 수 있고' 라는 고정 문장의 S3 라서(S1 문서에도 똑같이 있다) 문서의 등급과
#   상관이 없다 — 문서 자체의 사실이라 손대지 않는다.
# title 은 **오염됐을 때만** 별칭으로 바꾼다: 자기 실 doc_id 가 들어 있으면 그 자리만 별칭으로 바꾸고, 그래도 등급 코드가
# 남으면(또는 합성 후보에서 등급명 어휘가 있으면) 제목 전체를 별칭으로 바꾼다 — 깨끗한 제목은 검수자가 문서를 알아보는
# 유일한 이름이라 그대로 둔다. 다만 doc_id·title 은 **등급 코드를 담을 수 있는 필드**라서 목록 검색(query)에서는 둘 다 뺀다
# (blind_query_match: 검색어는 별칭 앞부분에만 닿는다).
#
# ── 후보 (ProxyGoldCandidateService._candidates 가 만드는 dict + get_candidate 의 decision_history)
#  HIDDEN
#    proposed_grade         메타데이터 intended_label 그대로. 제안 등급이자 합성 문서의 정답.
#    proposed_grade_basis   "공개 실문서 → S3 제안" 이라는 제안의 근거 문구.
#    document_path          원본 파일 경로. 파일명이 doc_id·제목을 그대로 싣고, 정리본
#                           (revisions/…) 경로는 어떤 문서가 손질됐는지까지 드러낸다. 화면은 안 쓴다.
#  OWN — 자기 결정만으로 계산
#    final_grade·latest_decision·decision_history   자기 결정의 등급·이벤트·이력.
#    status·grade_fixed     자기가 아직 결정하지 않았으면 **결정이 없는 문서의 상태**(메타데이터의
#                           candidate_status)로 보이고, 결정했으면 자기 마지막 결정을 따른다.
#                           남의 결정이 status 에 실려 나가면 "누가 이미 결정했다"가 그대로 읽힌다.
#    management             M(보안표시·접근범위) 입력. 적재 때 들어온 값(문서의 사실)과 **자기가 적은 값**만.
#                           M 은 등급을 거의 단독으로 가른다(TS/S1 을 M 하나로 88.3% 가름) — 남이 적은 값이
#                           섞이면 앵커링이고, 값 대신 "가려졌다"는 표식을 내도 남이 적었다는 사실이 샌다.
BLIND_HIDDEN_CANDIDATE_FIELDS = frozenset({"proposed_grade", "proposed_grade_basis", "document_path"})
BLIND_OWN_ONLY_CANDIDATE_FIELDS = frozenset({
    "final_grade", "latest_decision", "decision_history", "status", "grade_fixed", "management",
})
BLIND_ALIASED_CANDIDATE_FIELDS = frozenset({"doc_id", "title"})
BLIND_KEPT_CANDIDATE_FIELDS = frozenset({
    "document_origin", "requires_manual_audit", "review_batch",
    "claim_scope", "content_revision", "characters", "document_sha256",
    "extraction", "provenance", "source_file_sha256", "is_actual_document", "text",
})

# ── 결정 이벤트 (원장 한 줄 = decide 가 적는 dict). 숨김 검수자가 보는 이벤트는 **자기 것뿐**이다.
#  HIDDEN     proposed_grade     이벤트에 복사돼 있는 제안 등급.
#             management_before  **결정 직전의 저장된 M** — 그 사이에 다른 검수자가 적은 값과 신원(recorded_by)이
#                                그대로 들어 있다(독립 검증 결함 1). 자기 이전 값을 따로 내지 않는다.
#  REBUILT    management_after   저장된 값은 '이번에 준 칸만 덮고 나머지는 이전 값(남이 적은 것 포함)을 이은'
#                                병합 결과다(결함 2). 자기 M 시야로 **다시 만든 값**을 낸다.
#  OWN        자기가 이 결정에서 낸 것 — 이벤트 id·행위·결과 상태·등급·사유·행위자(=본인)·시각.
#  ALIASED    doc_id — 원장에는 실 doc_id 가 적혀 있지만 응답에는 별칭이 나간다.
#  PUBLIC     결정 시점에 복사해 둔 **문서의 사실** — 해시·출처·범위 문구·스키마 버전.
BLIND_HIDDEN_EVENT_FIELDS = frozenset({"proposed_grade", "management_before"})
BLIND_REBUILT_EVENT_FIELDS = frozenset({"management_after"})
BLIND_OWN_EVENT_FIELDS = frozenset({
    "event_id", "action", "status", "final_grade", "reason", "actor_id", "decided_at",
})
BLIND_ALIASED_EVENT_FIELDS = frozenset({"doc_id"})
BLIND_PUBLIC_EVENT_FIELDS = frozenset({
    "schema_version", "event_kind", "document_sha256", "document_origin", "claim_scope",
    "provenance_at_decision",
})

# 관리자·kl_backend·system 의 후보 행·상세에 **숨김 손잡이가 켜진 동안만** 더하는 키 — 검수자가 화면에서 보는 번호(별칭)와
# 실 doc_id 를 대응시키려는 것이다. 검수자 응답에는 절대 나가지 않는다(위 허용 목록에 없다).
ADMIN_ALIAS_KEY = "reviewer_alias"

# ── 집계 (summary · list.summary · list.batch_summary)
#  HIDDEN  quality            길이·등급 분포를 **제안 등급 기준**으로 센다(길이가 등급의 대리변수인지 재는
#                             지표) — 등급별 길이 최소·중앙·최대를 주므로 그대로 두면 길이로 등급을 추정한다.
#          by_proposed_grade  제안 등급 분포 그 자체.
#          by_final_grade     확정 등급 분포. 자기 것만으로 세도 화면이 쓰지 않는 등급 지표라 뺀다.
#  OWN     결정을 세는 값 — 서비스가 **자기 시야로 바꾼 행 위에서** 센다(list_candidates·summary 의 view).
#          fixed·unfixed·deferred·discarded·out_of_scope·by_status·actual_grade_fixed_unlocked,
#          batch_summary 의 terminal·pending·deferred·by_status.
#  KEPT    문서 수·출처 분포·출처 기록 현황 — 결정과 무관하다.
BLIND_HIDDEN_SUMMARY_FIELDS = frozenset({"quality", "by_proposed_grade", "by_final_grade"})
BLIND_OWN_SUMMARY_FIELDS = frozenset({
    "fixed", "unfixed", "deferred", "discarded", "out_of_scope", "by_status",
    "actual_grade_fixed_unlocked",
})
BLIND_KEPT_SUMMARY_FIELDS = frozenset({
    "total", "by_origin", "actual_document_intake", "actual_provenance_recorded",
    "actual_provenance_partial", "actual_provenance_legacy", "scope",
})
BLIND_HIDDEN_BATCH_SUMMARY_FIELDS = frozenset({"by_final_grade"})
BLIND_OWN_BATCH_SUMMARY_FIELDS = frozenset({"terminal", "pending", "deferred", "by_status"})
BLIND_KEPT_BATCH_SUMMARY_FIELDS = frozenset({"total"})

# M 입력 칸. 결정 요청이 주는 값은 이 둘뿐이고(decide), 나머지 키(state·level·reason)는 이 둘에서 계산된다.
_M_INPUT_FIELDS = ("security_marking", "access_scope")


class ReviewerAccessError(Exception):
    """범위를 정할 수 없거나 허용되지 않는다. golden.py 가 HTTPException 으로 옮긴다
    (서비스 계층을 프레임워크에서 떼어 두려고 예외를 따로 둔다)."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def norm_id(value: object) -> str:
    """신원·doc_id 비교용 정규화 — NFC + 앞뒤 공백 제거.

    한글 sub 는 입력 경로에 따라 NFC/NFD 로 갈릴 수 있다(맥 파일명이 NFD). 눈에는 같은 이름인데
    바이트가 달라 배정이 안 맞으면 검수자가 "내 문서가 안 보인다"를 겪는다. 대소문자는 접지
    않는다 — ID 는 대소문자를 구분한다.
    """
    return unicodedata.normalize("NFC", str(value or "")).strip()


def enforcement_active() -> bool:
    """두 손잡이 중 하나라도 켜져 있나. 잡 화면 같은 무인증 진입로가 이 값으로 로그인을 요구한다."""
    return bool(
        getattr(settings, "golden_reviewer_assignment_enforced", False)
        or getattr(settings, "golden_review_blind_enforced", False)
    )


def _roles(auth: Mapping[str, Any]) -> set[str]:
    roles = set(auth.get("actor_roles") or ())
    if not roles and auth.get("actor_role"):
        roles = {auth["actor_role"]}
    return roles


# ── 별칭 ID 계층 ─────────────────────────────────────────────────────────────
# 검수자에게 나가는 문서 번호를 실 doc_id 대신 **불투명 별칭**으로 바꾼다(머리말 참조). 이 절이 별칭을 만드는 유일한
# 자리이고(`alias_for`), 풀 전체의 충돌 검사와 역해석은 `AliasMap` 이 한다.

ALIAS_PREFIX = "RV-"
ALIAS_BODY_CHARS = 16                     # 80비트 — 후보 풀(수천 건) 안에서 우연히 겹칠 확률은 무시할 수준이다
ALIAS_SALT_NAME = "reviewer_alias.salt"   # 후보 원장과 같은 폴더의 별도 파일
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"        # I·L·O·U 를 뺀 32자 — 사람이 옮겨 적어도 헷갈리지 않는다
_ALIAS_RE = re.compile(r"RV-[0-9ABCDEFGHJKMNPQRSTVWXYZ]{16}")
_SALT_HEX = re.compile(r"[0-9a-fA-F]{64}")                # 우리가 만드는 것은 소문자지만 관리자가 손으로 심은 대문자도 받는다
_SALT_WAIT_SECONDS = 2.0                  # 다른 프로세스가 솔트를 쓰는 중이면 이만큼까지 기다린다(직접 생성 경로에서만 생긴다)

# 제목 오염 판정 — 등급 코드가 독립 낱말로 있나. 저장소의 `_GRADE_TOKEN`(\b)은 한글이 붙은 'TS급'·'S3로' 를 놓치므로,
# 영문·숫자만 경계로 보고(밑줄·한글은 경계로 친다) 대소문자를 가리지 않는다.
_GRADE_CODE_ANYWHERE = re.compile(r"(?<![A-Za-z0-9])(?:TS|S1|S2|S3)(?![A-Za-z0-9])", re.IGNORECASE)


class AliasLayerError(ReviewerAccessError):
    """별칭 계층이 동작할 수 없다(솔트를 읽거나 만들 수 없음 · 풀 안에서 별칭 충돌). 숨김 검수자의 요청은 이 오류로 **거절**된다.

    검수자에게는 원인을 알리지 않는다(내부 구조를 알려 줄 이유가 없다). 원인은 서버 로그(ERROR)에 남는다.
    """

    def __init__(self, reason: str) -> None:
        logger.error("검수자 별칭 계층을 쓸 수 없어 숨김 검수 요청을 거절한다: %s", reason)
        super().__init__(
            503, "reviewer alias layer unavailable; blind review is closed until an administrator fixes it")
        self.reason = reason


def _crockford(data: bytes) -> str:
    """바이트열을 Crockford base32(대문자·패딩 없음)로. 앞에서부터 5비트씩 끊는다."""
    bits = len(data) * 8
    pad = (-bits) % 5
    n = int.from_bytes(data, "big") << pad
    bits += pad
    return "".join(_CROCKFORD[(n >> shift) & 31] for shift in range(bits - 5, -1, -5))


def alias_for(salt: bytes, doc_id: str) -> str:
    """실 doc_id → 별칭. `RV-` + Crockford-base32(HMAC-SHA256(솔트, NFC(doc_id)))의 앞 16자.

    NFC 로 접어 넣는다 — 같은 한글 doc_id 가 NFC/NFD 로 갈려 들어와도 같은 별칭이 나오게.
    """
    key = unicodedata.normalize("NFC", str(doc_id)).encode("utf-8")
    digest = hmac.new(salt, key, hashlib.sha256).digest()
    return ALIAS_PREFIX + _crockford(digest)[:ALIAS_BODY_CHARS]


def is_alias(value: object) -> bool:
    """별칭 모양인가. 응답 허용 목록이 마지막 관문으로 쓴다 — 별칭이 아닌 doc_id 는 나가지 않는다(fail-closed)."""
    return isinstance(value, str) and _ALIAS_RE.fullmatch(value) is not None


class _SaltNotReady(Exception):
    """솔트 파일이 있으나 아직 다 쓰이지 않았다(하드링크를 못 쓰는 파일시스템에서 다른 프로세스가 쓰는 중)."""


def _read_salt(path: Path) -> bytes | None:
    """솔트를 읽는다. 파일이 없으면 None. **형식이 틀린 파일은 새로 만들지 않고 오류다** — 조용히 다시 만들면 별칭이
    통째로 바뀌어 검수자가 보던 문서 번호가 사라진다."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise AliasLayerError(f"솔트 파일을 읽지 못함: {path} ({type(exc).__name__}: {exc})") from exc
    text = raw.decode("ascii", errors="replace").strip()
    if _SALT_HEX.fullmatch(text):
        return bytes.fromhex(text)
    if len(text) < 64 and re.fullmatch(r"[0-9a-fA-F]*", text):
        raise _SaltNotReady
    raise AliasLayerError(f"솔트 파일 형식이 틀림(64자리 16진수여야 함): {path}")


def _write_salt_directly(path: Path, text: str) -> None:
    """최후 수단 — 최종 경로를 배타 생성하고 그 자리에 쓴다(하드링크를 못 쓰는 파일시스템용). 이미 있으면 진 것이다."""
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return
    with os.fdopen(fd, "w", encoding="ascii", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())


def _create_salt_file(path: Path) -> None:
    """무작위 32바이트를 **다 쓴 임시 파일**을 하드링크로 최종 경로에 붙인다 — 완성된 내용이 한 번에 나타나고,
    이미 있으면(경합에서 짐) FileExistsError 로 실패하므로 하나만 살아남는다. 지면 조용히 돌아간다(읽으면 이긴 쪽 값)."""
    text = secrets.token_hex(32) + "\n"
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid4().hex}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="ascii", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(tmp, path)
        except FileExistsError:
            return                                  # 경합에서 졌다 — 이긴 쪽 솔트를 읽는다
        except OSError as exc:
            # 하드링크를 못 쓰는 파일시스템(일부 네트워크 마운트). 권한 오류도 여기로 오지만 아래 직접 쓰기가
            # 같은 오류로 실패하므로 그대로 AliasLayerError 가 된다.
            logger.info("솔트를 하드링크로 붙이지 못해 직접 만든다: %s (%s)", path, exc)
            _write_salt_directly(path, text)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def load_or_create_salt(root: Path | str) -> bytes:
    """후보 폴더의 솔트를 읽고, 없으면 무작위 32바이트로 **원자적으로** 만든다. 동시에 여럿이 부르면 하나만 만들고 나머지는
    그것을 읽는다 — 모두 같은 값을 돌려받는다. 읽지도 만들지도 못하면 AliasLayerError."""
    path = Path(root) / ALIAS_SALT_NAME
    deadline = time.monotonic() + _SALT_WAIT_SECONDS
    creations = 0
    while True:
        try:
            salt = _read_salt(path)
        except _SaltNotReady:
            if time.monotonic() > deadline:
                raise AliasLayerError(f"솔트 파일이 끝내 완성되지 않음(쓰다 멈춘 파일이면 지우고 다시 시도): {path}") from None
            time.sleep(0.02)
            continue
        if salt is not None:
            return salt
        creations += 1
        if creations > 10:          # 만들 때마다 누군가 지워 버리는 비정상 상태 — 끝없이 돌지 않는다
            raise AliasLayerError(f"솔트 파일을 만들었는데 계속 사라짐: {path}")
        try:
            _create_salt_file(path)
        except OSError as exc:
            if exc.errno == errno.EEXIST:
                continue
            raise AliasLayerError(f"솔트 파일을 만들지 못함: {path} ({type(exc).__name__}: {exc})") from exc
        # 솔트가 없어 새로 만들었다(동시 경합에서 진 쪽도 이 줄을 지난다). 처음 켤 때는 정상이지만, 이미 검수자가 별칭을 받아 간 뒤에
        # 파일이 사라진 것이라면 별칭이 전부 바뀌어 검수자가 메모해 둔 번호가 404 가 된다 — 조용히 넘기지 않고 남긴다
        # (독립 검증 2026-09-22 지적: 로그 경고조차 없었다). 복구는 보관해 둔 솔트 파일을 되돌리는 것뿐이다.
        logger.warning(
            "검수자 별칭 솔트가 없어 새로 만들었다: %s — 이 폴더에서 이미 별칭이 발급됐다면 이전 별칭은 전부 무효다"
            "(보관본이 있으면 되돌릴 것)", path)


class AliasMap:
    """한 후보 풀의 실 doc_id ↔ 별칭. **풀 전체를 기준으로** 충돌을 검사한다(검수자에게 안 보이는 문서와도 겹치면 안 된다).

    앞으로 가는 방향(`alias`)은 풀에 없는 doc_id(결정 원장에만 남은 옛 문서)도 같은 식으로 계산한다.
    거꾸로(`resolve`)는 풀 안의 문서만 푼다.
    """

    def __init__(self, salt: bytes, pool_doc_ids: Iterable[str]) -> None:
        self._salt = salt
        by_alias: dict[str, str] = {}
        clashes: list[str] = []
        pool = sorted({str(d) for d in pool_doc_ids})
        for doc_id in pool:
            alias = alias_for(salt, doc_id)
            other = by_alias.setdefault(alias, doc_id)
            if other != doc_id:
                clashes.append(f"{alias} <- {other!r} , {doc_id!r}")
        real = set(pool)
        # 별칭이 어떤 문서의 **실 doc_id 와 같아도** 응답에서 둘을 구별할 수 없다.
        clashes += [f"{a} == 실 doc_id {d!r}" for a, d in by_alias.items() if a in real and a != d]
        if clashes:
            raise AliasLayerError(f"풀 안에서 별칭이 충돌함({len(clashes)}건): " + " ; ".join(clashes[:5]))
        self._to_real = by_alias

    @classmethod
    def for_service(cls, service: Any) -> "AliasMap":
        """서비스가 읽는 후보 폴더의 솔트와 풀 전체로 만든다. 폴더가 아직 없으면(풀이 비었다) 별칭을 붙일 문서가 없으니
        폴더를 만들지 않고 일회용 솔트를 쓴다."""
        root = Path(service.root)
        if not root.exists():
            return cls(secrets.token_bytes(32), ())
        return cls(load_or_create_salt(root), service.review_batch_index())

    def alias(self, doc_id: str) -> str:
        return alias_for(self._salt, doc_id)

    def resolve(self, alias: str) -> str | None:
        """별칭 → 풀 안의 실 doc_id. 모르면 None. 정확히 같은 철자만 받는다."""
        return self._to_real.get(alias) if isinstance(alias, str) else None


def _scrub_doc_id(value: Any, doc_id: str, alias: str) -> Any:
    """문자열 안에 **박혀 있는** 자기 실 doc_id 를 별칭으로 바꾼다(중첩 dict·list 포함, 대소문자 무시)."""
    if not doc_id:
        return value
    needle = unicodedata.normalize("NFC", doc_id)
    lowered = needle.lower()
    pattern: list[re.Pattern[str]] = []          # 걸리는 문자열이 처음 나올 때만 만든다(풀 전체에서 정규식을 수천 번 만들지 않게)

    def walk(v: Any) -> Any:
        if isinstance(v, str):
            if lowered not in v.lower():
                return v
            if not pattern:
                pattern.append(re.compile(re.escape(needle), re.IGNORECASE))
            return pattern[0].sub(alias, v)
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        if isinstance(v, list):
            return [walk(x) for x in v]
        return v

    return walk(value)


def blind_query_match(row: Mapping[str, Any], needle: str) -> bool:
    """숨김 검수자의 목록 검색어(query)가 이 행에 맞는가 — **화면에 나가는 문서 번호(별칭)의 앞부분에만** 닿는다.

    종전 규칙은 doc_id·title 부분 일치라 query=TS 가 GOLD-P-TS-001 을 돌려줬다. 후보 풀 측정에서 등급 코드가 나오는 필드가
    doc_id(988건)와 title(1건)이라, 이 둘에는 맞추지 않는다: 실 doc_id 는 이 시야에 없고(행의 doc_id 는 이미 별칭이다) 제목도
    검색 대상에서 뺀다. 검색어는 'RV-' 로 시작해야 하고 별칭은 무작위라, 등급 코드·낱말로 좁혀서 얻는 것이 없다.
    needle 은 서비스가 소문자로 접어 넘긴다."""
    return needle.startswith(ALIAS_PREFIX.lower()) and str(row.get("doc_id") or "").lower().startswith(needle)


def _blind_title(title: object, doc_id: str, alias: str, *, is_real: bool) -> str:
    """제목 — 자기 doc_id 가 박혀 있으면 그 자리를 별칭으로 바꾸고, 그래도 등급 코드(합성 후보는 등급명 어휘까지)가
    남으면 제목 전체를 별칭으로 바꾼다. 깨끗한 제목은 그대로다."""
    text = _scrub_doc_id(str(title or ""), doc_id, alias)
    if _GRADE_CODE_ANYWHERE.search(unicodedata.normalize("NFKC", text)) or _exposes_grade(text, is_real=is_real):
        return alias
    return text


# ── 배정 원장 ────────────────────────────────────────────────────────────────


class AssignmentLedger:
    """검수 배정 append-only 원장. 이벤트 = assign / unassign.

    한 줄 = 한 이벤트(JSON). 필드:
        schema_version, event_id, event(assign|unassign), reviewer_id,
        doc_id **또는** review_batch(정확히 하나), actor_id(배정한 관리자 = 서버가 확정한 JWT sub),
        at(UTC ISO), reason

    현재 배정은 이벤트를 **순서대로 재생**해 얻는다(지우거나 고치지 않는다 — 결정 원장과 같은 규율).
    배정과 해제는 **같은 단위**로 짝이 맞는다: 배치로 배정한 검수자에게서 문서 하나만 빼는 것은
    표현하지 않는다(그 문서는 배치 배정으로 계속 보인다). 빼려면 배치를 해제하고 남길 문서를
    문서 단위로 다시 배정한다.
    """

    def __init__(self, root: Path | str) -> None:
        root = Path(root)
        self.path = root / ASSIGNMENT_LEDGER_NAME
        self.lock_path = root / f"{ASSIGNMENT_LEDGER_NAME}.lock"

    # 읽기 ---------------------------------------------------------------
    def read_events(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        events: list[dict[str, Any]] = []
        try:
            # ⚠ splitlines() 를 쓰지 않는다 — 줄 경계가 \n 만이 아니라 U+2028·U+2029·U+0085·\x0b·\x0c·\x1c–\x1e 도 줄바꿈으로 본다.
            #   원장은 ensure_ascii=False 로 적으므로(한글 그대로) 검수자 ID·사유에 그 문자가 들어 있으면 한 이벤트가 두 토막이 되어 둘 다
            #   "JSON 이 아님" 으로 버려지고, 배정은 적혔는데 아무 데서도 안 보인다(2026-09-22 콘솔 배정 패널의 적대적 문자열 시험이 잡았다).
            #   json.dumps 가 \n 은 언제나 이스케이프하므로 이벤트 하나는 raw \n 이 없는 한 줄이다.
            lines = self.path.read_text(encoding="utf-8").split("\n")
        except OSError as exc:
            # 읽지 못하면 **아무도 배정받지 못한 것**으로 본다(fail-closed) — 검수자에게 문서가
            # 안 보일 뿐 새지는 않는다. 조용히 넘기지 않고 남긴다.
            logger.error("검수 배정 원장을 읽지 못함(배정 없음으로 처리): %s (%s)", self.path, exc)
            return []
        for n, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("검수 배정 원장 %d행이 JSON 이 아니라 건너뜀: %s", n, self.path)
                continue
            if self._valid(row):
                events.append(row)
            else:
                logger.warning("검수 배정 원장 %d행이 형식에 맞지 않아 건너뜀: %s", n, self.path)
        return events

    @staticmethod
    def _valid(row: Any) -> bool:
        if not isinstance(row, dict) or row.get("event") not in (EVENT_ASSIGN, EVENT_UNASSIGN):
            return False
        if not norm_id(row.get("reviewer_id")):
            return False
        has_doc = bool(norm_id(row.get("doc_id")))
        has_batch = bool(norm_id(row.get("review_batch")))
        return has_doc != has_batch          # 정확히 하나

    @staticmethod
    def replay(events: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, set[str]]]:
        """검수자별 현재 배정 {reviewer_id: {"doc_ids": {...}, "review_batches": {...}}}."""
        state: dict[str, dict[str, set[str]]] = {}
        for e in events:
            rid = norm_id(e.get("reviewer_id"))
            slot = state.setdefault(rid, {"doc_ids": set(), "review_batches": set()})
            if norm_id(e.get("doc_id")):
                key, value = "doc_ids", norm_id(e.get("doc_id"))
            else:
                key, value = "review_batches", norm_id(e.get("review_batch"))
            if e.get("event") == EVENT_ASSIGN:
                slot[key].add(value)
            else:
                slot[key].discard(value)
        return state

    def state(self) -> dict[str, dict[str, set[str]]]:
        return self.replay(self.read_events())

    @staticmethod
    def live_assignments(events: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """지금 유효한 배정을 (검수자, 대상) 하나당 한 줄로 — 관리자 화면의 배정 표가 쓴다.

        replay() 와 같은 규칙으로 같은 이벤트를 같은 순서로 읽는다(배정 = 더함, 해제 = 뺌). 다른 점은 각 줄에
        **그 배정을 적은 이벤트**의 시각(at)·배정한 관리자(actor_id)·사유(reason)를 붙인다는 것뿐이다. 배정 → 해제 →
        다시 배정이면 마지막 배정 이벤트가 나온다(그 시각부터 유효하다). 화면이 원장 이벤트를 다시 재생하지 않게 서버가
        준다 — 이벤트 목록은 limit 로 잘리고, 재생 규칙이 화면에 또 생기면 서버와 어긋날 수 있다.
        """
        live: dict[tuple[str, str, str], Mapping[str, Any]] = {}
        for e in events:
            rid = norm_id(e.get("reviewer_id"))
            if norm_id(e.get("doc_id")):
                key = (rid, "doc_id", norm_id(e.get("doc_id")))
            else:
                key = (rid, "review_batch", norm_id(e.get("review_batch")))
            if e.get("event") == EVENT_ASSIGN:
                live.setdefault(key, e)     # 이미 유효한 배정이면 처음 적힌 줄을 둔다(apply 는 중복을 적지 않는다 — 손으로 넣은 줄 대비)
            else:
                live.pop(key, None)
        rows = [
            {
                "reviewer_id": rid, "kind": kind, "target": target,
                "assigned_at": e.get("at"), "assigned_by": e.get("actor_id"),
                "reason": str(e.get("reason") or ""),
            }
            for (rid, kind, target), e in live.items()
        ]
        # 검수자별로 묶고, 배치를 문서보다 앞에 둔다(배치 하나가 문서 수십 건을 덮는다).
        rows.sort(key=lambda r: (r["reviewer_id"], r["kind"] != "review_batch", r["target"]))
        return rows

    def visible_doc_ids(
        self, reviewer_id: str, doc_batches: Mapping[str, str | None]
    ) -> frozenset[str]:
        """이 검수자가 지금 볼 수 있는 doc_id — 문서 단위 배정 + 배정된 배치에 속한 문서."""
        slot = self.state().get(norm_id(reviewer_id))
        return self._resolve(slot, doc_batches) if slot else frozenset()

    @staticmethod
    def _resolve(
        slot: Mapping[str, AbstractSet[str]], doc_batches: Mapping[str, str | None]
    ) -> frozenset[str]:
        by_doc, batches = slot["doc_ids"], slot["review_batches"]
        return frozenset(
            doc for doc, batch in doc_batches.items()
            if doc in by_doc or (batch and norm_id(batch) in batches)
        )

    # 쓰기 ---------------------------------------------------------------
    def apply(
        self, *, event: str, reviewer_id: str, actor_id: str,
        targets: list[tuple[str, str]], reason: str = "",
    ) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        """targets = [("doc_id"|"review_batch", 값), …]. 돌려주는 값 = (적은 이벤트, 건너뛴 대상).

        이미 그 상태인 대상(배정된 것을 또 배정 · 배정 안 된 것을 해제)은 **적지 않고** 건너뛴다 —
        멱등이고 원장에 뜻 없는 줄이 쌓이지 않는다. 현재 상태를 읽는 것부터 적는 것까지를 **잠금
        안에서** 하므로 동시에 같은 배정을 넣어도 줄이 두 번 적히지 않는다.
        """
        if event not in (EVENT_ASSIGN, EVENT_UNASSIGN):
            raise ValueError(f"unsupported assignment event: {event}")
        reviewer = norm_id(reviewer_id)
        actor = norm_id(actor_id)
        if not reviewer or not actor:
            raise ValueError("reviewer_id and actor_id are required")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        clean: list[tuple[str, str]] = []
        for kind, raw in targets:
            value = norm_id(raw)
            if kind not in ("doc_id", "review_batch") or not value:
                raise ValueError(f"invalid assignment target: {kind!r}={raw!r}")
            if (kind, value) not in clean:          # 같은 요청 안의 중복은 한 번만
                clean.append((kind, value))
        written: list[dict[str, Any]] = []
        skipped: list[dict[str, str]] = []
        with _exclusive_ledger_lock(self.lock_path):
            slot = self.replay(self.read_events()).get(
                reviewer, {"doc_ids": set(), "review_batches": set()})
            lines: list[str] = []
            for kind, value in clean:
                bucket = slot["doc_ids" if kind == "doc_id" else "review_batches"]
                if (value in bucket) == (event == EVENT_ASSIGN):
                    skipped.append({kind: value})     # 이미 그 상태
                    continue
                row = {
                    "schema_version": 1,
                    "event_id": str(uuid4()),
                    "event": event,
                    "reviewer_id": reviewer,
                    kind: value,
                    "actor_id": actor,
                    "at": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "reason": (reason or "").strip(),
                }
                written.append(row)
                lines.append(dumps_line(row, sort_keys=True) + "\n")   # U+2028 등을 이스케이프(koipa/jsonl_lines.py)
                (bucket.add if event == EVENT_ASSIGN else bucket.discard)(value)
            if lines:
                with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                    handle.writelines(lines)
                    handle.flush()
                    os.fsync(handle.fileno())
        return written, skipped

    # 현황 ---------------------------------------------------------------
    def status(
        self, doc_batches: Mapping[str, str | None], *,
        reviewer_id: str | None = None, doc_id: str | None = None,
        review_batch: str | None = None,
    ) -> dict[str, Any]:
        """관리자용 배정 현황. 검수자별 배정 + 문서 단위 커버리지(누구에게도 안 배정된 문서 수)."""
        events = self.read_events()
        state = self.replay(events)
        rows: list[dict[str, Any]] = []
        covered: set[str] = set()
        for rid in sorted(state):
            slot = state[rid]
            if not slot["doc_ids"] and not slot["review_batches"]:
                continue        # 배정을 모두 해제한 검수자는 현황에서 뺀다(이력은 events 에 남는다)
            visible = self._resolve(slot, doc_batches)
            covered |= visible
            rows.append({
                "reviewer_id": rid,
                "doc_ids": sorted(slot["doc_ids"]),
                "review_batches": sorted(slot["review_batches"]),
                "visible_doc_count": len(visible),
                "_visible": visible,
            })
        if reviewer_id:
            rows = [r for r in rows if r["reviewer_id"] == norm_id(reviewer_id)]
        if doc_id:
            rows = [r for r in rows if norm_id(doc_id) in r["_visible"]]
        if review_batch:
            rows = [r for r in rows if norm_id(review_batch) in r["review_batches"]]
        for r in rows:
            r.pop("_visible")
        # 배정 표(관리자 화면) — 위 필터를 통과한 검수자들의 배정을 한 줄씩 펼친 것. 필터를 바꿔도 reviewers 와 같은 검수자만 나온다.
        shown = {r["reviewer_id"] for r in rows}
        return {
            "total_candidates": len(doc_batches),
            "assigned_candidate_count": len(covered),
            "unassigned_candidate_count": len(doc_batches) - len(covered),
            "reviewers": rows,
            "assignments": [a for a in self.live_assignments(events) if a["reviewer_id"] in shown],
            "ledger_events_total": len(events),
            "by_event": dict(sorted(Counter(str(e.get("event")) for e in events).items())),
        }


# ── 숨김 검수자 한 명의 시야 ─────────────────────────────────────────────────


def _is_decision(event: Mapping[str, Any]) -> bool:
    """결정 이벤트인가. 출처 기록 같은 다른 종류는 상태·등급을 바꾸지 않는다(_latest_decisions 와 같은 규칙)."""
    return str(event.get("event_kind") or "decision") == "decision"


class BlindView:
    """숨김 검수자 한 명이 볼 수 있는 원장·후보 — **원장을 그 사람의 이벤트만으로 다시 읽은 것**이다.

    저장된 값(후보 행의 status·final_grade·management, 이벤트의 management_before/after)에는 다른 검수자의
    결정이 섞여 있다. 그래서 응답에서 키를 하나씩 막는 대신, 문서마다 원장의 자기 이벤트만 다시 재생해
    "다른 검수자가 아무도 결정하지 않았다면 이 검수자가 보았을 화면"을 만든다. 남의 결정은 재생에 들어가지
    않으니 어느 필드로도 샐 수 없다.

    쓰는 곳은 셋이다.  rows() — 후보 행(서비스가 집계·필터 **앞에** 끼운다),  own_events() — 결정 원장 화면,
    그리고 ReviewerScope.shape_candidate — 상세·결정 응답. 원장과 저장 형식은 읽기만 한다.
    """

    def __init__(self, actor_id: str, service: Any = None) -> None:
        self.actor_id = actor_id
        self._service = service
        self._by_doc_cache: dict[str, list[dict[str, Any]]] | None = None
        self._base_status_cache: dict[str, str] | None = None
        self._alias_cache: AliasMap | None = None

    def _aliases(self) -> AliasMap:
        """이 요청의 별칭 표 — 처음 쓸 때 한 번 만든다(솔트를 읽거나 만들지 못하면 AliasLayerError = 요청 거절)."""
        if self._alias_cache is None:
            if self._service is None:
                raise RuntimeError("BlindView needs the candidate service to alias doc_ids")
            self._alias_cache = AliasMap.for_service(self._service)
        return self._alias_cache

    # 원장 ---------------------------------------------------------------
    def _own(self, event: Mapping[str, Any] | None) -> bool:
        # NFC 로 접어 비교한다 — 한글 sub 는 NFC/NFD 로 갈릴 수 있고, 원장에는 로그인 때 들어온 그대로의
        # sub 가 적혀 있다(norm_id 와 같은 규칙).
        return bool(event) and norm_id(event.get("actor_id")) == self.actor_id

    @staticmethod
    def _group(events: Iterable[Any]) -> dict[str, list[dict[str, Any]]]:
        by_doc: dict[str, list[dict[str, Any]]] = {}
        for e in events:
            if isinstance(e, dict):
                by_doc.setdefault(str(e.get("doc_id") or ""), []).append(e)
        return by_doc

    def _by_doc(self) -> dict[str, list[dict[str, Any]]]:
        if self._by_doc_cache is None:
            if self._service is None:
                raise RuntimeError("BlindView.rows() needs the candidate service")
            self._by_doc_cache = self._group(self._service.ledger_rows())
        return self._by_doc_cache

    def _base_status(self) -> dict[str, str]:
        if self._base_status_cache is None:
            if self._service is None:
                raise RuntimeError("BlindView.rows() needs the candidate service")
            self._base_status_cache = self._service.base_status_index()
        return self._base_status_cache

    # M 입력 --------------------------------------------------------------
    def _replay_management(
        self, doc_events: list[dict[str, Any]], current: Mapping[str, Any] | None,
    ) -> tuple[dict[str, Any], dict[int, dict[str, Any]]]:
        """한 문서의 원장을 재생해 (자기 M 시야의 현재 값, {id(이벤트): 그 이벤트 직후 자기 M 시야}).

        결정이 M 을 줄 때 서버는 '이번에 준 칸만 덮고 나머지는 이전 값을 잇는다'(decide). 이벤트는 **준 값을
        따로 싣지 않고** 병합 전(management_before)·후(management_after)만 싣는다 — 저장 형식은 바꾸지 않으므로
        여기서 이벤트 순서와 before/after 의 차이로 재구성한다:

          · 시작값 = 문서에 M 을 처음 쓴 이벤트의 management_before(적재 때 값 = 문서의 사실). M 을 쓴 이벤트가
            없으면 후보의 현재 값이 곧 적재 값이다. 다만 적재 값에 다른 사람의 recorded_by 가 붙어 있으면(원장에
            없는 옛 기록) 그 사람의 판단이므로 뺀다.
          · 자기 이벤트에서 **값이 바뀐 칸**(before ≠ after)은 자기가 준 값이다 → 시야에 반영.
          · 값이 그대로인 칸은 반영하지 않는다. 안 준 것인지 이전과 같은 값을 준 것인지 이벤트로는 가를 수
            없는데, 이전 값이 남의 것이면 그 값을 내 것처럼 보이는 것이 결함 2 다. 그래서 **자기가 남의 값과
            똑같은 값을 적은 경우에는 그 값이 안 보인다**(안전한 쪽의 오차 — 값이 새는 것보다 낫다).

        남의 이벤트는 시야에 들어가지 않는다 — 남이 그 사이에 바꾼 칸도, 남이 적은 값도 재생 대상이 아니다.
        """
        bearing = [
            e for e in doc_events
            if _is_decision(e) and isinstance(e.get("management_after"), dict)
        ]
        base = dict(bearing[0].get("management_before") or {}) if bearing else dict(current or {})
        by, at = base.get("recorded_by"), base.get("recorded_at")
        if by and norm_id(by) != self.actor_id:
            world: dict[str, Any] = dict.fromkeys(_M_INPUT_FIELDS)
            by = at = None
        else:
            world = {f: base.get(f) for f in _M_INPUT_FIELDS}
        after_views: dict[int, dict[str, Any]] = {}
        for e in bearing:
            if not self._own(e):
                continue
            before, after = e.get("management_before") or {}, e["management_after"]
            moved = False
            for f in _M_INPUT_FIELDS:
                if after.get(f) != before.get(f):
                    world[f] = after.get(f)
                    moved = True
            if moved:
                by, at = after.get("recorded_by"), after.get("recorded_at")
            after_views[id(e)] = _management_view(
                {"management": {**world, "recorded_by": by, "recorded_at": at}})
        return _management_view({"management": {**world, "recorded_by": by, "recorded_at": at}}), after_views

    # 이벤트 --------------------------------------------------------------
    def _shape_event(self, event: Mapping[str, Any], after_views: Mapping[int, dict[str, Any]]) -> dict[str, Any]:
        """자기 이벤트 한 건 — 분류표(BLIND_*_EVENT_FIELDS)의 허용 목록 위에서 만든다. doc_id 는 별칭으로 나간다."""
        out = {
            k: v for k, v in event.items()
            if k in BLIND_OWN_EVENT_FIELDS or k in BLIND_PUBLIC_EVENT_FIELDS or k in BLIND_ALIASED_EVENT_FIELDS
        }
        rebuilt = after_views.get(id(event))
        if rebuilt is not None:
            out["management_after"] = rebuilt
        if "doc_id" in out:
            real = str(out["doc_id"])
            alias = self._aliases().alias(real)
            out = _scrub_doc_id(out, real, alias)      # 사유 같은 문자열 안에 박힌 실 doc_id 도
            out["doc_id"] = alias                      # 키 자리는 그대로(제자리 교체)
        return out

    def own_events(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """결정 원장 화면용 — 이벤트 목록(원장 순서)에서 자기 이벤트만 골라 위 모양으로 바꾼다."""
        self._aliases()          # 별칭 계층을 못 쓰면 이벤트가 없어도 여기서 거절된다(조용히 빈 목록을 주지 않는다)
        after_views: dict[int, dict[str, Any]] = {}
        for doc_events in self._group(events).values():
            after_views.update(self._replay_management(doc_events, None)[1])
        return [self._shape_event(e, after_views) for e in events if isinstance(e, dict) and self._own(e)]

    # 후보 ----------------------------------------------------------------
    def _row(self, row: Mapping[str, Any]) -> dict[str, Any]:
        doc_id = str(row.get("doc_id") or "")
        doc_events = self._by_doc().get(doc_id, [])
        own = [e for e in doc_events if self._own(e)]
        decisions = [e for e in own if _is_decision(e)]
        latest = decisions[-1] if decisions else None
        management, after_views = self._replay_management(doc_events, row.get("management"))
        out = dict(row)
        # 결정이 없는 문서의 상태 = 메타데이터의 candidate_status(_candidates 가 결정으로 덮기 전 값).
        out["status"] = (latest or {}).get("status") or self._base_status().get(doc_id) or "proposed"
        out["final_grade"] = (latest or {}).get("final_grade")
        out["grade_fixed"] = bool(out["final_grade"])
        out["latest_decision"] = self._shape_event(latest, after_views) if latest else None
        out["management"] = management
        if "decision_history" in row:
            out["decision_history"] = [self._shape_event(e, after_views) for e in own]
        # 문서 번호 — 이 시야를 지나는 모든 후보 행의 doc_id 는 별칭이다. 서비스는 이 행 위에서 정렬·검색·집계하므로
        # **줄 순서도 별칭 순**이 된다(실 doc_id 순이면 등급 코드가 든 id 가 같은 등급끼리 뭉쳐 위치로 등급이 읽힌다).
        alias = self._aliases().alias(doc_id)
        for key in list(out):
            if key not in ("text", "doc_id", "title"):        # 본문은 문서의 사실이라 손대지 않는다
                out[key] = _scrub_doc_id(out[key], doc_id, alias)
        out["doc_id"] = alias
        out["title"] = _blind_title(
            out.get("title"), doc_id, alias, is_real=bool(row.get("is_actual_document")))
        return out

    def rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """후보 행을 이 검수자의 시야로 바꾼다(새 dict — 캐시된 행은 건드리지 않는다)."""
        self._aliases()          # 별칭 계층을 못 쓰면 행이 없어도 여기서 거절된다
        return [self._row(r) for r in rows]


# ── 요청자의 범위 ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ReviewerScope:
    """한 요청이 무엇을 볼 수 있나. `restricted=False` 면 어떤 것도 걸러내지 않는다."""

    restricted: bool = False
    actor_id: str = ""
    assignment: bool = False
    blind: bool = False

    # 배정 ----------------------------------------------------------------
    def visible_doc_ids(self, service: Any) -> frozenset[str] | None:
        """볼 수 있는 doc_id 집합. **None = 제한 없음**, 빈 집합 = 아무것도 안 보임(뭉치지 말 것)."""
        if not (self.restricted and self.assignment):
            return None
        return AssignmentLedger(service.root).visible_doc_ids(
            self.actor_id, service.review_batch_index())

    @property
    def blind_active(self) -> bool:
        return self.restricted and self.blind

    # 숨김 ----------------------------------------------------------------
    def candidate_view(self, service: Any) -> CandidateView | None:
        """서비스의 list_candidates·summary 에 넘길 시야. **집계·필터 앞에서** 행을 바꾸게 하려는 것이다 —
        나중에 바꾸면 폐기 제외·상태 필터·건수가 남의 결정을 따라간다. 숨김이 아니면 None(= 종전 그대로)."""
        if not self.blind_active:
            return None
        return BlindView(self.actor_id, service).rows

    @staticmethod
    def _allowed(mapping: Mapping[str, Any], *allowed: AbstractSet[str]) -> dict[str, Any]:
        return {k: v for k, v in mapping.items() if any(k in a for a in allowed)}

    @classmethod
    def _allowed_candidate(cls, row: Mapping[str, Any]) -> dict[str, Any]:
        """후보 행 한 건을 분류표의 허용 목록으로 거른다. **마지막 관문** — doc_id 가 별칭 모양이 아니면(시야를 안 거친
        행이라는 뜻이다) 나가지 않고 요청을 거절한다. 실 doc_id 가 검수자에게 새는 것보다 오류가 낫다."""
        out = cls._allowed(
            row, BLIND_ALIASED_CANDIDATE_FIELDS, BLIND_KEPT_CANDIDATE_FIELDS, BLIND_OWN_ONLY_CANDIDATE_FIELDS)
        if not is_alias(out.get("doc_id")):
            raise AliasLayerError("숨김 응답에 별칭이 아닌 doc_id 가 실리려 했다(BlindView 를 거치지 않은 행)")
        return out

    # 관리자 시야의 매핑 키 ---------------------------------------------------------
    def _annotates_aliases(self) -> bool:
        """관리자(제한 없는 시야)의 후보 행·상세에 reviewer_alias 를 더할까 — **숨김 손잡이가 켜진 동안만.**
        꺼져 있으면 응답은 종전과 바이트 단위로 같다."""
        return (not self.restricted) and bool(getattr(settings, "golden_review_blind_enforced", False))

    def _admin_alias(self, service: Any) -> Any:
        """(doc_id → 별칭 또는 None) 함수. 별칭 계층이 망가졌으면 오류를 삼키지 않고 로그에 남기고 None 을 준다 — 관리자
        화면이 별칭 문제로 통째로 죽으면 그 문제를 고칠 화면도 잃는다(검수자 요청은 이 경우 503 으로 닫힌다)."""
        try:
            return AliasMap.for_service(service).alias
        except AliasLayerError:
            return lambda _doc_id: None

    def shape_candidate(self, candidate: Mapping[str, Any], service: Any) -> dict[str, Any]:
        """후보 한 건(상세·결정 응답). 숨김이 아니면 그대로 돌려준다(관리자는 숨김 손잡이가 켜진 동안 reviewer_alias 만 더해진다)."""
        if not self.blind_active:
            if self._annotates_aliases():
                return {**candidate, ADMIN_ALIAS_KEY: self._admin_alias(service)(candidate.get("doc_id"))}
            return dict(candidate)
        (row,) = BlindView(self.actor_id, service).rows([dict(candidate)])
        return self._allowed_candidate(row)

    def shape_summary(self, summary: Mapping[str, Any]) -> dict[str, Any]:
        """집계에서 뺄 것을 뺀다. 남는 결정 집계는 서비스가 자기 시야의 행 위에서 센 값이어야 한다(candidate_view)."""
        if not self.blind_active:
            return dict(summary)
        return self._allowed(summary, BLIND_KEPT_SUMMARY_FIELDS, BLIND_OWN_SUMMARY_FIELDS)

    def shape_list(self, payload: Mapping[str, Any], service: Any = None) -> dict[str, Any]:
        """GET /golden/candidates 응답 전체. 행·집계는 서비스가 candidate_view 로 이미 자기 시야로 바꾼 것이다."""
        if not self.blind_active:
            if service is not None and self._annotates_aliases():
                alias = self._admin_alias(service)
                out = dict(payload)
                out["candidates"] = [
                    {**c, ADMIN_ALIAS_KEY: alias(c.get("doc_id"))} for c in payload.get("candidates", [])]
                return out
            return dict(payload)
        out = dict(payload)
        out["candidates"] = [self._allowed_candidate(c) for c in payload.get("candidates", [])]
        out["summary"] = self.shape_summary(payload.get("summary") or {})
        out["batch_summary"] = self._allowed(
            payload.get("batch_summary") or {}, BLIND_KEPT_BATCH_SUMMARY_FIELDS, BLIND_OWN_BATCH_SUMMARY_FIELDS)
        return out

    def event_view(self, service: Any) -> dict[str, Any]:
        """recent_decisions 에 넘길 인자 — 숨김이면 자기 이벤트만, 분류표의 허용 목록 위에서. doc_id 는 별칭으로 나간다."""
        if not self.blind_active:
            return {}
        return {"view": BlindView(self.actor_id, service).own_events}

    def query_match(self) -> QueryMatch | None:
        """list_candidates 에 넘길 검색 규칙. 숨김이면 별칭 앞부분 일치뿐이고, 아니면 None(= 종전 규칙 그대로)."""
        return blind_query_match if self.blind_active else None

    # 경로의 문서 번호 ------------------------------------------------------------
    def resolve_doc_id(self, service: Any, doc_id: str) -> str | None:
        """경로 {doc_id} 자리에 온 값을 실 doc_id 로 푼다. **None = 없는 문서**(호출부가 404).

        숨김이 아니면 종전 그대로 값을 돌려준다. 숨김이면 **별칭만** 받고, 그 검수자에게 보이는(배정된) 문서 안에서만
        푼다 — 실 doc_id 를 넣거나, 별칭이 배정 밖 문서의 것이면 없는 문서와 똑같이 None 이다(존재 여부·대응을 캐지 못한다).
        """
        if not self.blind_active:
            return doc_id
        real = AliasMap.for_service(service).resolve(doc_id)
        if real is None:
            return None
        visible = self.visible_doc_ids(service)
        return real if visible is None or real in visible else None


UNRESTRICTED = ReviewerScope()


def resolve_scope(auth: Mapping[str, Any] | None) -> ReviewerScope:
    """인증 결과 → 이 요청의 범위. 손잡이가 꺼져 있거나 관리자 계열이면 제한 없음.

    auth=None 은 **인증 없이 들어오는 진입로**(잡 화면 HTML)다. 손잡이가 켜져 있으면 누구인지
    모르는 요청을 통과시킬 수 없으므로 401 — 안 그러면 검수자가 쿠키를 빼고 같은 주소를 열어
    격리를 우회한다.
    """
    if not enforcement_active():
        return UNRESTRICTED
    if auth is None:
        raise ReviewerAccessError(
            401, "portal login required while reviewer assignment/blind review is enforced")
    if not _roles(auth).isdisjoint(PRIVILEGED_ROLES):
        return UNRESTRICTED
    claims = auth.get("claims")
    actor_id = norm_id(getattr(claims, "sub", ""))
    if not actor_id:
        # 누구인지 모르면 누구의 배정인지도 모른다 — 공유 API Key 로는 검수자 범위를 정할 수 없다.
        raise ReviewerAccessError(
            403, "reviewer isolation requires a portal JWT login; shared API keys are not allowed")
    return ReviewerScope(
        restricted=True,
        actor_id=actor_id,
        assignment=bool(getattr(settings, "golden_reviewer_assignment_enforced", False)),
        blind=bool(getattr(settings, "golden_review_blind_enforced", False)),
    )
