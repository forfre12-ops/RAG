"""합성 gold_candidate 후속 분할 — 누출 가드 + 등급층화 75/25 holdout 분할 (정본 미변경).

설계(평가셋 앵커 중심 다층 평가, 2026-06-29 §05/§06 ⑤):
build_synthetic_golden 러너는 생성→합의게이트(gold_candidate)까지만 한다. 이 모듈은 그 산출을 받아:
  (1) 누출 가드 — 기존 train/eval holdout/gold와 본문 중복(정규화 text_hash) 드롭. 러너가
      build_golden_set에 holdout_texts를 안 넘겨 dropped_leaked=0이던 구멍을 여기서 닫는다.
      **holdout 오염은 C(eval_cards)·D 회귀의 의미를 무너뜨리므로 본 생성 전 필수.**
  (2) 등급층화 75/25 분할 — gold_candidate를 등급별로 silver_train(~75%)/synthetic_holdout(~25%)
      으로 가른다. **결정적**(text_hash 정렬 기반, 시드/난수 불요 → 재현 가능).
  (3) split_role 스탬프 — holdout엔 '일반화 아님(평가 진실 아님)' 명시.

**정본 미변경**: 입력 records를 변형하지 않고 새 dict를 낸다. split_role은 golden_tiers의 (파생)
truth-tier와 **직교** — label_source/review_status를 덮지 않아 tier_of()는 여전히 gold_candidate로 본다.
순수 함수 — DB/LLM 불요. 얇은 run-dir 엔트리(split_run_dir)만 파일 I/O.
"""
from __future__ import annotations

import json
import hashlib
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from koipa.hygiene import text_hash

SILVER = "silver_train"
HOLDOUT = "synthetic_holdout"
HOLDOUT_NOTE = "상대비교 holdout — 일반화 아님(평가 진실 아님)"
DEFAULT_HOLDOUT_FRAC = 0.25


@dataclass
class SplitResult:
    silver: list[dict]
    holdout: list[dict]
    dropped_leaked: int
    dropped_duplicate: int
    stats: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "silver": len(self.silver),
            "holdout": len(self.holdout),
            "dropped_leaked": self.dropped_leaked,
            "dropped_duplicate": self.dropped_duplicate,
            "stats": self.stats,
        }


def _grade_of(r: dict) -> object:
    return r.get("label") or r.get("grade")


def _text_of(r: dict, text_key: str) -> str:
    return str(r.get(text_key) or "")


def _family_of(r: dict, fallback: str) -> str:
    """Return the source/template family used for leakage-safe splitting.

    A high-fidelity proxy document can have unique text while still sharing the
    same source document or scenario template.  Legacy records without this new
    field retain one-record-per-family behaviour through ``fallback``.
    """
    family = str(r.get("document_family_id") or "").strip()
    return family or fallback


def _holdout_count(n: int, frac: float) -> int:
    """등급 n건 중 holdout 개수. n<2면 0(단일표본 분할 불가), 아니면 최소 1 보장."""
    if n < 2 or frac <= 0:
        return 0
    return max(1, round(frac * n))


def split_for_training(
    records: Sequence[dict],
    *,
    existing_texts: Sequence[str] = (),
    existing_family_ids: Sequence[str] = (),
    holdout_frac: float = DEFAULT_HOLDOUT_FRAC,
    grade_of: Callable[[dict], object] = _grade_of,
    text_key: str = "text",
) -> SplitResult:
    """gold_candidate records → silver_train / synthetic_holdout (누출드롭 + 등급층화 결정적 분할).

    단계: (1) 기존 코퍼스 누출 드롭(text_hash) → (2) 런 내 본문중복 드롭 → (3) 등급별 text_hash
    정렬 후 앞 holdout_frac을 holdout, 나머지 silver → (4) split_role 스탬프(정본 미변경, 새 dict).
    고등급(TS/S1)은 표본 희소라 holdout 0~1 가능 — stats에 등급별 수치를 그대로 노출(앵커 의존 신호).
    """
    existing = {text_hash(t) for t in existing_texts if t}
    existing_families = {str(value).strip() for value in existing_family_ids if str(value).strip()}
    seen: set[str] = set()
    dropped_leak = dropped_family_leak = dropped_dup = 0

    # (1)+(2) 누출·중복 드롭, 등급별 버킷팅 (결정적: text_hash로 정렬 키 보유)
    by_grade: dict[str, list[tuple[str, str, dict]]] = defaultdict(list)
    for r in records:
        text = _text_of(r, text_key)
        if not text.strip():
            continue
        h = text_hash(text)
        if h in existing:
            dropped_leak += 1
            continue
        if h in seen:
            dropped_dup += 1
            continue
        seen.add(h)
        family = _family_of(r, h)
        if family in existing_families:
            dropped_family_leak += 1
            continue
        by_grade[str(grade_of(r))].append((h, family, r))

    # A family is a global atomic unit even when it contains matched
    # counterfactuals from several grades.  Select holdout families by minimizing
    # distance from all per-grade targets at once.
    family_items: dict[str, list[tuple[str, str, dict]]] = defaultdict(list)
    grade_targets = {grade: _holdout_count(len(items), holdout_frac)
                     for grade, items in by_grade.items()}
    for grade, items in by_grade.items():
        for h, family, record in items:
            family_items[family].append((grade, h, record))

    def _stratum(grade: str, record: dict) -> str:
        scenario = str(record.get("scenario_id") or "").strip()
        return f"{grade}|{scenario}" if scenario else grade

    stratum_sizes: Counter[str] = Counter(
        _stratum(grade, record)
        for items in family_items.values()
        for grade, _, record in items
    )
    stratum_targets = {
        stratum: _holdout_count(size, holdout_frac)
        for stratum, size in stratum_sizes.items()
    }
    family_stratum_counts = {
        family: Counter(_stratum(grade, record) for grade, _, record in items)
        for family, items in family_items.items()
    }
    ordered_families = sorted(
        family_items,
        key=lambda value: (hashlib.sha256(value.encode("utf-8")).hexdigest(), value),
    )
    selected_families: set[str] = set()
    selected_counts: Counter[str] = Counter()

    def _distance(counts: Counter[str]) -> int:
        return sum(abs(stratum_targets[stratum] - counts.get(stratum, 0))
                   for stratum in stratum_targets)

    if len(ordered_families) > 1 and any(stratum_targets.values()):
        for family in ordered_families:
            if len(selected_families) >= len(ordered_families) - 1:
                break  # always leave at least one independent training family
            proposed = selected_counts + family_stratum_counts[family]
            if _distance(proposed) < _distance(selected_counts):
                selected_families.add(family)
                selected_counts = proposed
        if not selected_families:
            family = min(
                ordered_families,
                key=lambda value: (_distance(family_stratum_counts[value]), value),
            )
            selected_families.add(family)
            selected_counts = family_stratum_counts[family].copy()

    silver: list[dict] = []
    holdout: list[dict] = []
    for family, items in family_items.items():
        for _, _, record in items:
            if family in selected_families:
                holdout.append({**record, "split_role": HOLDOUT, "eval_note": HOLDOUT_NOTE})
            else:
                silver.append({**record, "split_role": SILVER})

    per_grade: dict[str, dict] = {}
    for grade, items in by_grade.items():
        grade_families = {family for _, family, _ in items}
        held = sum(1 for _, family, _ in items if family in selected_families)
        per_grade[grade] = {
            "n": len(items),
            "target_holdout": grade_targets[grade],
            "holdout": held,
            "silver": len(items) - held,
            "families": len(grade_families),
            "unsplittable_family": len(grade_families) == 1 and len(items) > 1,
        }
    per_stratum = {
        stratum: {
            "n": size,
            "target_holdout": stratum_targets[stratum],
            "holdout": selected_counts.get(stratum, 0),
        }
        for stratum, size in sorted(stratum_sizes.items())
    }

    train_families = {str(row.get("document_family_id") or text_hash(_text_of(row, text_key)))
                      for row in silver}
    holdout_families = {str(row.get("document_family_id") or text_hash(_text_of(row, text_key)))
                        for row in holdout}
    family_overlap = train_families & holdout_families
    if family_overlap:
        raise AssertionError(f"document family leakage across split: {sorted(family_overlap)}")

    stats = {
        "input": len(records),
        "kept": len(silver) + len(holdout),
        "silver": len(silver),
        "holdout": len(holdout),
        "dropped_leaked": dropped_leak,
        "dropped_family_leaked": dropped_family_leak,
        "dropped_duplicate": dropped_dup,
        "holdout_frac": holdout_frac,
        "per_grade": per_grade,
        "per_stratum": per_stratum,
        "holdout_by_grade": {g: v["holdout"] for g, v in per_grade.items()},
        "family_overlap": len(family_overlap),
        "unsplittable_families": {
            g: v["n"] for g, v in per_grade.items() if v["unsplittable_family"]
        },
        # 고등급 holdout이 0이면 앵커(legal_floor)에 의존해야 한다는 신호.
        "high_grade_holdout": per_grade.get("TS", {}).get("holdout", 0)
        + per_grade.get("S1", {}).get("holdout", 0),
    }
    return SplitResult(
        silver=silver, holdout=holdout,
        dropped_leaked=dropped_leak, dropped_duplicate=dropped_dup, stats=stats,
    )


def load_corpus_texts(paths: Sequence[str | Path], *, text_key: str = "text") -> list[str]:
    """기존 코퍼스 본문 로드(jsonl 또는 *.json 디렉토리) — 누출 가드 입력. 없는 경로는 건너뜀."""
    texts: list[str] = []
    for p in paths:
        path = Path(p)
        if not path.exists():
            continue
        files = [path] if path.is_file() else sorted(path.glob("*.json")) + sorted(path.glob("*.jsonl"))
        for f in files:
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                t = rec.get(text_key) or f"{rec.get('title','')}\n{rec.get('body','')}".strip()
                if t:
                    texts.append(t)
    return texts


def load_corpus_family_ids(paths: Sequence[str | Path]) -> list[str]:
    """Load declared source/template families from existing corpus paths."""
    families: list[str] = []
    for p in paths:
        path = Path(p)
        if not path.exists():
            continue
        files = [path] if path.is_file() else sorted(path.glob("*.json")) + sorted(path.glob("*.jsonl"))
        for f in files:
            for line in f.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                family = str(rec.get("document_family_id") or "").strip()
                if family:
                    families.append(family)
    return families


def split_run_dir(
    run_dir: str | Path,
    *,
    existing_corpus_paths: Sequence[str | Path] = (),
    holdout_frac: float = DEFAULT_HOLDOUT_FRAC,
    text_key: str = "text",
) -> SplitResult:
    """run-스코프 엔트리: golden_runs/<run_id>의 build_*.jsonl(gold_candidate)을 받아 분할.

    silver_train.jsonl / synthetic_holdout.jsonl / split_stats.json을 같은 run_dir에 기록(정본 미변경).
    existing_corpus_paths = 누출 가드 대상(기존 train/eval holdout/gold 경로).
    """
    rd = Path(run_dir)
    gold_files = sorted(rd.glob("build_*.jsonl"))
    records: list[dict] = []
    for gf in gold_files:
        for line in gf.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))

    existing = load_corpus_texts(existing_corpus_paths, text_key=text_key)
    existing_families = load_corpus_family_ids(existing_corpus_paths)
    result = split_for_training(
        records, existing_texts=existing, existing_family_ids=existing_families,
        holdout_frac=holdout_frac, text_key=text_key,
    )

    def _w(name: str, rows: list[dict]) -> None:
        with (rd / name).open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    _w("silver_train.jsonl", result.silver)
    _w("synthetic_holdout.jsonl", result.holdout)
    with (rd / "split_stats.json").open("w", encoding="utf-8") as f:
        json.dump(result.stats, f, ensure_ascii=False, indent=2)
    return result
