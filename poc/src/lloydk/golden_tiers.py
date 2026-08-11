"""골든셋 3-tier 논리 분할 — 정본 1파일 + **파생** tier(저장 컬럼 아님 → 드리프트 방지).

레드팀 권고: tier는 label_source/review_status/reviewer로부터 결정되는 파생값이다. 3물리 파일로
쪼개면 train_subset.jsonl식 doc_id/스키마 드리프트가 재발하므로, 정본은 하나로 두고 consumer가
이 함수로 필터한다.

tier 결정:
  - locked_gold_eval : 유효 사람 서명(label_source=human_review + 인증 envelope: 실계정 reviewer·
                       gate_version·signed_at·reviewer_ids, is_valid_signoff). real 평가정답으로
                       **집계**되려면 실문서 출처(document_origin∈{public_real,customer_real})까지
                       갖춰야 함(is_real_locked_eval — eval_records/eval_readiness가 강제).
  - held_review      : human_review인데 유효 서명 미충족(무효/민팅/손편집) 또는 합성 본문 서명 —
                       평가정답 아님·학습 금지로 격리(역-누수 차단). '사람검수 합성사례'는 여기 남아
                       회귀 픽스처로만 쓴다.
  - legal_floor      : 법적근거/시나리오(public_definitive·nkt_designated + koipa_case_based·
                       codex_review·curated_scenario) — 릴리스 차단 floor(고→S3 회귀 차단). 평가 정답
                       승격은 사람 서명 후. 합성/템플릿이라 그 자체로 일반화-진실 아님.
                       ⚠️ floor 안에서도 권위가 갈린다: 외부권위(EXTERNAL_AUTHORITY_SOURCES,
                       record 검증은 is_external_authority)만 실측 인용 가능하고, 큐레이트 프록시
                       (SYNTHETIC_PROXY_SOURCES — koipa 판례 인용 조작 확인, 2026-07-03 감사)는
                       실세계 정답·서빙 권위로 취급 금지.
  - gold_candidate   : 새 게이트 자동 통과(label_source=rule_llm_agreement / review_status=gold_candidate)
                       — 사람 서명 대기. 학습엔 써도 됨.
  - silver_train     : 그 외(구 llm_judge_*, needs_review_*) — **학습 시드 전용, 평가 금지**.

consumer 계약:
  - 평가(eval) : locked_gold_eval만. 비어 있으면(9월 전) legal_floor를 interim floor로(호출부가 경고).
  - 학습(train): silver_train + gold_candidate (+ legal_floor). locked는 **절대 학습 금지**(train-on-test).
"""
from __future__ import annotations

import re
from typing import Sequence

TIER_LOCKED = "locked_gold_eval"
TIER_LEGAL_FLOOR = "legal_floor"
TIER_CANDIDATE = "gold_candidate"
TIER_SILVER = "silver_train"
# human_review 라벨이나 유효 서명(envelope)을 못 갖춘 격리 tier — 평가도 학습도 아님.
# (무효 서명·correction-import 민팅·손편집 human_review, 및 서명은 유효하나 합성 본문인 사례.)
# de-locked human_review가 SILVER(학습연료)로 흘러드는 역-누수를 차단하는 명시적 버킷.
TIER_HELD = "held_review"

# ── 문서 텍스트 출처(document_origin) — 라벨 권위와 직교하는 '본문 실재성' 축 ──────────────
# real 평가정답·최종 성능근거는 실문서에서만 나온다(capstone F1 0.26 텍스처 갭). 명시 필드가
# 우선, 없으면 source에서 결정적 유도, 그래도 모르면 unknown(fail-closed).
ORIGIN_SYNTHETIC = "synthetic"
ORIGIN_PUBLIC_REAL = "public_real"       # 공개 실문서(판례·금융보고서 등) — 중앙 실텍스트 회귀 근거
ORIGIN_CUSTOMER_REAL = "customer_real"   # 고객사 실(비식별)문서 — 최종 운영 성능 근거
ORIGIN_UNKNOWN = "unknown"               # 기본값(fail-closed): 명시·유도 불가 → real 아님
_VALID_ORIGINS = frozenset({ORIGIN_SYNTHETIC, ORIGIN_PUBLIC_REAL, ORIGIN_CUSTOMER_REAL, ORIGIN_UNKNOWN})
_REAL_TEXT_ORIGINS = frozenset({ORIGIN_PUBLIC_REAL, ORIGIN_CUSTOMER_REAL})
# source(본문 태그) → origin 결정적 유도. (콘솔 표시만 깨질 뿐 파일·리터럴은 UTF-8 정상.)
_SYNTHETIC_ORIGIN_SOURCES = frozenset({"synthetic", "public_scenario", "synthetic_grounded"})
_CUSTOMER_REAL_ORIGIN_SOURCES = frozenset({"real_deidentified"})
_PUBLIC_REAL_EXACT_SOURCES = frozenset({"금융보고서"})
_PUBLIC_REAL_PREFIXES = ("판례",)        # 판례 · 판례(1000+/2000+/3000+) · 판례_공개문서 …

# ── 권위 분류(2026-07-03 감사) — floor tier 안에서도 '실세계 정답'과 '큐레이트 프록시'를 구분한다 ──
# 외부권위: 라벨 근거가 텍스트 밖 실세계 사실(공개 판결/공시=public_definitive, §9 고시 지정=
# nkt_designated). 사람서명 없이 실측 인용 가능한 유일한 축. 단 nkt는 record가 지정근거
# (legal_reference)를 가질 때만 성립 — 근거 없는 손작성 시나리오가 이 출처를 위조 사용한 사례
# 6건 발견(augment_high_risk_gold, → curated_scenario로 교정). record 수준은 is_external_authority().
EXTERNAL_AUTHORITY_SOURCES = frozenset({"public_definitive", "nkt_designated"})
# 큐레이트/합성 프록시: 손작성 시나리오·LLM 리뷰. 감사에서 koipa_case_based의 판례 인용이 실사건과
# 무대응(사건번호 무관 재사용·자리표시자·시대착오)으로 확인 → 실세계 정답(legally grounded)·서빙
# 권위 출처로 취급 금지. 릴리스 차단 floor 회귀·학습 시드로만 쓴다.
SYNTHETIC_PROXY_SOURCES = frozenset({"koipa_case_based", "codex_review", "curated_scenario"})
# 손작성 합성 시나리오 텍스트의 self-tag(collect_public_gold.build_scenario_records가 record.source에
# 부여). 라벨 권위(고시 지정 등)가 정당해도 '본문'이 합성이면 real-text 실측 인용 불가 —
# is_external_authority의 2번째 축(2026-07-04 텍스처 갭 감사). label floor 역할은 tier_of가 유지.
SYNTHETIC_TEXT_SOURCE = "public_scenario"
# 법적근거/시나리오 출처(고등급 backbone) — 릴리스 차단 floor(tier 파생용 합집합).
_LEGAL_SOURCES = EXTERNAL_AUTHORITY_SOURCES | SYNTHETIC_PROXY_SOURCES
# 새 게이트(P1) 자동 통과 출처.
_CANDIDATE_SOURCES = {"rule_llm_agreement"}
# 사람 아님(머신/플레이스홀더 reviewer) — 엄격 판정(단일 소스). import_review_corrections.
# _is_machine_reviewer가 이 함수에 위임해 두 경로(eval/tier·CSV import) 강도를 통일한다.
# (이전엔 tier 경로가 느슨해 사람형 placeholder[human/reviewer/tbd/system]가 통과하는 구멍이었다.)
_MACHINE_PREFIXES = (
    "llm_judge",
    "ai_assist",
    "codex",
    "model",
    "machine",
    "public_gold",
    # 시연·리허설 마커(admin.DEMO_CREATED_BY='demo-console', parse_demo/시연 스크립트가 쓰는
    # actor.user_id)로 서명된 것은 사람 검수가 아니다. 이게 빠져 있어 데모 서명 산출물이
    # is_valid_signoff 를 통과하고 tier=locked_gold_eval 로 잡혔다(2026-08-08 실측) —
    # 정본에 병합되면 배포 게이트가 '사람 검증 평가셋이 있다'고 오판한다.
    "demo",
    "auto",
    "claude",
    "gpt",
    "qwen",
    "gemini",
    "anthropic",
    "openai",
    "bot",
    "judge",
)
# 정확매칭 placeholder(사람형 위조 통과 차단) — import 경로와 동일 집합.
_PLACEHOLDER_REVIEWERS = frozenset(
    {"", "human", "reviewer", "tbd", "-", "n/a", "na", "system", "ai", "ai_assist",
     "claude", "unknown", "none", "auto", "bot", "llm"}
)
# "aiden" 등 사람이름 오탐 방지: 머신 접두사 뒤 구분자(_/-)를 요구.
_MACHINE_REVIEWER_PREFIX = re.compile(r"^(ai|llm|gpt|claude|bot|auto)[_\-]")


def is_human_reviewer(reviewer_id: object) -> bool:
    """실계정 사람 검수자인가 — 엄격(placeholder·머신ID 모두 거부, fail-closed)."""
    rid = str(reviewer_id or "").strip().lower()
    if not rid or rid in _PLACEHOLDER_REVIEWERS:
        return False
    if "assist" in rid or _MACHINE_REVIEWER_PREFIX.match(rid):
        return False
    return not any(rid.startswith(p) for p in _MACHINE_PREFIXES)


def is_external_authority(record: dict) -> bool:
    """record가 '외부권위 정답'(사람서명 없이 실측 인용 가능)인가.

    두 축을 모두 요구한다 — 라벨 권위(external label)와 텍스트 실재(real text):
      1) source 수준(EXTERNAL_AUTHORITY_SOURCES) + nkt_designated는 지정근거(legal_reference)
         필수 — 근거 없는 손작성 시나리오가 nkt를 위조 사용해 버킷을 오염시킨 사례(2026-07-03
         감사) 재발 차단. koipa_case_based 등 SYNTHETIC_PROXY_SOURCES는 항상 False.
      2) 텍스트가 손작성 합성 시나리오(source=SYNTHETIC_TEXT_SOURCE)면 라벨 권위가 정당해도
         False — 지정고시가 TS '라벨'을 정당화해도 '본문'이 합성이면 real-text 실측 정확도로
         인용할 수 없다(텍스처 갭, capstone F1 0.26). 라벨 floor 역할은 tier_of가 legal_floor로
         별도 유지하되 '실측 인용 가능' 버킷에서는 제외(2026-07-04 감사).
    """
    src = record.get("label_source")
    if src not in EXTERNAL_AUTHORITY_SOURCES:
        return False
    if src == "nkt_designated" and not str(record.get("legal_reference") or "").strip():
        return False
    if str(record.get("source") or "") == SYNTHETIC_TEXT_SOURCE:
        return False
    return True


# 유효 서명(envelope) — promote_to_locked만 스탬프하는 gate_version/서명시각/독립검수자 목록을
# 묶어 검증한다. gate_version '존재'만 보면 파일에 값을 써넣어 우회 가능하므로 세 필드를 함께
# 요구한다(단, 세 값을 모두 손으로 위조하는 결정적 공격은 감사이벤트 대조[P1]로만 완전차단 —
# 여기선 손편집·correction-import 민팅·placeholder를 닫는다).
SUPPORTED_GATE_VERSIONS = frozenset({"human_signoff_v1"})  # golden_signoff.SIGNOFF_GATE_VERSION와 일치


def is_valid_signoff(record: dict) -> bool:
    """레코드가 인증 서명 envelope(promote_to_locked 산출)를 온전히 갖췄는가."""
    if record.get("label_source") != "human_review":
        return False
    if not is_human_reviewer(record.get("reviewer_id")):
        return False
    if record.get("gate_version") not in SUPPORTED_GATE_VERSIONS:
        return False
    if not str(record.get("signed_at") or "").strip():
        return False
    rids = record.get("reviewer_ids")
    return isinstance(rids, (list, tuple)) and len(rids) > 0


def document_origin(record: dict) -> str:
    """본문 실재성 축. 명시 document_origin 우선 → source에서 결정적 유도 → unknown(fail-closed)."""
    explicit = str(record.get("document_origin") or "").strip()
    if explicit in _VALID_ORIGINS and explicit != ORIGIN_UNKNOWN:
        return explicit
    src = str(record.get("source") or "").strip()
    if src in _SYNTHETIC_ORIGIN_SOURCES:
        return ORIGIN_SYNTHETIC
    if src in _CUSTOMER_REAL_ORIGIN_SOURCES:
        return ORIGIN_CUSTOMER_REAL
    if src in _PUBLIC_REAL_EXACT_SOURCES or any(src.startswith(p) for p in _PUBLIC_REAL_PREFIXES):
        return ORIGIN_PUBLIC_REAL
    return ORIGIN_UNKNOWN


def is_real_text_origin(record: dict) -> bool:
    """본문이 실문서(공개 실문서·고객 실문서)인가 — 합성/unknown은 False."""
    return document_origin(record) in _REAL_TEXT_ORIGINS


def is_real_locked_eval(record: dict) -> bool:
    """유효 서명 + **실문서 출처**. 평가셋 구성 보고(실문서 몇 건인지)에 쓴다.

    [2026-08-06 변경] 이 술어는 더 이상 eval pool 편입 조건이 아니다 — is_locked_eval 참조.
    """
    return is_valid_signoff(record) and is_real_text_origin(record)


def is_locked_eval(record: dict) -> bool:
    """평가정답인가 = **사람의 유효 서명**. 본문이 합성인지는 편입 조건이 아니다.

    [2026-08-06 결정] 종전엔 '실문서 출처'까지 요구해, 사람이 서명한 합성 고등급 문서가
    평가정답에 들어가지 못했다. 그런데 본 사업에서 발주처(관리자)는 **검수만** 수행하고
    실문서를 신규로 올리지 않는다 — 그 전제에서는 TS 평가정답이 **영원히 0** 이 되어
    배포 readiness 가 구조적으로 도달 불가였다(사람이 아무리 서명해도 게이트가 안 움직임).
    사람 서명이 곧 정답 확정이라는 원칙에 맞춰 편입 조건을 서명으로 단순화한다.

    대신 **평가셋 구성(실문서/합성 비율)은 항상 함께 보고**한다(eval_readiness 의
    real_per_grade/synthetic_per_grade). 수치를 인용할 때 구성으로 한정할 수 있게 하기 위함이며,
    구성 은폐가 아니라 명시가 목적이다.
    """
    return is_valid_signoff(record)


def tier_of(record: dict) -> str:
    """레코드의 논리 tier를 파생.

    human_review는 유효 서명(envelope)까지 요구하고, 못 넘으면 TIER_HELD로 격리한다 —
    SILVER(학습연료)로 흘러들지 않게(역-누수 차단). locked tier(서명 유효)라도 '실문서 출처'까지
    갖춰야 real 평가정답으로 집계된다(is_real_locked_eval; eval_records/eval_readiness가 강제)."""
    src = record.get("label_source")
    if src == "human_review":
        return TIER_LOCKED if is_valid_signoff(record) else TIER_HELD
    if src in _LEGAL_SOURCES:
        return TIER_LEGAL_FLOOR
    if src in _CANDIDATE_SOURCES or record.get("review_status") == "gold_candidate":
        return TIER_CANDIDATE
    return TIER_SILVER


def partition_by_tier(records: Sequence[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {
        TIER_LOCKED: [], TIER_LEGAL_FLOOR: [], TIER_CANDIDATE: [], TIER_SILVER: [], TIER_HELD: [],
    }
    for r in records:
        out[tier_of(r)].append(r)
    return out


def eval_records(
    records: Sequence[dict], *, allow_floor_fallback: bool = True,
) -> tuple[list[dict], str]:
    """평가 정답 = locked만. 없으면(9월 전) legal_floor를 interim으로 반환(2번째 값=실제 사용 tier).

    호출부는 반환된 tier가 legal_floor면 '템플릿 recall·일반화 아님'을 명시해 릴리스 게이트로만 쓴다.
    """
    locked = [r for r in records if is_locked_eval(r)]  # 사람 서명이 곧 평가정답
    if locked or not allow_floor_fallback:
        return locked, TIER_LOCKED
    floor = [r for r in records if tier_of(r) == TIER_LEGAL_FLOOR]
    return floor, TIER_LEGAL_FLOOR


# 학습 허용 tier(allowlist, fail-closed). LOCKED(평가정답)·HELD(격리)·미래 미지 tier는 기본 제외 —
# de-locked human_review가 `!= LOCKED` denylist로 학습에 새어들던 역효과를 원천 차단한다.
TRAIN_TIERS = frozenset({TIER_SILVER, TIER_CANDIDATE, TIER_LEGAL_FLOOR})


def train_records(records: Sequence[dict]) -> list[dict]:
    """학습 = silver + candidate + legal_floor + **평가에 쓰이지 않는 서명분**. held는 제외.

    제외 기준을 'locked tier'가 아니라 **is_real_locked_eval(실제 평가정답인가)** 로 잡는다.
    train-on-test 차단은 '평가에 쓰이는 레코드'를 학습에서 빼는 것이 목적인데, 종전 규칙은
    tier==LOCKED 전부를 뺐다. 그 결과 **사람이 서명한 합성문서**(예: 합성 TS)가
      · eval_records: 실문서 아님 → 평가 제외
      · train_records: locked tier → 학습 제외
    양쪽에서 빠져 **어디에도 쓰이지 않는 사각지대**가 됐다(2026-08-06 확인). 평가 pool 에 들어가지
    않는 레코드는 유출 위험이 없으므로 학습에 남긴다 — 사람 서명이 붙어 라벨 품질은 오히려 높다.
    두 함수가 같은 술어(is_real_locked_eval)에서 파생되므로 규칙이 어긋날 여지도 없다.
    held(서명 무효)는 종전대로 제외한다(역-누수 차단).
    """
    out: list[dict] = []
    for r in records:
        if is_locked_eval(r):               # 평가정답 → train-on-test 차단
            continue
        tier = tier_of(r)
        if tier in TRAIN_TIERS or tier == TIER_LOCKED:
            out.append(r)
    return out


# locked_gold_eval이 '실(real) 평가'로 인정되는 등급별 최소 표본 (readiness 기준).
DEFAULT_MIN_LOCKED_PER_GRADE = 5
_DEFAULT_GRADES = ("TS", "S1", "S2", "S3")


def eval_readiness(
    records: Sequence[dict],
    *,
    min_per_grade: int = DEFAULT_MIN_LOCKED_PER_GRADE,
    grades: Sequence[str] = _DEFAULT_GRADES,
) -> dict:
    """locked_gold_eval이 '실(real) 평가'로 쓸 만큼 충분한가 — readiness 게이트(죽음의 나선 #4).

    locked tier만 인정한다(legal_floor·합성 holdout은 실평가 아님). 각 등급이 min_per_grade
    이상이어야 ready. 무실데이터 단계에선 locked가 비어 ready=False → deploy gate가 합성
    test.jsonl만 보는(실분포 오염 맹목) 상태에서 **자동 활성(오염 자동승격)을 막는 근거**가 된다.
    locked가 운영 사람서명으로 충분히 쌓이면 ready=True → 그때 자동 활성을 허용한다.

    [2026-08-06] 편입 조건이 '서명'으로 단순화되면서(is_locked_eval) **게이트는 합성문서를
    포함해 센다.** 이 문단은 종전에 "게이트는 실문서 기준 그대로 두되 … 게이트를 완화하는 것이
    아니다"라고 적혀 있었으나 코드와 반대였다 — is_locked_eval 이 실문서 요건을 뗀 것이 곧
    게이트 완화이고, per_grade/ready 는 합성 서명분을 그대로 집계한다. 실측 2026-08-09:
    TS 5건이 **전부 합성**인데 ready=True 였다. 감리에서 이 docstring 만 읽으면 오판하므로
    사실대로 적는다.

    완화의 근거는 남아 있다: 발주처(관리자)는 검수만 하고 실문서를 신규 등록하지 않으므로
    실문서 요건을 두면 TS 평가정답이 영원히 0 이 되어 readiness 가 구조적으로 도달 불가였다.
    대신 구성을 **항상 함께 보고**한다(real_per_grade / synthetic_per_grade) — 합성으로 잰
    정확도는 실세계 성능이 아니므로(자체 실측: 합성학습→실문서 F1 0.26) 수치를 인용할 때
    반드시 구성으로 한정할 것. ready=True 가 곧 "실세계 평가 준비 완료"를 뜻하지 않는다.

    반환: {ready, per_grade, missing, reason, real_per_grade, synthetic_per_grade, ...}.
    (순수 함수 — DB/파일 불요, 단위테스트 가능.)
    """
    locked, _ = eval_records(records, allow_floor_fallback=False)  # locked tier만
    per_grade = {g: 0 for g in grades}
    for r in locked:
        code = r.get("label") or r.get("expected_grade")
        if code in per_grade:
            per_grade[code] += 1
    missing = [g for g in grades if per_grade[g] < min_per_grade]
    ready = bool(locked) and not missing

    # 평가셋 구성 — 같은 서명분을 실문서/합성으로 쪼개 보고한다(수치 인용 시 구성 한정용).
    real_per_grade = {g: 0 for g in grades}
    synth_per_grade = {g: 0 for g in grades}
    for r in locked:
        code = r.get("label") or r.get("expected_grade")
        if code not in real_per_grade:
            continue
        if is_real_text_origin(r):
            real_per_grade[code] += 1
        else:
            synth_per_grade[code] += 1

    return {
        "ready": ready,
        "per_grade": per_grade,
        "missing": missing,
        "reason": (
            "ok" if ready
            else ("no_locked_records" if not locked else f"insufficient_per_grade:{missing}")
        ),
        "real_per_grade": real_per_grade,
        "synthetic_per_grade": synth_per_grade,
        "real_total": sum(real_per_grade.values()),
        "synthetic_total": sum(synth_per_grade.values()),
    }
