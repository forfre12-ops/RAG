"""Build exact, auditable evidence spans for adjudicated proxy documents.

This module only locates sentences that visibly support S/V/M factors.  It does
not decide their meaning: eligibility still requires an independent consensus
judge whose factor scores match the catalog's expected scores.  Missing or
ambiguous textual support fails closed into the uncertain bucket.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import Mapping

from lloydk.modules.m3_labeling.rule_engine import grade_from_svm


class ProxyEvidenceError(ValueError):
    """A record cannot provide exact, non-shortcut S/V/M evidence."""


_DIRECT_GRADE_MARKER = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:TS|S[1-3])(?:\s*(?:등급|급))?(?![A-Za-z0-9])"
    r"|특급\s*(?:기밀|비밀)|[1-3]\s*급\s*(?:비밀|기밀|대외비)"
    r"|(?:보안|비밀|기밀)\s*등급\s*[:：]?\s*(?:TS|S[1-3]|특급|[1-3]급)"
    r"|대외비|극비"
)
_SENTENCE_RE = re.compile(r"[^.!?\n]+(?:[.!?]+|$)")
_FACTOR_KEYWORDS = {
    "competitive_value": (
        "경쟁", "비용", "손실", "기간", "협상", "수익", "매출", "복제",
        "재현", "선점", "불이익", "절감", "원가", "개발", "사업",
    ),
    "nonpublicity": (
        "외부", "공개", "보유자", "알 수 없", "노출", "발표 전", "원자료",
        "전체 조건", "결합조건", "상세 수치", "내부 검토", "제외한다",
    ),
    "access_controls": (
        "권한", "승인", "조회", "열람", "반출", "회수", "로그", "기록",
        "공용", "구분 없이", "절차", "담당 부서", "계정", "저장소",
    ),
}
_LEVEL_KEYS = {
    "nonpublicity": "secrecy",
    "competitive_value": "value",
    "access_controls": "management",
}


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sentences(text: str) -> list[tuple[int, int, str]]:
    candidates: list[tuple[int, int, str]] = []
    for match in _SENTENCE_RE.finditer(text):
        raw = match.group(0)
        leading = len(raw) - len(raw.lstrip())
        trailing = len(raw) - len(raw.rstrip())
        start = match.start() + leading
        end = match.end() - trailing
        quote = text[start:end]
        if 12 <= len(quote) <= 240 and not _DIRECT_GRADE_MARKER.search(quote):
            candidates.append((start, end, quote))
    return candidates


def _choose_sentence(
    candidates: list[tuple[int, int, str]],
    factor: str,
    used: set[tuple[int, int]],
) -> tuple[int, int, str]:
    keywords = _FACTOR_KEYWORDS[factor]
    ranked: list[tuple[int, int, int, tuple[int, int, str]]] = []
    for candidate in candidates:
        start, end, quote = candidate
        if (start, end) in used:
            continue
        hits = sum(keyword in quote for keyword in keywords)
        if not hits:
            continue
        # Prefer multiple independent clues and a substantive, bounded quote;
        # preserve document order as the final deterministic tiebreaker.
        ranked.append((hits, min(len(quote), 160), -start, candidate))
    if not ranked:
        raise ProxyEvidenceError(f"missing exact text evidence for {factor}")
    return max(ranked)[-1]


def build_evidence_card(record: Mapping[str, object]) -> dict:
    """Create a ``proxy-evidence-v1`` card from exact canonical-text spans."""
    text = str(record.get("text") or "")
    label = str(record.get("intended_label") or record.get("label") or "")
    scores = record.get("expected_factor_scores")
    if not text.strip() or not isinstance(scores, Mapping):
        raise ProxyEvidenceError("text and expected_factor_scores are required")
    try:
        normalized_scores = {
            key: int(scores[key]) for key in ("secrecy", "value", "management")
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise ProxyEvidenceError("invalid expected_factor_scores") from exc
    if any(value not in {0, 1, 2} for value in normalized_scores.values()):
        raise ProxyEvidenceError("factor scores must be 0, 1, or 2")
    derived = grade_from_svm(**{
        "s": normalized_scores["secrecy"],
        "v": normalized_scores["value"],
        "m": normalized_scores["management"],
    })
    if derived != label:
        raise ProxyEvidenceError(f"factor scores derive {derived}, not {label}")

    preliminary = record.get("evidence_card")
    claims = preliminary if isinstance(preliminary, Mapping) else {}
    candidates = _sentences(text)
    used: set[tuple[int, int]] = set()
    factors: dict[str, dict] = {}
    # Competitive value is mandatory text evidence and is selected first.
    for factor in ("competitive_value", "nonpublicity", "access_controls"):
        start, end, quote = _choose_sentence(candidates, factor, used)
        used.add((start, end))
        factors[factor] = {
            "claim": str(claims.get(factor) or factor),
            "expected_level": normalized_scores[_LEVEL_KEYS[factor]],
            "basis": "text",
            "spans": [{
                "start": start,
                "end": end,
                "quote": quote,
                "quote_sha256": _sha256(quote),
            }],
        }

    minimum_spans = 2 if label in {"TS", "S1"} else 1
    all_spans = {
        (span["start"], span["end"])
        for factor in factors.values()
        for span in factor["spans"]
    }
    if len(all_spans) < minimum_spans:
        raise ProxyEvidenceError("insufficient distinct evidence spans")
    if label in {"TS", "S1"} and sum(end - start for start, end in all_spans) < 40:
        raise ProxyEvidenceError("high-grade evidence spans total fewer than 40 characters")
    duplicate_quotes = Counter(text[start:end] for start, end in all_spans)
    if any(count > 1 for count in duplicate_quotes.values()):
        raise ProxyEvidenceError("duplicate evidence quote")
    return {
        "schema": "proxy-evidence-v1",
        "text_sha256": _sha256(text),
        "factors": factors,
    }
