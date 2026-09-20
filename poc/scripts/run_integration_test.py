"""통합테스트 환경 — 후보 데이터 풀 하나로 [사전게이트 -> 누출없는분할 -> 학습 -> 평가 -> 보고]를 한 번에 돌린다.

왜 필요한가: 오늘 여러 번 확인했다 — 근접중복·표층지름길이 있는 채로 자기분할 정확도만 내면
92%대가 나오지만 실문서/타 코퍼스에서는 무너진다(v-0d2e9ad0 등, memory llm-corpus-model-predicts-all-ts).
이 스크립트는 그 숫자를 "경고 없이" 내는 걸 구조적으로 막는다 — 사전게이트 결과를 최종 보고서에
반드시 같은 표로 동반시킨다.

새 로직은 만들지 않는다 — 전부 기존 도구를 그대로 부른다:
  0단계  measure_train_duplication.embed/clusters (import) + run_ngram_shortcut_check.measure (import)
  1단계  0단계의 군집(0.95+ 문턱)을 그대로 분할 단위로 써서 group split
  2단계  scripts/p1_train_classifier.py --mode full (subprocess)
  3단계  scripts/eval_serving_path.py (subprocess, 자기분할 eval + 기존 gold_real 4면)
  4단계  KL 요청 형식(전체건수/등급별건수/구성/P·R·F1/등급별)으로 md+json 산출

사용:
    python scripts/run_integration_test.py --pool datasets/new_build_candidates_2026-09-17/... \
        --label 20260918 --epochs 5

주의: 0단계는 Ollama(bge-m3, localhost:11434)가 떠 있어야 한다. 2단계는 GPU 학습이라 시간이 걸린다.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

POC_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(POC_ROOT / "scripts"))

from measure_train_duplication import embed as duptool_embed  # noqa: E402
from measure_ngram_shortcuts import measure as ngram_measure  # noqa: E402

# 기존 gold_real 홀드아웃 4면 — recall_regression_report.py 와 동일 등록표(정본 하나로 유지)
EXISTING_FACES = {
    "hardened42": {"gold": "datasets/gold_real/holdout_eval.hardened.jsonl", "contaminated": True,
                   "note": "usable_for_comparison=False(길이-only 1NN 0.429, tell 1.000)"},
    "clean42": {"gold": "datasets/gold_real/holdout_eval.clean.jsonl", "contaminated": True,
                "note": "usable_for_comparison=False(길이-only 1NN 0.571, tell 1.000)"},
    "holdout109": {"gold": "datasets/gold_real/holdout_eval.jsonl", "contaminated": True,
                   "note": "usable_for_comparison=False(Theil's U 0.372), holdout109_rejudged 와 라벨 22.0% 불일치"},
    "golden100": {"gold": "datasets/gold/golden100_labeled_v3.jsonl", "contaminated": True,
                  "note": "지름길 셋(글자 n-gram 5겹 100.0%, 라벨섞기 28.0%; 2026-09-20) — 절대 성능 근거 불가, 상대 비교용"},
}
GRADES = ["TS", "S1", "S2", "S3"]


def load_pool(path: Path) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            if d.get("text") and d.get("label") in GRADES:
                rows.append(d)
    return rows


def preflight(rows: list[dict], out_dir: Path) -> dict:
    print(f"[0단계 사전게이트] n={len(rows)} — 근접중복(임베딩) + 지름길(순열기준선) 진단 시작")
    texts = [r["text"][:4000] for r in rows]
    emb = duptool_embed(texts, url="http://localhost:11434/api/embed", model="bge-m3")
    import numpy as np
    E = np.asarray(emb, dtype="float32")
    E /= (np.linalg.norm(E, axis=1, keepdims=True) + 1e-9)
    sim = E @ E.T
    n = len(rows)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    # 2026-09-18 실측: 순수 파이썬 이중루프는 n=1,833(168만 쌍)에서는 즉시 끝났지만
    # n=30,093(4억5천만 쌍)에서 심각하게 느려질 위험이 확인돼 numpy 벡터화로 바꾼다.
    # 상삼각만 본다(대각선 제외) — np.triu_indices 로 (i,j) 쌍만 뽑고 그 위치에서만 비교한다.
    iu, ju = np.triu_indices(n, k=1)
    sim_upper = sim[iu, ju]
    pairs_090 = int((sim_upper >= 0.90).sum())
    for i, j in zip(iu[sim_upper >= 0.95].tolist(), ju[sim_upper >= 0.95].tolist()):
        union(i, j)  # 분할 단위로 쓰는 문턱(보수적 — 0.90 보다 높여 과결합 방지)
    clusters = defaultdict(list)
    for i in range(n):
        clusters[find(i)].append(i)
    largest = max((len(v) for v in clusters.values()), default=0)

    shortcut_rows = [{"text": r["text"], "label": r["label"],
                       "family_id": str(find(idx))} for idx, r in enumerate(rows)]
    ngram = ngram_measure(shortcut_rows, seeds=5)

    result = {
        "n": n,
        "near_dup_clusters_0_95": len(clusters),
        "near_dup_largest_cluster": largest,
        "pairs_above_0_90": pairs_090,
        "shortcut_stratified_cv": ngram["stratified_cv"],
        "shortcut_family_cv": ngram.get("family_cv"),
        "cluster_of": [find(i) for i in range(n)],
    }
    (out_dir / "preflight.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[0단계] 완료 — 군집(0.95+) {len(clusters)}개(최대 {largest}건), "
          f"지름길 excess {ngram['stratified_cv']['excess_pp']:.1f}pp")
    return result


def group_stratified_split(rows: list[dict], cluster_of: list[int], test_size: float, seed: int):
    # 군집을 등급별로 묶어 8:2에 가깝게 배분(군집은 절대 쪼개지 않는다)
    import random
    rng = random.Random(seed)
    by_cluster = defaultdict(list)
    for idx, c in enumerate(cluster_of):
        by_cluster[c].append(idx)
    cluster_ids = list(by_cluster.keys())
    rng.shuffle(cluster_ids)

    train_idx, test_idx = [], []
    per_grade_target_test = Counter()
    per_grade_total = Counter(r["label"] for r in rows)
    for g, n in per_grade_total.items():
        per_grade_target_test[g] = round(n * test_size)
    per_grade_test_count = Counter()

    for c in cluster_ids:
        members = by_cluster[c]
        member_grades = Counter(rows[i]["label"] for i in members)
        # 이 군집을 test로 넣었을 때 목표치를 넘는 등급이 있으면 train으로
        would_overflow = any(per_grade_test_count[g] + member_grades[g] > per_grade_target_test[g] * 1.3
                              for g in member_grades)
        if not would_overflow and sum(per_grade_test_count.values()) < sum(per_grade_target_test.values()):
            test_idx.extend(members)
            per_grade_test_count.update(member_grades)
        else:
            train_idx.extend(members)
    return train_idx, test_idx


def write_jsonl(path: Path, rows: list[dict], indices: list[int]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for i in indices:
            r = rows[i]
            f.write(json.dumps({"doc_id": r.get("doc_id", f"row{i}"), "text": r["text"], "label": r["label"]},
                                ensure_ascii=False) + "\n")


def run_training(train_path: Path, val_path: Path, test_path: Path, output_dir: Path, epochs: int) -> dict:
    cmd = [sys.executable, "scripts/p1_train_classifier.py", "--mode", "full",
           "--train-path", str(train_path.relative_to(POC_ROOT)),
           "--val-path", str(val_path.relative_to(POC_ROOT)),
           "--test-path", str(test_path.relative_to(POC_ROOT)),
           "--output-dir", str(output_dir.relative_to(POC_ROOT)),
           "--epochs", str(epochs), "--no-mlflow"]
    print(f"[2단계 학습] {' '.join(cmd)}")
    proc = subprocess.run(cmd, cwd=str(POC_ROOT), capture_output=True, text=True)
    return {"returncode": proc.returncode, "stdout_tail": proc.stdout[-3000:], "stderr_tail": proc.stderr[-3000:]}


TABLE_ROW_RE = re.compile(
    r"^\|\s*(?P<tau>\S+)\s*\|\s*(?P<fnr>[\d.]+)\s*\|\s*(?P<fnr_hi>[\d.]+)\s*\|\s*(?P<fnr_ts>[\d.]+)\s*\|\s*(?P<fnr_s1>[\d.]+)\s*\|\s*(?P<f1>[\d.]+)\s*\|\s*(?P<acc>[\d.]+)\s*\|",
    re.MULTILINE,
)


def run_eval(model_dir: Path, gold: str, out_dir: Path, tag: str) -> dict:
    report_path = out_dir / f"{tag}_serving.md"
    cmd = [sys.executable, "scripts/eval_serving_path.py",
           "--model-dir", str(model_dir.relative_to(POC_ROOT)),
           "--gold", gold, "--taus", "0",
           "--report", str(report_path.relative_to(POC_ROOT))]
    proc = subprocess.run(cmd, cwd=str(POC_ROOT), capture_output=True, text=True)
    if proc.returncode != 0:
        return {"error": proc.stderr[-1500:]}
    text = report_path.read_text(encoding="utf-8") if report_path.exists() else proc.stdout
    m = TABLE_ROW_RE.search(text)
    if not m:
        return {"error": "표 파싱 실패", "raw_tail": text[-1000:]}
    row = m.groupdict()
    return {
        "fnr_directional_high_grade_avg": float(row["fnr_hi"]),
        "recall_high_grade_avg": round(1.0 - float(row["fnr_hi"]), 4),
        "f1_macro": float(row["f1"]),
        "accuracy": float(row["acc"]),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pool", required=True, help="입력 후보 데이터 jsonl (text,label 필드)")
    ap.add_argument("--label", required=True, help="산출 폴더명 태그(예: 20260918) — 자동시각 금지 규율상 호출자가 명시")
    ap.add_argument("--epochs", type=int, default=5)
    ap.add_argument("--test-size", type=float, default=0.2)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--skip-train", action="store_true", help="이미 학습된 모델로 3~4단계만 재실행")
    ap.add_argument("--model-dir", default=None, help="--skip-train 일 때 평가할 모델 경로")
    args = ap.parse_args()

    pool_path = Path(args.pool)
    out_dir = POC_ROOT / "reports" / f"integration_test_{args.label}"
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = load_pool(pool_path)
    label_dist = Counter(r["label"] for r in rows)
    print(f"[입력] {pool_path} -> {len(rows)}건(라벨+본문 유효), 등급분포 {dict(label_dist)}")

    pre = preflight(rows, out_dir)

    train_idx, test_idx = group_stratified_split(rows, pre["cluster_of"], args.test_size, args.seed)
    val_idx = test_idx[: len(test_idx) // 2]
    test_idx2 = test_idx[len(test_idx) // 2:]
    write_jsonl(out_dir / "split_train.jsonl", rows, train_idx)
    write_jsonl(out_dir / "split_val.jsonl", rows, val_idx)
    write_jsonl(out_dir / "split_test.jsonl", rows, test_idx2)
    print(f"[1단계 분할] train={len(train_idx)} val={len(val_idx)} test={len(test_idx2)} "
          f"(0.95+ 근접중복 군집은 분할 경계를 넘지 않음)")

    if args.model_dir:
        model_dir = Path(args.model_dir)
        if not model_dir.is_absolute():
            model_dir = (POC_ROOT / model_dir).resolve()
    else:
        model_dir = out_dir / "model"
    if not args.skip_train:
        train_result = run_training(out_dir / "split_train.jsonl", out_dir / "split_val.jsonl",
                                     out_dir / "split_test.jsonl", model_dir, args.epochs)
        (out_dir / "train_log.json").write_text(json.dumps(train_result, ensure_ascii=False, indent=2), encoding="utf-8")
        if train_result["returncode"] != 0:
            print("[2단계] 학습 실패 — train_log.json 확인")
            print(train_result["stderr_tail"][-2000:])
            return 1

    # 2026-09-18 실측: p1_train_classifier.py는 --output-dir 바로 밑이 아니라 그 안에
    # v-<hash> 버전 하위폴더를 만들어 최종 배포용 모델(tokenizer.json 포함)을 거기 저장한다
    # (체크포인트 checkpoint-*와 나란히 있음). model_dir을 그 하위폴더로 재지정하지 않으면
    # eval_serving_path.py가 토크나이저를 못 찾는다(부모 폴더엔 tokenizer.json이 없음).
    version_dirs = sorted((d for d in model_dir.glob("v-*") if d.is_dir()),
                           key=lambda d: d.stat().st_mtime, reverse=True)
    if version_dirs:
        print(f"[안내] 배포용 모델 하위폴더 발견: {version_dirs[0].name} — 평가는 이 경로로 진행")
        model_dir = version_dirs[0]
    else:
        print(f"[경고] {model_dir} 안에 v-* 버전 폴더가 없음 — 그대로 진행하나 실패할 수 있음")

    print("[3단계 평가] 자기분할 test + 기존 gold_real 4면")
    eval_results = {"self_split_test": run_eval(model_dir, str((out_dir / "split_test.jsonl").relative_to(POC_ROOT)),
                                                 out_dir, "self_split")}
    for face, cfg in EXISTING_FACES.items():
        eval_results[face] = {**run_eval(model_dir, cfg["gold"], out_dir, face),
                               "known_contaminated": cfg["contaminated"], "note": cfg["note"]}

    summary = {
        "pool": str(pool_path),
        "n_total": len(rows),
        "label_distribution": dict(label_dist),
        "split": {"train": len(train_idx), "val": len(val_idx), "test": len(test_idx2)},
        "preflight_warnings": {
            "near_dup_clusters_0_95": pre["near_dup_clusters_0_95"],
            "near_dup_largest_cluster": pre["near_dup_largest_cluster"],
            "pairs_above_0_90": pre["pairs_above_0_90"],
            "shortcut_excess_pp": pre["shortcut_stratified_cv"]["excess_pp"],
            "shortcut_permutation_baseline": pre["shortcut_stratified_cv"]["permutation_mean"],
        },
        "eval_results": eval_results,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    md = [
        f"# 통합테스트 결과 — {args.label}",
        "",
        f"- 입력 풀: `{pool_path}`",
        f"- 전체 {len(rows)}건, 등급분포 {dict(label_dist)}",
        f"- 분할: train {len(train_idx)} / val {len(val_idx)} / test {len(test_idx2)} "
        f"(근접중복 군집 {pre['near_dup_clusters_0_95']}개, 최대 군집 {pre['near_dup_largest_cluster']}건 — 분할 경계 안 넘음)",
        "",
        "## ⚠ 사전게이트 경고 (숫자와 항상 같이 볼 것)",
        f"- 0.90 이상 근접중복 쌍: {pre['pairs_above_0_90']}",
        f"- 표층지름길(char-ngram+SVC) 정확도 {pre['shortcut_stratified_cv']['mean']:.1%} vs "
        f"라벨섞기 기준선 {pre['shortcut_stratified_cv']['permutation_mean']:.1%} "
        f"(초과 {pre['shortcut_stratified_cv']['excess_pp']:.1f}pp)",
        "",
        "## 평가 결과",
        "| 면 | 오염(참고용) | 정확도 | F1 | 고등급 재현율 |",
        "|---|---|---:|---:|---:|",
    ]
    for face, r in eval_results.items():
        if "error" in r:
            md.append(f"| {face} | - | 오류 | - | - |")
            continue
        contam = "⚠ 예" if r.get("known_contaminated") else ("- (자기분할, 낙관적 참고용)" if face == "self_split_test" else "-")
        md.append(f"| {face} | {contam} | {r['accuracy']:.1%} | {r['f1_macro']:.3f} | {r['recall_high_grade_avg']:.1%} |")
    (out_dir / "summary.md").write_text("\n".join(md), encoding="utf-8")

    print(f"\n[완료] {out_dir / 'summary.md'}")
    print(f"[완료] {out_dir / 'summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
