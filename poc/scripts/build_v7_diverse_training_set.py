"""v7 학습셋 — S3 를 판례 장르에서 떼어내고 길이 다양성을 넣는다.

두 결함을 동시에 고친다(둘 다 2026-08-12 실측).

**① S3 = 판례 결박 (v5 계보)**
    v5 배포본 학습셋의 S3 813건 중 24.8% 가 판례이고 중앙 길이 1,096자다
    (다른 등급 336~429자). 모델이 "길고 판례스러우면 S3" 를 배웠고, 실제로
    판례가 없는 셋에서 S3 재현율이 무너진다:
        공개 판례 400건      S3 96%
        일상 업무문서 500건  S3 20%
        v3 합성 업무문서 800건 S3 2.5%
    같은 결박이 평가셋에도 있어(경화42 의 S3 7건 = 전부 판례) **측정 자체가 안 됐다.**

**② 길이 균일 (v6 계보)**
    v6 는 판례 결박이 없다(전 등급 판례 0%). 대신 길이가 2,140~3,625 로 몰려 있어
    **짧은 문서를 본 적이 없다.** 실문서는 120~8,000자로 흩어진다.

그래서 v7 = v6(요인 근거·장르 중립) + 짧은 비-판례 S3 + 중간 길이 TS/S1 + 판례 소수.

**판례를 빼지 않는다.** 유일한 실데이터이고, 공개 문서의 한 종류로서 정당한 S3 다.
바꾸는 것은 "S3 = 판례"라는 **등식**이지 판례의 존재가 아니다. 목표는 S3 안에서
판례 비율을 10% 아래로 낮추고, 등급 간 길이 분포가 겹치게 만드는 것이다.

등급 정의는 `proxy_corpus.grade_from_svm` 이 정본이다. 확인 결과 **value=0 이면
비밀성·관리성과 무관하게 S3** 다 — 즉 "내부 문서이되 경제적 가치 없음"은 S3 이고,
mundane_s3 의 라벨은 규칙과 일치한다(v5 가 그 80%를 S2 로 본 것이 오류다).

발행 전 koipa.dataset_leakage 게이트를 통과해야 한다.
No LLM or model is called.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from koipa.dataset_leakage import audit, check_or_raise  # noqa: E402

GRADES = ("TS", "S1", "S2", "S3")
COURT_MARKERS = ("판결", "선고", "원고", "피고", "법원", "사건", "항소", "상고", "재판부")
# S3 안의 판례 비율 상한. 0 이 아닌 이유: 판례는 유일한 실데이터이고 정당한 S3 다.
# 문제는 존재가 아니라 **독점**이었다(v5 에서 24.8% + 길이 3배 → 사실상 S3 의 정의).
MAX_COURT_SHARE_IN_S3 = 0.10

SOURCES = {
    "v6": ROOT / "datasets/proxy_gold/direct_authored_catalog_training.v6.jsonl",
    "mundane": ROOT / "datasets/mundane_s3/raw.scored.jsonl",
    "patent": ROOT / "datasets/patent_proxy/holdout_eval_clean.jsonl",
    "v5train": ROOT / "datasets/labeled_p1_v5_clean/train.jsonl",
}
OUT_DIR = ROOT / "datasets" / "labeled_v7_diverse"


class V7BuildError(RuntimeError):
    """발행 전에 걸러야 하는 구조적 문제."""


def _text(row: dict) -> str:
    for key in ("text", "content", "body"):
        if row.get(key):
            return str(row[key])
    return ""


def _grade(row: dict) -> str:
    return str(row.get("label") or row.get("expected_grade") or row.get("grade") or "")


def _is_court(text: str) -> bool:
    return sum(marker in text for marker in COURT_MARKERS) >= 3


def _read(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]


def _seed(text: str) -> int:
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:12], 16)


def _norm(row: dict, origin: str, index: int) -> dict:
    """공통 스키마로 정규화 — 출처를 남겨 나중에 구성을 되짚을 수 있게 한다."""
    text = _text(row)
    return {
        "doc_id": str(row.get("doc_id") or f"v7-{origin}-{index:05d}"),
        "text": text,
        "label": _grade(row),
        "document_family_id": str(
            row.get("document_family_id") or f"v7-{origin}-family-{index:05d}"
        ),
        "v7_origin": origin,
        "v7_is_court": _is_court(text),
        "v7_chars": len(text),
    }


def collect() -> list[dict]:
    pool: list[dict] = []
    seen: set[str] = set()
    for origin, path in SOURCES.items():
        rows = _read(path)
        for index, row in enumerate(rows):
            text = _text(row)
            grade = _grade(row)
            if not text or grade not in GRADES:
                continue
            key = hashlib.sha256(" ".join(text.split()).encode("utf-8")).hexdigest()
            if key in seen:          # 출처 간 중복 제거 — 같은 문서를 두 번 배우지 않는다
                continue
            seen.add(key)
            pool.append(_norm(row, origin, index))
    return pool


LENGTH_BINS = 8


def select(pool: list[dict], per_grade: int) -> list[dict]:
    """등급 **간** 길이 분포를 같게 만들면서 판례 비율을 낮춘다.

    ⚠ 등급 안에서만 사분위를 고르는 것으로는 부족했다(실측 1차 시도: 길이-only 0.692).
    등급마다 출처가 달라 **주변 분포가 갈렸기 때문**이다 — 중앙 길이가 S3 193 · S2 1,037 로
    5배 벌어졌고, 그러면 길이가 다시 등급을 알려준다.

    그래서 **전역 길이 구간**(전체 풀의 8분위)을 잡고 **모든 등급이 각 구간에서 같은 수**를
    가져간다. 그러면 등급별 길이 주변분포가 설계상 동일해져 길이가 정보를 갖지 못한다.
    구간별 정원은 가장 부족한 등급에 맞춘다 — 한 등급이 못 채우면 그 구간 전체를 줄인다.

    판례는 S3 상한 10% 까지만 받고 나머지 자리는 비-판례로 채운다. 판례를 빼는 게 아니라
    **독점을 깨는** 것이다.
    """
    by_grade: dict[str, list[dict]] = defaultdict(list)
    for row in pool:
        by_grade[row["label"]].append(row)
    for grade in GRADES:
        if not by_grade.get(grade):
            raise V7BuildError(f"등급 {grade} 후보가 없다")

    # 전역 길이 경계 — 전체 풀 기준이라 등급과 무관하다
    all_lengths = sorted(r["v7_chars"] for r in pool)
    edges = [all_lengths[(i * len(all_lengths)) // LENGTH_BINS] for i in range(1, LENGTH_BINS)]

    def bin_of(chars: int) -> int:
        return sum(1 for e in edges if chars >= e)

    # (등급, 구간) 별 후보. 판례는 뒤로 밀어 비-판례가 먼저 뽑히게 한다.
    cells: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for row in pool:
        cells[(row["label"], bin_of(row["v7_chars"]))].append(row)
    for key in cells:
        cells[key].sort(key=lambda r: (r["v7_is_court"], r["doc_id"]))

    per_bin = per_grade // LENGTH_BINS
    chosen: list[dict] = []
    used: set[str] = set()
    for b in range(LENGTH_BINS):
        quota = min(per_bin, *(len(cells[(g, b)]) for g in GRADES))
        if quota <= 0:
            continue
        for grade in GRADES:
            take = cells[(grade, b)][:quota]
            chosen.extend(take)
            used.update(r["doc_id"] for r in take)

    # ── 희소 자원 보강 ─────────────────────────────────────────────────────
    # 균등 구간만으로는 **짧은 문서(mundane)와 판례가 거의 안 들어온다** — 구간 정원이
    # 가장 부족한 등급에 맞춰지기 때문이다(실측 1차: mundane 500중 34 · 판례 0%).
    # 그런데 그 둘이 정확히 이 셋을 만드는 이유다:
    #   · 판례 = **유일한 실데이터**. 빼면 학습셋이 100% 합성이 되고, 공개 판례 FPR
    #     시험(400건)에 대응하는 학습 신호가 사라진다. 목표는 제거가 아니라 독점 해소다.
    #   · 짧은 문서 = v6 이 못 본 길이대(120~276자). 실문서는 여기 많다.
    # 길이 지표에 여유가 있을 때만(호출부가 검사) 상한까지 보강한다.
    def topup(rows: list[dict], cap: int) -> None:
        added = 0
        for row in rows:
            if added >= cap:
                break
            if row["doc_id"] in used:
                continue
            chosen.append(row)
            used.add(row["doc_id"])
            added += 1

    s3_pool = sorted(by_grade["S3"], key=lambda r: r["doc_id"])
    s3_target = sum(1 for r in chosen if r["label"] == "S3")
    topup([r for r in s3_pool if r["v7_is_court"]],
          max(1, int(s3_target * MAX_COURT_SHARE_IN_S3)))
    topup([r for r in s3_pool if r["v7_origin"] == "mundane"],
          max(1, int(s3_target * 0.25)))
    return chosen


def split(rows: list[dict], train: float, val: float) -> dict[str, list[dict]]:
    """가족 단위·결정적 분할(v6 분할기와 같은 원칙)."""
    out: dict[str, list[dict]] = {"train": [], "val": [], "test": []}
    for row in rows:
        position = _seed(row["document_family_id"]) / float(1 << 48)
        key = "train" if position < train else ("val" if position < train + val else "test")
        out[key].append(row)
    for key in out:
        out[key].sort(key=lambda r: r["doc_id"])
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="v7 다양화 학습셋")
    parser.add_argument("--per-grade", type=int, default=700)
    parser.add_argument("--train-ratio", type=float, default=0.70)
    parser.add_argument("--val-ratio", type=float, default=0.15)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)

    pool = collect()
    print(f"[pool] {len(pool)}건 · 출처 {dict(Counter(r['v7_origin'] for r in pool))}")
    rows = select(pool, args.per_grade)

    print(f"\n[selected] {len(rows)}건")
    for grade in GRADES:
        sub = [r for r in rows if r["label"] == grade]
        if not sub:
            continue
        lens = sorted(r["v7_chars"] for r in sub)
        court = sum(r["v7_is_court"] for r in sub)
        origins = Counter(r["v7_origin"] for r in sub)
        print(f"  {grade}: n={len(sub):4d} 길이 {lens[0]:5d}~{lens[-1]:6d} "
              f"(중앙 {lens[len(lens)//2]:5d}) 판례 {court/len(sub):5.1%} {dict(origins)}")

    report = audit((r["label"], r["text"]) for r in rows)
    print(f"\n[leakage] 길이-only {report['length_only_1nn']:.3f} · "
          f"Theil's U {report['length_theils_u']:.3f} · "
          f"tell {report['tell_sentences']}종(커버 {report['tell_coverage']:.3f})")

    if not args.write:
        print("\n(--write 없음: 파일 미생성)")
        return 0

    check_or_raise(((r["label"], r["text"]) for r in rows), label="v7-diverse-training")
    parts = split(rows, args.train_ratio, args.val_ratio)
    families = {k: {r["document_family_id"] for r in v} for k, v in parts.items()}
    for a, b in (("train", "val"), ("train", "test"), ("val", "test")):
        if families[a] & families[b]:
            raise V7BuildError(f"가족 겹침 {a}/{b}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, object] = {
        "schema": "v7-diverse-training-v1",
        "sources": {k: str(v.relative_to(ROOT)) for k, v in SOURCES.items() if v.is_file()},
        "policy": {
            "max_court_share_in_s3": MAX_COURT_SHARE_IN_S3,
            "length_quartile_balanced": True,
            "family_disjoint_split": True,
            "grade_rule": "proxy_corpus.grade_from_svm (value=0 -> S3 무조건)",
        },
        "leakage": report,
        "splits": {},
    }
    for name, subset in parts.items():
        payload = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in subset)
        path = OUT_DIR / f"{name}.jsonl"
        temporary = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex}")
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        manifest["splits"][name] = {
            "documents": len(subset),
            "families": len({r["document_family_id"] for r in subset}),
            "grades": dict(sorted(Counter(r["label"] for r in subset).items())),
            "court_share": round(
                sum(r["v7_is_court"] for r in subset) / max(len(subset), 1), 4
            ),
            "sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        }
    (OUT_DIR / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"\n[written] {OUT_DIR}")
    print(json.dumps(manifest["splits"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
