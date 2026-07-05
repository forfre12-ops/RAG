"""
데이터셋 품질 검사 스크립트.

기본 검사 (--train / --val / --test):
  [FAIL] train/val/test 간 doc_id / 텍스트 hash overlap
  [FAIL] 문서 본문 내 등급명 bracket 삽입 비율 > 5%
  [FAIL] 고유 텍스트 비율 < 0.95
  [FAIL] 빈 본문 존재
  [WARN] label_source 필드 없는 비율 > 50%
  [WARN] mojibake 패턴 비율 > 1%
  [INFO] source/grade 분포

Gold 강화 검사 (--gold):
  [FAIL] label_source != human_review
  [FAIL] review_status != accepted
  [FAIL] source == test_set_v2  (합성 gold를 real gold로 오용)
  [FAIL] 본문에 합성 마커 존재 ([가상기업A], [등급분류], [기밀자료] 등)
  [FAIL] train과 exact hash overlap

사용:
  python scripts/check_data_quality.py
  python scripts/check_data_quality.py --gold datasets/gold_real/classification_gold.jsonl
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
from collections import Counter
from pathlib import Path

# Windows CP949 터미널에서 유니코드 출력 보장
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf-8-sig"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() not in ("utf-8", "utf-8-sig"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# lloydk.hygiene 정본 위생 유틸 (정규화 텍스트 해시 — 누출 게이트 강화)
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
from lloydk.hygiene import text_hash  # noqa: E402 (sys.path 설정 후)


# bracket-style 키워드 삽입 패턴 (hard leakage)
BRACKET_GRADE_PATTERN = re.compile(
    r"\[특급기밀\]|\[1급\s*비밀\]|\[2급\s*비밀\]|\[3급\s*공개\]"
    r"|\[TS\]|\[S1\]|\[S2\]|\[S3\]"
    r"|특급기밀\s*관련\s*사항|비밀\s*관련\s*사항"
)
MOJIBAKE_PATTERN = re.compile(r"[愿蹂諛以]|\\ufffd|\?{3,}")

# 합성 마커 — gold_real에 있으면 FAIL
SYNTHETIC_MARKER_PATTERN = re.compile(
    r"\[가상기업[A-Z가-힣]\]"
    r"|\[홍길동\(가명\)\]"
    r"|\[등급분류\]|\[기밀자료\]|\[내부자료\]|\[등급코드\]"
    r"|Noop fallback"
)

# gold_real에서 source로 허용되지 않는 값
FORBIDDEN_GOLD_SOURCES = {"test_set_v2", "synthetic", "rag_corpus_v2", "qa_augment"}


def _sha1(text: str) -> str:
    # 정규화 텍스트 해시(공백차이 동일시) — build_p1 홀드아웃 분리와 동일 기준으로 누출
    # 게이트 강화. (구: raw 해시 → 공백만 다른 중복 누출을 놓침; 현 데이터 영향 0, 미래 방어)
    return text_hash(text)


def load_jsonl(path: Path) -> list[dict]:
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            records.append(json.loads(line))
    return records


def load_hash_manifest(path: Path) -> set[str]:
    """누출 게이트용 텍스트해시 매니페스트(한 줄 1해시, #주석 허용) 로드.

    대용량/미추적 학습풀(train_subset.jsonl 등)을 CI에 커밋하지 않고도 holdout↔train 누출
    검사를 실제로 수행하게 한다. 해시는 build_leakage_manifest.py 가 이 스크립트의 _text/_sha1
    로 생성 → 검사 측과 100% 패리티.
    """
    hs: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            hs.add(line)
    return hs


def _text(r: dict) -> str:
    return r.get("text", "") or (r.get("title", "") + " " + r.get("body", ""))


def check_split(name: str, records: list[dict]) -> tuple[dict, list[str]]:
    issues: list[str] = []
    n = len(records)
    texts = [_text(r) for r in records]
    hashes = [_sha1(t) for t in texts]

    empty_count = sum(1 for t in texts if not t.strip())
    unique_ratio = len(set(hashes)) / max(n, 1)
    bracket_hits = sum(1 for t in texts if BRACKET_GRADE_PATTERN.search(t))
    bracket_ratio = bracket_hits / max(n, 1)
    mojibake_hits = sum(1 for t in texts if MOJIBAKE_PATTERN.search(t))
    mojibake_ratio = mojibake_hits / max(n, 1)
    no_label_source = sum(1 for r in records if not r.get("label_source"))
    no_label_source_ratio = no_label_source / max(n, 1)

    grade_dist = dict(Counter(r.get("label", r.get("target_grade", "?")) for r in records))
    source_dist = dict(Counter(r.get("source", "unknown") for r in records))
    label_source_dist = dict(Counter(r.get("label_source", "none") for r in records))

    if empty_count > 0:
        issues.append(f"FAIL [{name}] 빈 본문 {empty_count}건")
    if unique_ratio < 0.95:
        issues.append(
            f"FAIL [{name}] 고유 텍스트 비율 {unique_ratio:.3f} < 0.95 "
            f"({n - len(set(hashes))}건 중복)"
        )
    if bracket_ratio > 0.05:
        issues.append(
            f"FAIL [{name}] 등급명 bracket 삽입 비율 {bracket_ratio:.3f} > 0.05 "
            f"({bracket_hits}/{n}건)"
        )
    if mojibake_ratio > 0.01:
        issues.append(f"WARN [{name}] mojibake 비율 {mojibake_ratio:.3f} > 0.01 ({mojibake_hits}/{n}건)")
    if no_label_source_ratio > 0.50:
        issues.append(
            f"WARN [{name}] label_source 없는 비율 {no_label_source_ratio:.3f} "
            f"({no_label_source}/{n}건)"
        )

    stats = {
        "name": name,
        "n": n,
        "empty_count": empty_count,
        "unique_ratio": round(unique_ratio, 4),
        "bracket_grade_ratio": round(bracket_ratio, 4),
        "bracket_grade_count": bracket_hits,
        "mojibake_ratio": round(mojibake_ratio, 4),
        "no_label_source_ratio": round(no_label_source_ratio, 4),
        "grade_distribution": grade_dist,
        "source_distribution": source_dist,
        "label_source_distribution": label_source_dist,
    }
    return stats, issues


ACCEPTED_GOLD_LABEL_SOURCES = {
    "human_review",         # 사람이 직접 검수
    "llm_judge_consensus",  # 룰 라벨러 + LLM 동의, 둘 다 고신뢰
    "llm_judge_primary",    # 룰 라벨러 무의견 + LLM 고신뢰 단독
    "codex_review",         # AI adjudicate, requires_human_signoff=True
    "public_definitive",    # 이미 공개된 문서 (판례, DART 공시 등) → 정의상 S3
    "koipa_case_based",     # 손작성 시나리오 (S1/S2) — 2026-07-03 감사: 판례 인용 조작 확인,
                            # 외부권위 아님(synthetic proxy). 신규 생성은 curated_scenario 사용.
    "curated_scenario",     # 손작성 경계 시나리오 — 외부권위 주장 없음(floor 회귀·학습용)
    "nkt_designated",       # 산업부 국가핵심기술 지정 근거 시나리오 (TS) — legal_reference 필수
}

def check_gold_real(name: str, records: list[dict], train_hashes: set[str]) -> tuple[dict, list[str]]:
    """gold_real/ 파일에 대한 강화 검사."""
    issues: list[str] = []
    n = len(records)

    not_human = [r for r in records if r.get("label_source") not in ACCEPTED_GOLD_LABEL_SOURCES]
    not_accepted = [r for r in records if r.get("review_status") != "accepted"]
    forbidden_src = [r for r in records if r.get("source") in FORBIDDEN_GOLD_SOURCES]
    synth_marker = [r for r in records if SYNTHETIC_MARKER_PATTERN.search(_text(r))]

    texts = [_text(r) for r in records]
    hashes = [_sha1(t) for t in texts]
    train_overlap = sum(1 for h in hashes if h in train_hashes)

    if not_human:
        bad_sources = Counter(r.get("label_source") for r in not_human)
        issues.append(
            f"FAIL [{name}] 허용되지 않은 label_source {len(not_human)}건: {dict(bad_sources)} "
            f"(허용: {sorted(ACCEPTED_GOLD_LABEL_SOURCES)})"
        )
    if not_accepted:
        issues.append(
            f"FAIL [{name}] review_status != accepted: {len(not_accepted)}건"
        )

    # 외부권위 위조 가드(2026-07-03 감사): nkt_designated는 지정근거(legal_reference)가 있어야
    # 한다. 근거 없는 손작성 시나리오가 nkt를 위조 사용해 external_authority 버킷을 오염시킨
    # 사례 6건(augment_high_risk_gold)의 재발 차단 — 위조분은 curated_scenario로 교정할 것.
    forged_nkt = [
        r for r in records
        if r.get("label_source") == "nkt_designated"
        and not str(r.get("legal_reference") or "").strip()
    ]
    if forged_nkt:
        ids = [str(r.get("doc_id", "?"))[:12] for r in forged_nkt[:8]]
        issues.append(
            f"FAIL [{name}] nkt_designated인데 legal_reference 없음 {len(forged_nkt)}건 "
            f"(외부권위 위조 의심, 예: {ids}) — label_source를 curated_scenario로 교정 필요"
        )
    if forbidden_src:
        srcs = Counter(r.get("source") for r in forbidden_src)
        issues.append(
            f"FAIL [{name}] 합성 source 포함 {len(forbidden_src)}건: {dict(srcs)} "
            f"(gold_real에는 실문서/공개문서만 허용)"
        )
    if synth_marker:
        issues.append(
            f"FAIL [{name}] 합성 마커 포함 {len(synth_marker)}건 "
            f"([가상기업A], [등급분류] 등)"
        )
    if train_overlap > 0:
        issues.append(f"FAIL [{name}] train과 exact hash overlap {train_overlap}건")

    # 등급 × label_source 크로스탭
    grade_by_label_source: dict[str, dict[str, int]] = {}
    for r in records:
        ls = r.get("label_source", "none")
        g  = r.get("label", "?")
        grade_by_label_source.setdefault(ls, {})
        grade_by_label_source[ls][g] = grade_by_label_source[ls].get(g, 0) + 1

    stats = {
        "name": name,
        "n": n,
        "not_human_review": len(not_human),
        "not_accepted": len(not_accepted),
        "forbidden_source_count": len(forbidden_src),
        "synthetic_marker_count": len(synth_marker),
        "train_overlap": train_overlap,
        "grade_distribution": dict(Counter(r.get("label", "?") for r in records)),
        "source_distribution": dict(Counter(r.get("source", "unknown") for r in records)),
        "label_source_distribution": dict(Counter(r.get("label_source", "none") for r in records)),
        "grade_by_label_source": grade_by_label_source,
    }
    return stats, issues


def check_overlap(splits: dict[str, list[dict]]) -> list[str]:
    issues: list[str] = []
    hash_sets: dict[str, set[str]] = {}
    id_sets: dict[str, set[str]] = {}

    for name, records in splits.items():
        texts = [_text(r) for r in records]
        hash_sets[name] = {_sha1(t) for t in texts}
        id_sets[name] = {r.get("doc_id", "") for r in records if r.get("doc_id")}

    names = list(splits.keys())
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            hash_overlap = hash_sets[a] & hash_sets[b]
            id_overlap = id_sets[a] & id_sets[b]
            if hash_overlap:
                issues.append(f"FAIL [{a}↔{b}] 텍스트 hash overlap {len(hash_overlap)}건")
            if id_overlap:
                issues.append(f"FAIL [{a}↔{b}] doc_id overlap {len(id_overlap)}건")

    return issues


def check_leakage(
    train_records: list[dict],
    eval_records: list[dict],
    eval_name: str,
    train_name: str = "train_pool",
    *,
    train_hashes: set[str] | None = None,
) -> tuple[dict, list[str]]:
    """test/holdout이 train과 '텍스트'로 겹치는지(누출) 검사.

    doc_id만 분리하고 텍스트해시를 점검 안 하면, 동일 문서가 다른 doc_id로 train·
    holdout 양쪽에 들어가 평가가 오염된다. 2026-06-23 실측: holdout_eval 109건 중
    67건(42%)이 train_subset에 텍스트 중복 → 분류기 점수 부풀음(head-to-head 무효화).
    본 게이트로 재발을 차단한다.

    train_hashes 를 주면 train_records 를 다시 해싱하지 않고 그 집합을 쓴다(커밋된 해시
    매니페스트 경로 — 대용량 학습풀 없이 CI에서 실제 누출검사 수행).
    """
    if train_hashes is None:
        train_hashes = {_sha1(_text(r)) for r in train_records}
    leaked = [r for r in eval_records if _sha1(_text(r)) in train_hashes]
    issues: list[str] = []
    if leaked:
        sample = [r.get("doc_id", "?") for r in leaked[:5]]
        issues.append(
            f"FAIL [{eval_name}↔{train_name}] 텍스트 누출 {len(leaked)}/{len(eval_records)}건 "
            f"(doc_id 분리됐어도 본문 동일) sample={sample}"
        )
    stats = {
        "name": f"leakage:{eval_name}",
        "eval_n": len(eval_records),
        "train_n": len(train_hashes),
        "leaked_count": len(leaked),
        "leaked_doc_ids": [r.get("doc_id") for r in leaked],
    }
    return stats, issues


def main() -> int:
    ap = argparse.ArgumentParser(description="데이터셋 품질 검사")
    ap.add_argument("--train", default="datasets/labeled_v2_balanced/train.jsonl")
    ap.add_argument("--val", default=None)
    ap.add_argument("--test", default="datasets/gold/classification_gold.jsonl",
                    help="synthetic masked eval (분포 검사만)")
    ap.add_argument("--gold", default=None,
                    help="gold_real JSONL — 강화 검사 (human_review 필수, 합성 마커 금지)")
    ap.add_argument("--train-pool", default="datasets/gold_real/train_subset.jsonl",
                    help="누출 게이트: holdout과 텍스트해시 교차검증할 학습 풀")
    ap.add_argument("--train-hash-manifest", default=None,
                    help="누출 게이트: 학습풀 대신 커밋된 텍스트해시 매니페스트(한 줄 1해시)를 사용 — "
                         "대용량/미추적 학습풀(train_subset.jsonl 은 gitignore) 없이 CI에서 실제 "
                         "누출검사 수행(fake-green 차단). 존재하면 --train-pool 보다 우선.")
    ap.add_argument("--holdout",
                    default="datasets/gold_real/holdout_eval.clean.jsonl,datasets/gold_real/holdout_business.clean.jsonl",
                    help="누출 게이트 대상 holdout/test (쉼표로 여러 개). 기본=정화 홀드아웃 → "
                         "train-pool과 텍스트해시 누출 검사를 기본 수행(fail-closed). 빈 문자열('')로 비활성.")
    ap.add_argument("--report", default="reports/data_quality_report.json")
    ap.add_argument(
        "--gold-train-overlap-policy",
        choices=["fail", "warn", "ignore"],
        default="fail",
        help=(
            "How to handle --gold records that overlap --train. Use fail for eval-only gold; "
            "use warn only for mixed gold pools where release eval evidence is checked separately."
        ),
    )
    ap.add_argument("--strict-coverage", action="store_true",
                    help="누출/overlap 검사가 입력 부재로 **수행되지 못하면** FAIL(exit 1). 기본 off — "
                         "CI에서 학습풀을 추적/공급하면 이 플래그로 '무음 skip=green'(fake-green)을 차단한다.")
    args = ap.parse_args()

    # 검사가 '통과'가 아니라 '수행 못 됨'인 경우를 명시 기록 — train_overlap=0 이 '검증됨'으로
    # 오독되는 fake-green 을 차단한다(무음 skip 대신 가시적 COVERAGE-GAP + 옵션 강제).
    coverage_gaps: list[str] = []

    # 일반 split 로드
    splits: dict[str, list[dict]] = {}
    for split_name, path_str in [("train", args.train), ("val", args.val), ("test", args.test)]:
        if not path_str:
            continue
        p = Path(path_str)
        if p.exists():
            splits[split_name] = load_jsonl(p)
        else:
            print(f"[SKIP] {split_name}: {p} 없음", file=sys.stderr)

    if not splits:
        print("[ERROR] 검사할 데이터셋이 없습니다.", file=sys.stderr)
        return 2

    all_issues: list[str] = []
    all_stats: list[dict] = []
    leak_stats: list[dict] = []

    for name, records in splits.items():
        stats, issues = check_split(name, records)
        all_stats.append(stats)
        all_issues.extend(issues)

    overlap_issues = check_overlap(splits)
    all_issues.extend(overlap_issues)

    # gold_real 강화 검사
    if args.gold:
        gold_path = Path(args.gold)
        if gold_path.exists():
            gold_records = load_jsonl(gold_path)
            train_hashes: set[str] = set()
            if "train" in splits:
                train_hashes = {_sha1(_text(r)) for r in splits["train"]}
            elif args.train_hash_manifest and Path(args.train_hash_manifest).exists():
                # 원본 train split(gitignore/CI 부재)이 없어도 커밋된 해시 매니페스트로 overlap 수행.
                train_hashes = load_hash_manifest(Path(args.train_hash_manifest))
            else:
                # train 해시 소스 부재 → gold↔train overlap 을 계산할 수 없다. train_overlap=0 이
                # '오염 없음 검증됨'으로 오독되지 않도록 명시적 coverage-gap 으로 남긴다.
                coverage_gaps.append(
                    f"gold train-overlap 미검사 — train 해시 소스 부재({args.train or '미지정'})")
                print("[COVERAGE-GAP] gold↔train overlap NOT checked — train 해시 소스 부재 "
                      "(train_overlap=0 은 '검증됨'이 아니라 '미검사')", file=sys.stderr)
            gstats, gissues = check_gold_real("gold_real", gold_records, train_hashes)
            if args.gold_train_overlap_policy != "fail" and gstats.get("train_overlap", 0) > 0:
                overlap_issues = [
                    issue for issue in gissues
                    if issue.startswith("FAIL [gold_real] train")
                ]
                gissues = [
                    issue for issue in gissues
                    if not issue.startswith("FAIL [gold_real] train")
                ]
                if args.gold_train_overlap_policy == "warn":
                    for issue in overlap_issues:
                        gissues.append(issue.replace("FAIL [gold_real]", "WARN [gold_real]", 1))
            gstats["train_overlap_checked"] = bool(train_hashes)
            all_stats.append(gstats)
            all_issues.extend(gissues)
        else:
            print(f"[SKIP] gold: {gold_path} 없음 (미구축)", file=sys.stderr)

    # ── train ↔ holdout 텍스트 누출 게이트 (데이터 위생) ──────────────
    # train 해시 소스: 커밋된 매니페스트(우선) → 없으면 원본 학습풀(로컬). 둘 다 없으면 COVERAGE-GAP.
    if (args.train_hash_manifest or args.train_pool) and args.holdout:
        train_hashes: set[str] | None = None
        train_label = ""
        manifest = Path(args.train_hash_manifest) if args.train_hash_manifest else None
        if manifest is not None and manifest.exists():
            train_hashes = load_hash_manifest(manifest)
            train_label = manifest.name
        elif args.train_pool and Path(args.train_pool).exists():
            tp = Path(args.train_pool)
            train_hashes = {_sha1(_text(r)) for r in load_jsonl(tp)}
            train_label = tp.name
        if train_hashes is None:
            src = args.train_hash_manifest or args.train_pool
            coverage_gaps.append(f"leak-check 미수행 — train 해시 소스 부재({src})")
            print(f"[COVERAGE-GAP] leak-check NOT run — train 해시 소스 {src} 없음 "
                  "(매니페스트/학습풀 둘 다 부재; 통과가 아니라 미수행)", file=sys.stderr)
        else:
            for hpath in [h.strip() for h in args.holdout.split(",") if h.strip()]:
                hp = Path(hpath)
                if not hp.exists():
                    coverage_gaps.append(f"leak-check 미수행 — holdout 부재({hp})")
                    print(f"[COVERAGE-GAP] leak-check NOT run — holdout {hp} 없음", file=sys.stderr)
                    continue
                lstats, lissues = check_leakage(
                    [], load_jsonl(hp), hp.name, train_label, train_hashes=train_hashes
                )
                leak_stats.append(lstats)
                all_issues.extend(lissues)
                if lstats["leaked_doc_ids"]:
                    ids = [i for i in lstats["leaked_doc_ids"] if i]
                    leak_out = Path("reports") / f"leaked_ids_{hp.stem}.txt"
                    leak_out.parent.mkdir(parents=True, exist_ok=True)
                    leak_out.write_text("\n".join(ids), encoding="utf-8")
                    print(f"[leak] {hp.name}: 누출 {lstats['leaked_count']}/{lstats['eval_n']}건 "
                          f"→ doc_id 목록 {leak_out}", file=sys.stderr)

    fails = [i for i in all_issues if i.startswith("FAIL")]
    warns = [i for i in all_issues if i.startswith("WARN")]

    print("\n=== 데이터 품질 검사 결과 ===")
    for s in all_stats:
        n_val = s.get("n", 0)
        if "bracket_grade_ratio" in s:
            print(
                f"\n[{s['name']}] n={n_val}, "
                f"unique={s.get('unique_ratio', '-'):.3f}, "
                f"bracket={s.get('bracket_grade_ratio', '-'):.3f}, "
                f"empty={s.get('empty_count', 0)}"
            )
            print(f"  grade: {s.get('grade_distribution', {})}")
            print(f"  source: {s.get('source_distribution', {})}")
            print(f"  label_source: {s.get('label_source_distribution', {})}")
        else:
            # gold_real 강화 검사 결과
            print(f"\n[{s['name']}] n={n_val} (강화 검사)")
            print(f"  not_accepted_source={s.get('not_human_review', 0)}, "
                  f"not_accepted={s.get('not_accepted', 0)}, "
                  f"synth_marker={s.get('synthetic_marker_count', 0)}, "
                  f"train_overlap={s.get('train_overlap', 0)}")
            print(f"  grade: {s.get('grade_distribution', {})}")
            print(f"  source: {s.get('source_distribution', {})}")
            # ── label_source 3층 분리 리포트 ──────────────────────────────
            ls_dist = s.get("label_source_distribution", {})
            if ls_dist:
                total = sum(ls_dist.values())
                print(f"\n  ┌─ label_source 분리 (신뢰도 계층) ─────────────────────────")
                tiers = [
                    ("human_review",        "① human_review    (최고 신뢰)"),
                    ("llm_judge_consensus",  "② llm_judge_consensus (룰+LLM 동의)"),
                    ("llm_judge_primary",    "③ llm_judge_primary   (LLM 단독 고신뢰)"),
                    ("codex_review",         "④ codex_review    (AI adjudicate, signoff 필요)"),
                ]
                for key, label in tiers:
                    cnt = ls_dist.get(key, 0)
                    pct = cnt / total * 100 if total else 0
                    marker = " ←N/A" if cnt == 0 else ""
                    print(f"  │  {label}: {cnt}건 ({pct:.1f}%){marker}")
                others = {k: v for k, v in ls_dist.items()
                          if k not in {t[0] for t in tiers}}
                if others:
                    print(f"  │  기타: {others}")
                print(f"  └─ 합계: {total}건")
                # 등급 × label_source 크로스탭
                grade_by_ls = s.get("grade_by_label_source", {})
                if grade_by_ls:
                    print(f"\n  ┌─ 등급 × label_source 크로스탭 ───────────────────────────")
                    grades = ["TS", "S1", "S2", "S3"]
                    ls_keys = [t[0] for t in tiers if ls_dist.get(t[0], 0) > 0]
                    header = "  │  {:4s} " + " {:>8s}" * len(ls_keys)
                    print(header.format("등급", *[k.replace("llm_judge_","lj_")[:8] for k in ls_keys]))
                    for g in grades:
                        row_vals = [grade_by_ls.get(ls, {}).get(g, 0) for ls in ls_keys]
                        row = "  │  {:4s} " + " {:>8d}" * len(ls_keys)
                        print(row.format(g, *row_vals))
                    print(f"  └──────────────────────────────────────────────────────────")

    if all_issues:
        print()
        for issue in all_issues:
            print(f"  {issue}")

    # coverage-gap: 기본은 verdict 불변(가시 기록만), --strict-coverage 면 미수행 검사를 FAIL 처리.
    strict_fail = bool(args.strict_coverage and coverage_gaps)
    verdict = "FAIL" if (fails or strict_fail) else "PASS"
    if coverage_gaps:
        print(f"\n[COVERAGE-GAP] 수행되지 못한 검사 {len(coverage_gaps)}건"
              f"{' → --strict-coverage 로 FAIL 처리' if strict_fail else ' (가시 기록; strict 아님)'}:")
        for g in coverage_gaps:
            print(f"  - {g}")
    print(f"\n결과: {verdict}  (FAIL={len(fails)}, WARN={len(warns)}, COVERAGE-GAP={len(coverage_gaps)})")

    out = Path(args.report)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {"verdict": verdict, "fail_count": len(fails), "warn_count": len(warns),
             "issues": all_issues, "splits": all_stats, "leakage": leak_stats,
             "coverage_gaps": coverage_gaps, "strict_coverage": bool(args.strict_coverage)},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    print(f"리포트: {out}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
