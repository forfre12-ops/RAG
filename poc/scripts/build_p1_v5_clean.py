"""[v5_clean] P1 분류 학습셋 정본 재빌드 — 원천 풀부터 dedup·출처보존·누출0 분할.

배경(왜 v5인가): 배포 학습셋 v4_clean은 당시 합리적 기준선이지만 지금 기준으론 최선이 아니다.
재감사(2026-07-26)에서 확증된 결함:
  - 라벨 정합성: 교정된 골드(판례 157건→S3)가 미반영, 동일텍스트·다른라벨 충돌 202그룹/808행.
  - 판례 편향: 60.1%(3550행)가 판례형·그중 2365가 고등급, 2371이 source=synthetic로 위장 →
    모델이 '비밀성'이 아니라 '법률 문체'를 고등급 신호로 학습.
  - 평가 독립성: train↔oss val 41·test 52건 정확겹침(test 32건 라벨충돌) → 내부성능 0.783 허수.
  - provenance 소실: 빌더가 {text,label,source,doc_id}로 축약, document_origin/rule_grade/
    review_status 학습셋에 0건 전달.
  - 커버리지: 한국어 100%(영어 균형 120건이 영어 S1 recall 0/6→5/6), 긴문서 512토큰 truncation.

그래서 이 빌더는 v4_clean에 데이터를 '더 붙이지' 않고, **원천 풀부터** 다시 만든다:
  1) 모든 소스 결합(oss train+val+test·rag_corpus_v2·gold_real[학습tier]·균형 영어).
  2) 독립 평가셋(holdout/hardened/authority)과 겹치는 텍스트는 풀에서 제외(평가 독립성 보존).
  3) 공개 판결문은 결정 규칙으로 S3 고정(label_original 보존·relabel_basis 스탬프).
  4) 정규화 텍스트 기준 dedup + tier 권위로 라벨 충돌 결정 해소(물리 중복·모순 라벨 제거).
  5) 판례 비중을 운영 트래픽 가정치(≈15%)로 캡(결정론적 다운샘플).
  6) 정제 상태에서 균형 영어 추가.
  7) 물리 3회 복제 폐지 → 라벨 신뢰도(tier)·희소도 기반 sample_weight.
  8) tier·document_origin·rule_grade·review_status·doc_id 등 provenance 보존.
  9) 정제 후 전체 풀을 stratified train/val/test로 분할 + **정확중복 0건 게이트**.
 10) 긴문서는 long_doc 플래그+truncation_strategy=head_tail 힌트(트레이너가 존중).

승격 게이트(이 빌더 밖): 재학습 후 hardened holdout + 공개문서 FPR + adversarial veto 동시 통과 시에만.
결정론: 모든 순서/샘플링은 md5(정규화텍스트) 기반 → 재실행 동일 출력(파이썬 hash salt 회피).

사용:
  python scripts/build_p1_v5_clean.py                 # 정본 산출(datasets/labeled_p1_v5_clean/)
  python scripts/build_p1_v5_clean.py --dry-run       # 통계만, 파일 무기록
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

# Windows cp949 콘솔에서 em-dash 등 UTF-8 출력 크래시 방지(파일 기록은 항상 UTF-8).
for _stream in ("stdout", "stderr"):
    _s = getattr(sys, _stream)
    if _s and _s.encoding and _s.encoding.lower() not in ("utf-8", "utf-8-sig"):
        setattr(sys, _stream, io.TextIOWrapper(_s.buffer, encoding="utf-8", errors="replace"))

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from koipa.golden_tiers import (  # noqa: E402
    TIER_CANDIDATE,
    TIER_LEGAL_FLOOR,
    TIER_SILVER,
    TRAIN_TIERS,
    document_origin,
    is_external_authority,
    tier_of,
)

LABELS = {"TS", "S1", "S2", "S3"}

# ── 공개 판결문 결정 규칙 ────────────────────────────────────────────────────
# 판결문/심결문(법원·특허심판원이 스스로 쓴 공개 결정문)은 비공지성 결여로 정의상 S3.
# 두 경로로 탐지: (a) document_origin=public_real(source=판례*/금융보고서) — 결정적,
# (b) 본문에 소송절차 마커가 3종 이상 — source=synthetic으로 위장된 판례 프록시를 잡음.
# 회사 내부 소송메모 오탐 방지: 마커는 '법원이 판결문을 쓸 때' 나오는 어휘 중심. 실측(2026-07-26)
# 상 고등급 synthetic-court 1868건 중 1821건이 강한 판결서명 보유·표본 전수 공개판결문 확인.
_COURT_MARKERS = ("원고", "피고", "상고", "판결", "심결", "주문", "피고인", "항소", "대법원", "선고")


def _court_marker_hits(text: str) -> int:
    return sum(1 for m in _COURT_MARKERS if m in (text or ""))


def is_public_ruling(record: dict) -> bool:
    """레코드 본문이 공개 판결문/심결문인가 — 결정적(저장필드+마커, 네트워크·모델 불요)."""
    if document_origin(record) == "customer_real":
        return False  # 고객 실문서는 절대 자동강등 금지(안전)
    if document_origin(record) == "public_real" and str(record.get("source") or "").startswith("판례"):
        return True
    return _court_marker_hits(record.get("text") or "") >= 3


# ── 텍스트 정규화(dedup 키) ──────────────────────────────────────────────────
_WS = re.compile(r"\s+")


def norm_text(t: str) -> str:
    return _WS.sub(" ", (t or "")).strip()


def _stable_int(s: str) -> int:
    """파이썬 hash()는 실행마다 salt가 달라 재현 불가 → md5로 결정적 정수."""
    return int(hashlib.md5(s.encode("utf-8")).hexdigest(), 16)


def _is_english_heavy(t: str) -> bool:
    t = t or ""
    latin = sum(1 for c in t if "a" <= c.lower() <= "z")
    alpha = sum(1 for c in t if c.isalpha())
    return alpha > 0 and latin / alpha >= 0.5


# ── 로더: 각 소스를 provenance 보존 스키마로 정규화 ──────────────────────────
def _jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(json.loads(line))
    return out


def _base_record(r: dict, *, origin_dataset: str, label: str) -> dict:
    """provenance 보존 정규화 레코드. tier/document_origin은 파생값으로 계산."""
    return {
        "text": (r.get("text") or "").strip(),
        "label": label,
        "source": r.get("source") or origin_dataset,
        "doc_id": r.get("doc_id"),
        "domain": r.get("domain", ""),
        "label_source": r.get("label_source"),
        "tier": tier_of(r) if r.get("label_source") else TIER_SILVER,
        "document_origin": document_origin(r),
        "rule_grade": r.get("rule_grade"),
        "review_status": r.get("review_status"),
        "origin_dataset": origin_dataset,
    }


def load_oss(paths: list[Path]) -> list[dict]:
    rows = []
    for p in paths:
        for r in _jsonl(p):
            if r.get("text") and r.get("label") in LABELS:
                rows.append(_base_record(r, origin_dataset="labeled_oss_v1", label=r["label"]))
    return rows


def load_rag(corpus_dir: Path) -> tuple[list[dict], int]:
    rows, skipped = [], 0
    for f in sorted(corpus_dir.glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        body = (d.get("body") or "").strip()
        label = d.get("target_grade")
        if not d.get("label_match", False) or not body or label not in LABELS:
            skipped += 1
            continue
        rec = {"text": body, "label": label, "domain": d.get("domain", ""),
               "source": "rag_corpus_v2", "label_source": "rag_corpus_v2"}
        rows.append(_base_record(rec, origin_dataset="rag_corpus_v2", label=label))
    return rows, skipped


def load_gold(path: Path) -> tuple[list[dict], int]:
    """gold_real → 학습 허용 tier만(locked=평가정답·held=격리 제외). 디오염 라벨 그대로 사용."""
    rows, dropped_non_train = [], 0
    for r in _jsonl(path):
        if tier_of(r) not in TRAIN_TIERS:
            dropped_non_train += 1
            continue
        label = r.get("label") or r.get("expected_grade")
        if not (r.get("text") or "").strip() or label not in LABELS:
            continue
        rows.append(_base_record(r, origin_dataset="gold_real", label=label))
    return rows, dropped_non_train


def load_english(path: Path, per_grade: int) -> list[dict]:
    """균형 영어 커버리지 — 등급별 per_grade건 결정론적 선별(영어 S1 recall 레버)."""
    if not path.exists():
        return []
    eng = [r for r in _jsonl(path) if r.get("label") in LABELS and _is_english_heavy(r.get("text"))]
    # 정규화 텍스트로 먼저 유니크화(bilingual 셋은 텍스트당 4회 중복 → 미유니크화 시 dedup이 대부분 흡수).
    by_grade: dict[str, dict[str, dict]] = defaultdict(dict)
    for r in eng:
        by_grade[r["label"]].setdefault(norm_text(r.get("text")), r)
    picked = []
    for g in ("TS", "S1", "S2", "S3"):
        cand = sorted(by_grade.get(g, {}).values(), key=lambda r: _stable_int(norm_text(r.get("text"))))
        for r in cand[:per_grade]:
            rec = {"text": r["text"], "label": g, "source": "bilingual_en",
                   "label_source": "bilingual_en", "domain": "en"}
            picked.append(_base_record(rec, origin_dataset="bilingual_en", label=g))
    return picked


# ── 판례→S3 결정 규칙 적용(스탬프) ──────────────────────────────────────────
def apply_public_ruling_rule(rows: list[dict]) -> int:
    n = 0
    for r in rows:
        if r["label"] != "S3" and is_public_ruling(r):
            r["label_original"] = r["label"]
            r["label"] = "S3"
            r["relabel_basis"] = "public_ruling_S3: 공개 판결문/심결문은 비공지성 결여로 정의상 S3"
            r["is_court"] = True
            n += 1
        else:
            r.setdefault("is_court", is_public_ruling(r))
    return n


# ── dedup + 라벨 충돌 해소(tier 권위 → 다수결 → 안전 tiebreak) ───────────────
_TIER_RANK = {TIER_LEGAL_FLOOR: 3, TIER_CANDIDATE: 2, TIER_SILVER: 1}
_SEVERITY = {"TS": 3, "S1": 2, "S2": 1, "S3": 0}


def _authority(r: dict) -> int:
    base = _TIER_RANK.get(r.get("tier"), 1)
    return base + (1 if is_external_authority(r) else 0)


def resolve_group(group: list[dict]) -> tuple[dict, bool]:
    """동일 텍스트 그룹 → 단일 레코드. 라벨 결정: 최고권위 tier의 라벨, 동률이면 다수결,
    그래도 동률이면 most-severe(안전). 반환 레코드는 가장 풍부한 provenance를 유지."""
    labels = {r["label"] for r in group}
    conflict = len(labels) > 1
    top = max(_authority(r) for r in group)
    top_rows = [r for r in group if _authority(r) == top]
    votes = Counter(r["label"] for r in top_rows)
    best = max(votes.items(), key=lambda kv: (kv[1], _SEVERITY[kv[0]]))[0]
    # provenance가 가장 풍부한 레코드를 대표로(필드 채워진 수 기준), 라벨은 결정값으로 덮음
    rep = max(group, key=lambda r: sum(1 for v in r.values() if v not in (None, "", [])))
    rep = dict(rep)
    rep["label"] = best
    rep["dup_count"] = len(group)
    rep["origin_datasets"] = sorted({r["origin_dataset"] for r in group})
    if conflict:
        rep["label_collision"] = sorted(labels)
        rep["collision_resolved_by"] = "tier_authority>majority>severity"
    return rep, conflict


def dedup(rows: list[dict]) -> tuple[list[dict], int, int]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[norm_text(r["text"])].append(r)
    out, conflicts = [], 0
    for key in sorted(groups):  # 결정론적 순서
        rep, conflict = resolve_group(groups[key])
        conflicts += 1 if conflict else 0
        out.append(rep)
    removed = len(rows) - len(out)
    return out, removed, conflicts


# ── 판례 비중 캡(결정론적 다운샘플) ─────────────────────────────────────────
def cap_court(rows: list[dict], max_frac: float) -> tuple[list[dict], int]:
    court = [r for r in rows if r.get("is_court")]
    other = [r for r in rows if not r.get("is_court")]
    if not court:
        return rows, 0
    # 목표: court / (court+other) <= max_frac  →  court_keep <= max_frac/(1-max_frac) * other
    court_keep = min(len(court), int(round(max_frac / (1 - max_frac) * len(other))))
    court_sorted = sorted(court, key=lambda r: _stable_int(norm_text(r["text"])))
    kept = court_sorted[:court_keep]
    dropped = len(court) - len(kept)
    return other + kept, dropped


# ── sample_weight: tier 신뢰도 × 클래스 희소도(물리 복제 대체) ──────────────
def assign_weights(rows: list[dict]) -> None:
    tier_w = {TIER_LEGAL_FLOOR: 1.0, TIER_CANDIDATE: 0.85, TIER_SILVER: 0.6}
    counts = Counter(r["label"] for r in rows)
    med = sorted(counts.values())[len(counts) // 2] if counts else 1
    for r in rows:
        base = tier_w.get(r.get("tier"), 0.6)
        if is_external_authority(r):
            base = 1.0
        if r.get("origin_dataset") == "bilingual_en":
            base = max(base, 1.0)  # 영어 희소 커버리지 부스트
        rarity = min(2.0, med / max(1, counts[r["label"]]))  # 희소 클래스(S1/TS) 가중, 상한 2x
        r["sample_weight"] = round(base * (0.5 + 0.5 * rarity), 3)


# ── stratified 분할(정확중복 0 보장: unique 텍스트를 정확히 한 split에) ──────
def split_rows(rows: list[dict], val_frac: float, test_frac: float) -> dict[str, list[dict]]:
    by_label: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_label[r["label"]].append(r)
    out = {"train": [], "val": [], "test": []}
    for label in sorted(by_label):
        grp = sorted(by_label[label], key=lambda r: _stable_int(norm_text(r["text"])))
        n = len(grp)
        n_test = int(round(n * test_frac))
        n_val = int(round(n * val_frac))
        out["test"].extend(grp[:n_test])
        out["val"].extend(grp[n_test:n_test + n_val])
        out["train"].extend(grp[n_test + n_val:])
    return out


def _long_doc_annotate(rows: list[dict], char_thresh: int) -> None:
    for r in rows:
        n = len(r["text"])
        r["char_len"] = n
        if n > char_thresh:
            r["long_doc"] = True
            r["truncation_strategy"] = "head_tail"


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")


def _dist(rows: list[dict], key: str) -> dict:
    return dict(sorted(Counter(r.get(key) for r in rows).items(), key=lambda kv: str(kv[0])))


def main() -> int:
    ap = argparse.ArgumentParser(description="v5_clean P1 학습셋 재빌드(결정론·provenance보존·누출0).")
    ap.add_argument("--oss-dir", default="datasets/labeled_oss_v1")
    ap.add_argument("--rag-corpus", default="datasets/rag_corpus_v2")
    ap.add_argument("--gold-real", default="datasets/gold_real/classification_gold.jsonl")
    ap.add_argument("--bilingual", default="datasets/labeled_p1_bilingual_exp/train.jsonl")
    ap.add_argument("--eval-exclude", nargs="*", default=[
        "datasets/gold_real/holdout_eval.jsonl",
        "datasets/gold_real/holdout_eval.hardened.jsonl",
        "reports/_rank/_authority_eval.jsonl",
    ])
    ap.add_argument("--english-per-grade", type=int, default=30)  # 등급별 30 유니크 → 균형 영어 120(입증 레시피)
    ap.add_argument("--court-max-frac", type=float, default=0.15)
    ap.add_argument("--long-doc-chars", type=int, default=2000)
    ap.add_argument("--val-frac", type=float, default=0.10)
    ap.add_argument("--test-frac", type=float, default=0.10)
    ap.add_argument("--out-dir", default="datasets/labeled_p1_v5_clean")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    # 1) 소스 결합(oss train+val+test 통합 — 구 split은 이미 누출)
    oss = load_oss([Path(args.oss_dir) / f"{s}.jsonl" for s in ("train", "val", "test")])
    rag, rag_skipped = load_rag(Path(args.rag_corpus))
    gold, gold_dropped = load_gold(Path(args.gold_real))
    english = load_english(Path(args.bilingual), args.english_per_grade)
    pool = oss + rag + gold + english
    n_raw = len(pool)

    # 2) 독립 평가셋과 겹치는 텍스트 제외(평가 독립성 보존)
    eval_texts: set[str] = set()
    for p in args.eval_exclude:
        for r in _jsonl(Path(p)):
            t = r.get("text") or r.get("body")
            if t:
                eval_texts.add(norm_text(t))
    pool = [r for r in pool if norm_text(r["text"]) not in eval_texts]
    n_after_evalcut = len(pool)

    # 3) 공개 판결문 → S3 결정 규칙(스탬프)
    relabeled = apply_public_ruling_rule(pool)

    # 4) dedup + 라벨 충돌 해소
    pool, dup_removed, conflicts = dedup(pool)

    # 5) 판례 비중 캡
    pool, court_dropped = cap_court(pool, args.court_max_frac)

    # 6) sample_weight + long-doc 주석
    assign_weights(pool)
    _long_doc_annotate(pool, args.long_doc_chars)

    # 7) stratified 분할
    splits = split_rows(pool, args.val_frac, args.test_frac)

    # ── 게이트 검증 ──────────────────────────────────────────────────────────
    tr, va, te = (set(norm_text(r["text"]) for r in splits[s]) for s in ("train", "val", "test"))
    leak_val = len(tr & va)
    leak_test = len(tr & te)
    leak_vt = len(va & te)
    court_frac = sum(1 for r in pool if r.get("is_court")) / max(1, len(pool))
    provenance_ok = all((r.get("tier") and r.get("document_origin")) for r in pool)
    # 진짜 불변식: 정제 후 동일 정규화텍스트가 서로 다른 라벨을 갖는 그룹이 0이어야 함(모순 라벨 근절).
    _lbl_by_text: dict[str, set] = defaultdict(set)
    for r in pool:
        _lbl_by_text[norm_text(r["text"])].add(r["label"])
    unresolved_collisions = sum(1 for v in _lbl_by_text.values() if len(v) > 1)
    gates = {
        "cross_split_exact_overlap": {"train_val": leak_val, "train_test": leak_test, "val_test": leak_vt},
        "unresolved_label_collisions": unresolved_collisions,  # 0이어야 통과(동일텍스트=단일라벨)
        "rows_with_collision_audit_stamp": sum(1 for r in pool if r.get("label_collision")),  # 정보용(해소완료 감사추적)
        "court_fraction": round(court_frac, 4),
        "court_cap": args.court_max_frac,
        "provenance_present": provenance_ok,
        "eval_independence_rows_removed": n_raw - n_after_evalcut,
        "PASS": leak_val == 0 and leak_test == 0 and leak_vt == 0 and unresolved_collisions == 0
        and court_frac <= args.court_max_frac + 1e-9 and provenance_ok,
    }

    manifest = {
        "purpose": "v5_clean: 원천풀 dedup·출처보존·공개판결S3·판례캡·균형영어·누출0 split 재빌드.",
        "built_from": {
            "oss": {"dir": args.oss_dir, "rows": len(oss), "note": "train+val+test 통합 후 재분할"},
            "rag_corpus_v2": {"rows": len(rag), "skipped_no_label_match": rag_skipped},
            "gold_real": {"path": args.gold_real,
                           "rows_train_tier": len(gold), "dropped_locked_or_held": gold_dropped,
                           "note": "TRAIN_TIERS만(평가정답 locked·격리 held 제외) · 디오염 라벨 사용"},
            "bilingual_en": {"rows": len(english), "per_grade": args.english_per_grade},
        },
        "pipeline": {
            "raw_pool": n_raw,
            "eval_independence_removed": n_raw - n_after_evalcut,
            "public_ruling_relabeled_to_S3": relabeled,
            "dedup_rows_removed": dup_removed,
            "label_conflicts_resolved": conflicts,
            "court_rows_downsampled": court_dropped,
            "final_rows": len(pool),
        },
        "final": {
            "total": len(pool),
            "grade_distribution": _dist(pool, "label"),
            "origin_dataset_distribution": _dist(pool, "origin_dataset"),
            "document_origin_distribution": _dist(pool, "document_origin"),
            "tier_distribution": _dist(pool, "tier"),
            "court_rows": sum(1 for r in pool if r.get("is_court")),
            "english_rows": sum(1 for r in pool if r.get("origin_dataset") == "bilingual_en"),
            "long_docs": sum(1 for r in pool if r.get("long_doc")),
            "split_sizes": {s: len(splits[s]) for s in ("train", "val", "test")},
            "split_grade_distribution": {s: _dist(splits[s], "label") for s in ("train", "val", "test")},
        },
        "gates": gates,
        "honest_baseline_note": (
            "이 val/test는 dedup으로 train과 정확중복 0이 보장된 최초의 leak-free split이다. "
            "구 내부성능 0.783은 train↔oss val/test 41/52건 누출 산물이므로 폐기하고, 재학습 후 "
            "이 v5 val/test + hardened holdout + 공개문서 FPR로 재측정한 값을 정직 기준선으로 쓴다. "
            "모든 eval은 여전히 machine-silver(사람서명 0) — 모델 교체는 인간 adjudicated eval 후."
        ),
        "training_notes": {
            "no_physical_replication": "구 gold x3 물리복제 폐지 → sample_weight(tier×희소도)로 대체. 트레이너는 WeightedRandomSampler 또는 per-sample loss weight로 소비.",
            "long_doc_head_tail": f">{args.long_doc_chars}자 문서는 long_doc/truncation_strategy=head_tail 플래그. 트레이너가 head+tail로 512토큰 예산 배분(등급근거 말미 잘림 방지).",
            "provenance_preserved": "tier·document_origin·rule_grade·review_status·label_source·doc_id 보존 → 출처·검수 게이트가 학습까지 관통.",
        },
        "promotion_gate": "재학습 후 hardened holdout macro-F1 + 공개문서 FPR + adversarial veto(고→S3=0) 동시 통과 시에만 배포 후보. raw f1 단독 승격 금지.",
        "train_command": (
            "python scripts/p1_train_classifier.py --mode full "
            f"--train-path {Path(args.out_dir).as_posix()}/train.jsonl "
            f"--val-path {Path(args.out_dir).as_posix()}/val.jsonl "
            f"--test-path {Path(args.out_dir).as_posix()}/test.jsonl "
            "--epochs 5 --max-seq-len 512 --no-mlflow "
            "--output-dir artifacts/classifier_p1_v5_clean "
            "# 트레이너에 sample_weight·head_tail truncation 지원 추가 필요(training_notes 참조)"
        ),
    }

    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    if not gates["PASS"]:
        print("\n[v5-build] GATE FAIL", file=sys.stderr)
    if args.dry_run:
        print("\n[DRY-RUN] 파일 무기록", file=sys.stderr)
        return 0 if gates["PASS"] else 1

    out_dir = Path(args.out_dir)
    for s in ("train", "val", "test"):
        _write_jsonl(out_dir / f"{s}.jsonl", splits[s])
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[v5-build] wrote {out_dir}/ (train/val/test + manifest.json)", file=sys.stderr)
    return 0 if gates["PASS"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
