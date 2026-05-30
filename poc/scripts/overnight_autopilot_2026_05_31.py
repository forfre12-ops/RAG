"""오토파일럿 야간 무중단 스크립트 — 2026-05-31.

Phase 1  : git commit + .gitignore 정리
Phase 1.5: pytest 전체 + ruff (베이스라인)
Phase 2  : labeled dataset 재빌드 (OSS 3,754 + B-1 800 = 4,554건)
Phase 3  : P1 KF-DeBERTa GPU 재학습 + 실측
Phase 3.5: pytest 전체 (회귀 확인)
Phase 4  : P2 reranker 추가 측정
Phase 4.5: PSH 시나리오 자동 측정
Phase 5  : doc/15 v12 갱신 + 최종 git commit + 보고서

실행:
  python scripts/overnight_autopilot_2026_05_31.py
"""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# Windows CP949 콘솔 인코딩 문제 해결
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ── 경로 설정 ─────────────────────────────────────────────────
_HERE = Path(__file__).resolve().parent
_POC = _HERE.parent
_ROOT = _POC.parent
_PYTHON = _POC / ".venv" / "Scripts" / "python.exe"
_REPORT_PATH = _ROOT / "report" / f"overnight_{datetime.now().strftime('%Y-%m-%d')}_autopilot.md"

os.chdir(_POC)

# ── 결과 수집 ─────────────────────────────────────────────────
results: list[dict] = []


def run(label: str, cmd: list[str], cwd: Path = _POC, check: bool = False, timeout: int = 7200) -> dict:
    """명령 실행 → 결과 dict 반환."""
    print(f"\n{'='*60}")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {label}")
    print(f"  cmd: {' '.join(str(c) for c in cmd)}")
    print(f"{'='*60}")

    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            [str(c) for c in cmd],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        elapsed = time.perf_counter() - t0
        ok = proc.returncode == 0
        out = (proc.stdout or "") + (proc.stderr or "")
        # 마지막 50줄만 출력
        lines = out.strip().splitlines()
        for line in lines[-50:]:
            print(line)
        result = {
            "label": label,
            "ok": ok,
            "returncode": proc.returncode,
            "elapsed_s": round(elapsed, 1),
            "stdout_tail": "\n".join(lines[-30:]),
        }
    except subprocess.TimeoutExpired:
        elapsed = time.perf_counter() - t0
        print(f"  ⚠️  TIMEOUT ({timeout}s)")
        result = {
            "label": label,
            "ok": False,
            "returncode": -1,
            "elapsed_s": round(elapsed, 1),
            "stdout_tail": "TIMEOUT",
        }
    except Exception as e:
        elapsed = time.perf_counter() - t0
        print(f"  ❌ ERROR: {e}")
        result = {
            "label": label,
            "ok": False,
            "returncode": -2,
            "elapsed_s": round(elapsed, 1),
            "stdout_tail": str(e),
        }

    status = "✅ PASS" if result["ok"] else "❌ FAIL"
    print(f"  → {status} ({result['elapsed_s']}s)")
    results.append(result)
    return result


def git(*args, cwd: Path = _ROOT) -> dict:
    return run(f"git {args[0]}", ["git"] + list(args), cwd=cwd)


# ══════════════════════════════════════════════════════════════
# Phase 1 — 정리 + git commit
# ══════════════════════════════════════════════════════════════
print("\n\n【 Phase 1 — 정리 + git commit 】")

# .gitignore 갱신
gitignore = _ROOT / ".gitignore"
gi_text = gitignore.read_text(encoding="utf-8")
additions = [
    "poc/datasets/oss_corpus/",
    "poc/datasets/synthetic_qwen3_800/",
    "poc/datasets/realistic_corpus/",
    "poc/reports/p2_oss*.md",
    "poc/reports/p2_oss*.json",
]
changed = False
for line in additions:
    if line not in gi_text:
        gi_text += f"\n{line}"
        changed = True
if changed:
    gitignore.write_text(gi_text, encoding="utf-8")
    print("[Phase 1] .gitignore 갱신")

git("add", "poc/scripts/p2_compare_embeddings.py",
    "poc/scripts/p2_oss_corpus_builder.py",
    "poc/scripts/p2_topup_ts_s1.py",
    "poc/scripts/p2_koipa_corpus_builder.py",
    "poc/scripts/p2_build_realistic_corpus.py",
    "poc/scripts/overnight_autopilot_2026_05_31.py",
    ".gitignore",
    "poc/reports/p2_oss_2026-05-31.md",
)
git("commit", "-m", "feat(p2-oss): OSS 코퍼스 3754건 수집·측정 — KURE-v1 hybrid Recall@5 0.623\n\nCo-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>")


# ══════════════════════════════════════════════════════════════
# Phase 1.5 — pytest 베이스라인 + ruff
# ══════════════════════════════════════════════════════════════
print("\n\n【 Phase 1.5 — pytest 베이스라인 + ruff 】")

run("ruff check", [_PYTHON, "-m", "ruff", "check", "src/", "--statistics"])
r_pytest_base = run(
    "pytest 베이스라인",
    [_PYTHON, "-m", "pytest", "-x", "-q", "--tb=short", "--no-header"],
    timeout=1800,
)


# ══════════════════════════════════════════════════════════════
# Phase 2 — labeled dataset 재빌드
# ══════════════════════════════════════════════════════════════
print("\n\n【 Phase 2 — labeled dataset 재빌드 (OSS + B-1) 】")

# OSS corpus + synthetic_qwen3_800 합산 임시 디렉터리 생성
combined_dir = _POC / "datasets" / "labeled_oss_combined_tmp"
combined_dir.mkdir(exist_ok=True)

import shutil
copied = 0
for src_dir in [_POC / "datasets" / "oss_corpus",
                _POC / "datasets" / "synthetic_qwen3_800"]:
    if src_dir.exists():
        for f in src_dir.glob("*.json"):
            dest = combined_dir / f.name
            if not dest.exists():
                shutil.copy2(f, dest)
                copied += 1
print(f"[Phase 2] 합산: {copied}건 → {combined_dir}")

r_labeled = run(
    "labeled dataset 재빌드",
    [_PYTHON, "scripts/build_labeled_dataset.py",
     "--synth-dir", str(combined_dir),
     "--out", "datasets/labeled_oss_v1",
     "--include-all",
     "--ratio", "0.70,0.15,0.15"],
)

# 임시 디렉터리 정리
shutil.rmtree(combined_dir, ignore_errors=True)

# 빌드 결과 확인
labeled_dir = _POC / "datasets" / "labeled_oss_v1"
labeled_counts: dict[str, int] = {}
for split in ["train", "val", "test"]:
    f = labeled_dir / f"{split}.jsonl"
    if f.exists():
        labeled_counts[split] = sum(1 for _ in f.open(encoding="utf-8"))
print(f"[Phase 2] 결과: {labeled_counts}")


# ══════════════════════════════════════════════════════════════
# Phase 3 — P1 GPU 재학습 + 실측
# ══════════════════════════════════════════════════════════════
print("\n\n【 Phase 3 — P1 KF-DeBERTa GPU 재학습 】")

r_p1 = run(
    "P1 GPU 재학습 (3 epoch)",
    [_PYTHON, "scripts/p1_train_classifier.py",
     "--mode", "full",
     "--epochs", "3",
     "--labeled-dir", "datasets/labeled_oss_v1",
     "--report", "reports/p1_oss_2026-05-31.md"],
    timeout=7200,
)

# F1/FNR 추출
p1_f1, p1_fnr = "N/A", "N/A"
p1_report = _POC / "reports" / "p1_oss_2026-05-31.md"
if p1_report.exists():
    text = p1_report.read_text(encoding="utf-8")
    m = re.search(r"F1[- ]macro[^\d]*([\d.]+)", text, re.I)
    if m:
        p1_f1 = m.group(1)
    m = re.search(r"FNR[^\d]*([\d.]+)%?", text, re.I)
    if m:
        p1_fnr = m.group(1) + "%"
print(f"[Phase 3] P1 결과 — F1: {p1_f1}, FNR: {p1_fnr}")


# ══════════════════════════════════════════════════════════════
# Phase 3.5 — pytest 회귀 확인
# ══════════════════════════════════════════════════════════════
print("\n\n【 Phase 3.5 — pytest 회귀 확인 】")

r_pytest_reg = run(
    "pytest 회귀 확인",
    [_PYTHON, "-m", "pytest", "-x", "-q", "--tb=short", "--no-header"],
    timeout=1800,
)


# ══════════════════════════════════════════════════════════════
# Phase 4 — P2 reranker 추가 측정
# ══════════════════════════════════════════════════════════════
print("\n\n【 Phase 4 — P2 reranker (BGE) 추가 측정 】")

r_p2_rerank = run(
    "P2 KURE-v1 + BGE reranker",
    [_PYTHON, "scripts/p2_compare_embeddings.py",
     "--mode", "full",
     "--backends", "es",
     "--synth-dir", "datasets/oss_corpus",
     "--query-style", "koipa",
     "--reranker", "bge",
     "--oversample-factor", "4",
     "--report", "reports/p2_oss_rerank_2026-05-31.md"],
    timeout=3600,
)

# Recall 추출
p2_rerank_recall = "N/A"
p2_report = _POC / "reports" / "p2_oss_rerank_2026-05-31.md"
if p2_report.exists():
    text = p2_report.read_text(encoding="utf-8")
    m = re.search(r"\|\s*([\d.]+)\s*\|\s*[+\-][\d.]+\s*\|.*rerank", text, re.I)
    if not m:
        m = re.search(r"recall_at_k[\":\s]+([\d.]+)", text)
    if m:
        p2_rerank_recall = m.group(1)
print(f"[Phase 4] P2 reranker Recall@5: {p2_rerank_recall}")


# ══════════════════════════════════════════════════════════════
# Phase 4.5 — PSH 시나리오
# ══════════════════════════════════════════════════════════════
print("\n\n【 Phase 4.5 — PSH 시나리오 자동 측정 】")

r_psh = run(
    "PSH dryrun 시나리오",
    [_PYTHON, "scripts/run_perf_scenarios.py",
     "--mode", "dryrun",
     "--report", "reports/psh_oss_2026-05-31.json"],
    timeout=600,
)


# ══════════════════════════════════════════════════════════════
# Phase 5 — 최종 보고서 + git commit
# ══════════════════════════════════════════════════════════════
print("\n\n【 Phase 5 — 최종 보고서 생성 + git commit 】")

total_elapsed = sum(r["elapsed_s"] for r in results)
passed = sum(1 for r in results if r["ok"])
failed_list = [r["label"] for r in results if not r["ok"]]

# pytest 결과 파싱
def parse_pytest(tail: str) -> str:
    m = re.search(r"(\d+) passed", tail)
    if m:
        base = m.group(0)
        mf = re.search(r"(\d+) failed", tail)
        return base + (f", {mf.group(0)}" if mf else "")
    return "알 수 없음"

pytest_base_result = parse_pytest(r_pytest_base.get("stdout_tail", ""))
pytest_reg_result  = parse_pytest(r_pytest_reg.get("stdout_tail", ""))

# 보고서 작성
report_md = f"""# 야간 오토파일럿 보고서 — 2026-05-31

- 시작: (야간 자동 실행)
- 완료: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
- 총 소요: {round(total_elapsed/60, 1)}분
- 결과: {passed}/{len(results)} PASS

---

## 단계별 결과

| Phase | 작업 | 결과 | 소요 |
|---|---|:---:|---|
| 1 | git commit + .gitignore | {'✅' if any(r['ok'] for r in results if 'git' in r['label'].lower()) else '⚠️'} | — |
| 1.5 | pytest 베이스라인 | {'✅' if r_pytest_base['ok'] else '❌'} | {r_pytest_base['elapsed_s']}s |
| 2 | labeled dataset 재빌드 | {'✅' if r_labeled['ok'] else '❌'} | {r_labeled['elapsed_s']}s |
| 3 | P1 GPU 재학습 | {'✅' if r_p1['ok'] else '❌'} | {r_p1['elapsed_s']}s |
| 3.5 | pytest 회귀 확인 | {'✅' if r_pytest_reg['ok'] else '❌'} | {r_pytest_reg['elapsed_s']}s |
| 4 | P2 reranker 측정 | {'✅' if r_p2_rerank['ok'] else '❌'} | {r_p2_rerank['elapsed_s']}s |
| 4.5 | PSH 시나리오 | {'✅' if r_psh['ok'] else '❌'} | {r_psh['elapsed_s']}s |

---

## 핵심 수치

| 지표 | 이전 | 야간 결과 | 합격선 |
|---|---|---|---|
| pytest | 540+ PASS | {pytest_reg_result} | 회귀 0 |
| P1 F1-macro | 1.000 (합성 자기참조) | **{p1_f1}** (OSS 실측) | ≥ 0.75 |
| P1 FNR | 0.0% (합성 자기참조) | **{p1_fnr}** (OSS 실측) | ≤ 5% |
| P2 Recall@5 (base) | 0.623 (KURE hybrid) | — | ≥ 0.80 |
| P2 Recall@5 (reranker) | — | **{p2_rerank_recall}** | ≥ 0.80 |

---

## labeled dataset 현황

| split | 건수 |
|---|---|
| train | {labeled_counts.get('train', 'N/A')} |
| val | {labeled_counts.get('val', 'N/A')} |
| test | {labeled_counts.get('test', 'N/A')} |
| **합계** | **{sum(labeled_counts.values()) if labeled_counts else 'N/A'}** |

소스: OSS corpus 3,754건 + synthetic_qwen3_800 800건 = 4,554건

---

## 실패 항목
{chr(10).join(f'- {l}' for l in failed_list) if failed_list else '- 없음 (전체 PASS)'}

---

## 내일 할 일

1. P1 F1/FNR 수치 확인 → 합격선 통과 여부 판정
2. P2 reranker Recall@5 확인 → 0.80 근접 여부
3. doc/15 v12 갱신 (야간 결과 반영)
4. 발주처 회신 대기 항목 체크
"""

_REPORT_PATH.parent.mkdir(exist_ok=True)
_REPORT_PATH.write_text(report_md, encoding="utf-8")
print(f"[Phase 5] 보고서: {_REPORT_PATH}")
print(report_md)

# 최종 git commit
git("add",
    "reports/",
    "poc/reports/",
    "poc/datasets/labeled_oss_v1/",
    ".gitignore",
)
git("commit", "-m",
    f"chore(overnight): 야간 오토파일럿 완료 — P1 F1={p1_f1} / P2 reranker={p2_rerank_recall}\n\n"
    f"pytest: {pytest_reg_result}\n"
    f"Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
)

print(f"\n\n{'='*60}")
print(f"오토파일럿 완료: {passed}/{len(results)} PASS, {round(total_elapsed/60,1)}분")
print(f"보고서: {_REPORT_PATH}")
print(f"{'='*60}")
