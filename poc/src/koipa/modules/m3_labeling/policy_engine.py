"""고객사 정책 엔진 — **사실**을 받아 **그 고객사 등급 후보**를 계산한다.

■ 왜 이 층이 따로 있는가 (2026-09-13 결정)

고객사마다 등급의 뜻이 다르다. 같은 문서가 A사에서는 "1급 비밀", B사에서는 "경영기밀",
C사에서는 "대외비"다. 그래서 **등급을 공통 골든셋으로 만들면 고객사 데이터가 섞이는 순간
정답이 충돌한다.** 실측으로도 확인됐다 — 공개 규정 3건으로 매핑표를 채워 보니 대조에 필요한
두 열이 **0/11** 이었고, 층위부터 안 맞았다(3·3·5단계 대 우리 4단계).
([[internal-rules-mapping-table-pilot-2026-09-12]])

그래서 층을 가른다:

    문서 → **사실** 추출/공급 → 고객사 정책 엔진 → 등급 **후보** → 사람 최종 확정

모델은 등급이 아니라 **사실**을 다룬다. 사실은 문서에 실제로 쓰여 있거나 시스템에 값으로
존재하므로 라벨이 내용을 따라간다 — 등급처럼 "붙이는 것"이 아니다. 2026-09-13 에 확인한
근인(합성 문서에 등급을 가를 내용이 없다)이 이 구조에서는 생기지 않는다.

■ 사실은 두 층이다 — 섞으면 안 된다

    본문에서 추출   공개 여부 · 가격/원가/입찰/설계도/소스코드 포함 여부 · 법정 보호 근거
    시스템이 공급   보안표식 · 소유자 · 접근범위(ACL) · DLP 라벨 · 실제 열람 범위

⚠ 보안표식·ACL 은 **본문에서 안 나온다.** 실측: 전 데이터셋 0건이고, 실문서 판정면에서
  관리성 어휘를 가진 것이 TS 9/33 · S1 2/22 · S2 1/17 뿐이다. 저장 위치와 권한 설정에 있다.
  이걸 "모델이 추출한다"고 두면 값이 없을 때 조용히 빈칸으로 흘러 **고등급이 구조적으로
  도달 불가**가 된다. 2026-09-13 실측: 관리성 서술을 지우니 정확도는 1.1%p 만 떨어졌는데
  **미탐이 1건 → 21건(23배)** 이었다.

■ 엔진이 지키는 것

    ① **증거가 없으면 하향하지 않는다.** 필요한 증거가 빠진 규칙은 "적용됨"이 아니라
       "확인 필요"로 남긴다. 없는 증거를 '조건 미충족'으로 읽으면 그대로 미탐이 된다.
    ② **재현 가능해야 한다.** 같은 사실 + 같은 정책 버전 → 항상 같은 결과. 검색·유사도 같은
       확률적 요소를 판정 경로에 넣지 않는다(벡터 거리로 등급을 가르는 AUROC 0.460 실측).
    ③ **왜 그 등급인지 남긴다.** 적용 규칙 id · 충족한 조건 · 빠진 증거를 함께 돌려준다.
    ④ **정책 버전과 시행일을 결과에 박는다.** 나중에 "그때 무슨 규칙으로 그랬나"에 답해야 한다.

⛔ 이 엔진은 **등급을 확정하지 않는다.** 후보를 낼 뿐이고 확정은 사람이 한다(FUN-005).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 조건 연산자 — 결정형만 둔다. "비슷하다" 류는 넣지 않는다.
OPERATORS = ("eq", "ne", "in", "not_in", "gte", "lte", "exists", "missing", "contains_any")

# 사실 이름의 두 층. 검증에서 "추출"과 "공급"을 구분해 보여 주기 위한 것이고,
# 엔진 동작은 같다(둘 다 그냥 사실이다).
EXTRACTED_FACTS = (
    "public_disclosed", "content_kinds", "legal_protection_basis", "has_concrete_parameters",
)
SUPPLIED_FACTS = (
    "security_marking", "access_scope", "owner_org", "dlp_label", "actual_reader_scope",
)


@dataclass
class Rule:
    id: str
    grade: str
    priority: int = 100
    when: dict[str, Any] = field(default_factory=dict)
    requires_evidence: tuple[str, ...] = ()
    note: str = ""


@dataclass
class Policy:
    org_id: str
    version: str
    effective_date: str
    # 민감한 것부터 낮은 것 순서. **하향 금지**를 판단하려면 순서가 반드시 있어야 한다.
    grade_order: tuple[str, ...] = ()
    rules: tuple[Rule, ...] = ()
    default_grade: str = ""
    note: str = ""

    def severity(self, grade: str) -> int:
        """민감도. 목록에 없으면 -1(비교에서 빠진다)."""
        return len(self.grade_order) - self.grade_order.index(grade) if grade in self.grade_order else -1


@dataclass
class Proposal:
    """엔진의 답. **확정이 아니라 후보다.**"""

    org_id: str
    policy_version: str
    effective_date: str
    grade: str | None
    rule_id: str | None
    matched: tuple[str, ...] = ()
    missing_evidence: tuple[str, ...] = ()
    blocked_rules: tuple[str, ...] = ()
    needs_review: bool = False
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "org_id": self.org_id, "policy_version": self.policy_version,
            "effective_date": self.effective_date, "grade": self.grade,
            "rule_id": self.rule_id, "matched": list(self.matched),
            "missing_evidence": list(self.missing_evidence),
            "blocked_rules": list(self.blocked_rules),
            "needs_review": self.needs_review, "reason": self.reason,
        }


def _test(op: str, value: Any, expected: Any) -> bool:
    if op == "exists":
        return value not in (None, "", [], {})
    if op == "missing":
        return value in (None, "", [], {})
    if value is None:
        return False
    if op == "eq":
        return value == expected
    if op == "ne":
        return value != expected
    if op == "in":
        return value in (expected or [])
    if op == "not_in":
        return value not in (expected or [])
    if op == "gte":
        return float(value) >= float(expected)
    if op == "lte":
        return float(value) <= float(expected)
    if op == "contains_any":
        got = value if isinstance(value, (list, tuple, set)) else [value]
        return any(x in got for x in (expected or []))
    raise ValueError(f"모르는 연산자: {op}")


def _condition_results(rule: Rule, facts: dict) -> tuple[list[str] | None, list[str]]:
    """(충족한 조건, 미확인 사실). 첫 값 None은 확정된 AND 불충족이다."""
    matched: list[str] = []
    unknown: list[str] = []
    for fact, spec in rule.when.items():
        op = str(spec.get("op", "eq"))
        expected = spec.get("value")
        present = fact in facts and facts[fact] not in (None, "", [], {})
        # exists/missing 은 '값 없음' 자체를 묻는 조건이라 unknown 으로 빼지 않는다.
        if not present and op not in ("exists", "missing"):
            unknown.append(fact)
            continue
        if _test(op, facts.get(fact), expected):
            matched.append(f"{fact} {op} {expected!r}" if expected is not None else f"{fact} {op}")
        else:
            # [2026-09-13] **불충족이 확정되면 unknown 을 들고 나가지 않는다.**
            # 종전에는 `return [], unknown` 이라 앞서 쌓인 미확인이 그대로 나갔고,
            # 그 결과 **조건 작성 순서에 따라 검수 여부가 달라졌다**(외부 리뷰가 재현):
            #   접근범위(미확인) 먼저 → needs_review=True
            #   공개여부(불충족) 먼저 → needs_review=False
            # 한 조건이라도 '아님' 이 확인되면 그 규칙은 순서와 무관하게 해당 없음이고,
            # 남은 미확인은 이 규칙의 판단에 더 이상 영향을 주지 않는다.
            return None, []
    return matched, unknown


def evaluate(policy: Policy, facts: dict) -> Proposal:
    """사실 → 그 고객사 등급 **후보**.

    ⚠ 증거가 빠진 규칙은 '해당 없음'이 아니라 **보류**로 다룬다. 빠진 증거를 '조건 미충족'
      으로 읽으면 더 낮은 규칙이 이겨서 **하향**이 된다 — 그것이 곧 미탐이다.
    """
    base = {"org_id": policy.org_id, "policy_version": policy.version,
            "effective_date": policy.effective_date}
    blocked: list[str] = []
    all_missing: list[str] = []
    conflicting: list[str] = []
    best: tuple[int, Rule, list[str]] | None = None

    for rule in sorted(policy.rules, key=lambda r: (r.priority, r.id)):
        matched, unknown = _condition_results(rule, facts)
        # 확정된 불충족은 별도 requires_evidence 부재로 다시 보류시키지 않는다.
        # unknown과 false를 구별해야 조건 순서뿐 아니라 증거 선언에도 일관된다.
        if matched is None:
            continue
        missing = [e for e in rule.requires_evidence
                   if facts.get(e) in (None, "", [], {})] + unknown
        if missing:
            # 이 규칙은 **판단 불가**다. 아래 규칙이 그냥 이기게 두지 않는다.
            blocked.append(rule.id)
            all_missing.extend(missing)
            continue
        if not matched:
            continue
        if best is None:
            best = (rule.priority, rule, matched)
        elif rule.priority == best[0] and rule.grade != best[1].grade:
            # [2026-09-13] 같은 우선순위인데 등급이 다르면 **규칙 id 사전순**으로 골라
            # 낮은 등급이 이기던 버그가 있었다(외부 리뷰 재현: A-low(S2) 가 B-high(TS) 를 이김).
            # 조용한 하향은 곧 미탐이다. FNR-safe 로 **더 민감한 등급**을 택하고 충돌을 남긴다.
            # 정책은 애초에 이런 충돌이 없어야 하므로 check_org_policy_rules 가 발행 시 잡는다.
            conflicting.append(f"{best[1].id}({best[1].grade})~{rule.id}({rule.grade})")
            if policy.severity(rule.grade) > policy.severity(best[1].grade):
                best = (rule.priority, rule, matched)

    missing_unique = tuple(dict.fromkeys(all_missing))

    if best is None:
        return Proposal(
            **base, grade=policy.default_grade or None, rule_id=None,
            missing_evidence=missing_unique, blocked_rules=tuple(blocked),
            needs_review=bool(blocked),
            reason=("증거가 없어 판단하지 못한 규칙이 있다 — 하향하지 않고 검수로 보낸다"
                    if blocked else "해당하는 규칙이 없다 — 기본 등급"),
        )

    _prio, rule, matched = best
    # 보류된 규칙 중 **지금 후보보다 높은 등급**이 있으면, 그 가능성을 닫지 않는다.
    higher_blocked = [
        r.id for r in policy.rules
        if r.id in blocked and policy.severity(r.grade) > policy.severity(rule.grade)
    ]
    return Proposal(
        **base, grade=rule.grade, rule_id=rule.id, matched=tuple(matched),
        missing_evidence=missing_unique, blocked_rules=tuple(blocked),
        needs_review=bool(higher_blocked) or bool(conflicting),
        reason=(("증거가 없어 더 높은 등급 규칙(%s)을 판단하지 못했다 — 검수 필요"
                 % ", ".join(higher_blocked)) if higher_blocked
                else ("같은 우선순위에서 등급이 갈린다(%s) — 더 민감한 쪽을 택하고 검수로 보낸다"
                      % ", ".join(conflicting)) if conflicting
                else "규칙 %s 충족" % rule.id),
    )


def load(path: str | Path) -> Policy:
    data: dict[str, Any] = json.loads(Path(path).read_text(encoding="utf-8"))
    rules = tuple(
        Rule(
            id=str(r.get("id") or ""), grade=str(r.get("grade") or ""),
            priority=int(r.get("priority", 100)), when=dict(r.get("when") or {}),
            requires_evidence=tuple(r.get("requires_evidence") or ()),
            note=str(r.get("note") or ""),
        )
        for r in (data.get("rules") or []) if not str(r.get("id", "")).startswith("_")
    )
    return Policy(
        org_id=str(data.get("org_id") or ""), version=str(data.get("policy_version") or ""),
        effective_date=str(data.get("effective_date") or ""),
        grade_order=tuple(data.get("grade_order") or ()), rules=rules,
        default_grade=str(data.get("default_grade") or ""), note=str(data.get("note") or ""),
    )


def validate(policy: Policy) -> list[str]:
    """정책을 받자마자 검사한다. 빈 목록이면 쓸 수 있다.

    ⚠ "규칙이 있는가"만 보지 않는다. **순서·연산자·등급 이름**이 맞아야 하향 금지가 성립한다.
    """
    issues: list[str] = []
    if not policy.org_id.strip():
        issues.append("org_id 가 비어 있습니다")
    if not policy.version.strip():
        issues.append("policy_version 이 비어 있습니다 — 나중에 '그때 무슨 규칙이었나'에 답할 수 없습니다")
    if not policy.effective_date.strip():
        issues.append("effective_date(시행일)가 비어 있습니다")
    if len(policy.grade_order) < 2:
        issues.append("grade_order(민감한 것부터의 순서)가 없습니다 — "
                      "순서가 없으면 '증거 없을 때 하향 금지'를 판단할 수 없습니다")
    if not policy.rules:
        issues.append("규칙이 한 건도 없습니다")

    seen: set[str] = set()
    for rule in policy.rules:
        if not rule.id:
            issues.append("id 가 없는 규칙이 있습니다")
        elif rule.id in seen:
            issues.append(f"규칙 id 중복: {rule.id}")
        seen.add(rule.id)
        if policy.grade_order and rule.grade not in policy.grade_order:
            issues.append(f"{rule.id}: 등급 '{rule.grade}' 가 grade_order 에 없습니다")
        if not rule.when:
            issues.append(f"{rule.id}: 조건(when)이 비어 있습니다 — 모든 문서에 걸립니다")
        for fact, spec in rule.when.items():
            op = str((spec or {}).get("op", "eq"))
            if op not in OPERATORS:
                issues.append(f"{rule.id}: 모르는 연산자 '{op}' (쓸 수 있는 것: {', '.join(OPERATORS)})")
            if fact not in EXTRACTED_FACTS + SUPPLIED_FACTS:
                issues.append(f"{rule.id}: 모르는 사실 이름 '{fact}' — "
                              "정해진 사실만 쓸 수 있습니다(오타면 그 조건이 조용히 무시됩니다)")
    if policy.default_grade and policy.grade_order and policy.default_grade not in policy.grade_order:
        issues.append(f"default_grade '{policy.default_grade}' 가 grade_order 에 없습니다")
    return issues
