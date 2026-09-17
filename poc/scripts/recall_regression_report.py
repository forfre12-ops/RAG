"""재현율/미탐률(FNR) 회귀 리포트 — 여러 도구·정의를 한 번에 묶어 낸다.

왜 필요한가: 이 프로젝트에는 "재현율"이라는 이름의 지표가 코드에 없다. 대신
표준 recall과 방향성 FNR(고등급->저등급만 세는 것)이라는 서로 다른 정의가,
모델단독/오프라인 서빙경로/실 API(합의게이트 포함) 라는 서로 다른 측정 경계와
섞여 인용돼 왔다(2026-09-17 인벤토리에서 "미탐 16%"와 "FNR 30%"를 같은 것으로
착각할 뻔한 사례가 실제로 나왔다). 이 스크립트는 숫자만 내지 않고 그 숫자를 낸
[도구 · 정의 · needs_review 처리방식 · 이 면의 알려진 오염 여부]를 항상 함께 찍는다.

이 스크립트는 새 측정 로직을 만들지 않는다 — scripts/eval_serving_path.py(오프라인
서빙경로, argmax 기본)를 각 면에 대해 그대로 호출하고 결과를 한 파일로 묶을 뿐이다.
API 경로(scripts/measure_serving_fnr.py, 합의게이트까지 포함한 유일한 완전 경로)는
API 키가 필요해 이 스크립트가 자동으로 돌리지 않는다 — api_path 칸을 비워두고
사람이 따로 채우도록 한다.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

POC_ROOT = Path(__file__).resolve().parents[1]  # .../poc

# 면 이름 -> (gold 파일, 이 면이 알려진 오염 셋인지, 비고)
# 오염 여부는 poc/scripts/report_holdout_independence.py 실측(2026-09-05, 2026-09-17 재확인)을 인용한다.
FACES = {
    "hardened42": {
        "gold": "datasets/gold_real/holdout_eval.hardened.jsonl",
        "contaminated": True,
        "note": "usable_for_comparison=False(길이-only 1NN 0.429, tell 1.000) — 정확도 근거로 인용 금지",
    },
    "clean42": {
        "gold": "datasets/gold_real/holdout_eval.clean.jsonl",
        "contaminated": True,
        "note": "usable_for_comparison=False(길이-only 1NN 0.571, tell 1.000) — 정확도 근거로 인용 금지",
    },
    "holdout109": {
        "gold": "datasets/gold_real/holdout_eval.jsonl",
        "contaminated": True,
        "note": "usable_for_comparison=False(Theil's U 0.372) — holdout109_rejudged 와 라벨 22.0% 불일치, 정본 미확정",
    },
    "golden100": {
        "gold": "datasets/gold/golden100_labeled_v2.jsonl",
        "contaminated": False,
        "note": "적대(adversarial) 설계셋 — 오염 재검증은 안 됐으나 '공정비교 4종'에 원래 속하지 않았음. datasets/adversarial/golden_100.jsonl(본문 없음)과 절대 혼동하지 말 것",
    },
}

TABLE_ROW_RE = re.compile(
    r"^\|\s*(?P<tau>\S+)\s*\|\s*(?P<fnr>[\d.]+)\s*\|\s*(?P<fnr_hi>[\d.]+)\s*\|\s*(?P<fnr_ts>[\d.]+)\s*\|\s*(?P<fnr_s1>[\d.]+)\s*\|\s*(?P<f1>[\d.]+)\s*\|\s*(?P<acc>[\d.]+)\s*\|",
    re.MULTILINE,
)


def run_face(python_exe: str, model_dir: str, face: str, gold: str, out_dir: Path) -> dict:
    report_path = out_dir / f"{face}_serving.md"
    cmd = [
        python_exe, "scripts/eval_serving_path.py",
        "--model-dir", model_dir,
        "--gold", gold,
        "--taus", "0",
        "--report", str(report_path.relative_to(POC_ROOT)),
    ]
    proc = subprocess.run(cmd, cwd=str(POC_ROOT), capture_output=True, text=True)
    if proc.returncode != 0:
        return {"error": proc.stderr[-2000:] or proc.stdout[-2000:]}
    text = report_path.read_text(encoding="utf-8") if report_path.exists() else proc.stdout
    m = TABLE_ROW_RE.search(text)
    if not m:
        return {"error": "표 파싱 실패 — 원문 확인 필요", "raw_tail": text[-1500:]}
    row = m.groupdict()
    return {
        "fnr_directional_overall": float(row["fnr"]),
        "fnr_directional_high_grade_avg": float(row["fnr_hi"]),
        "fnr_directional_ts": float(row["fnr_ts"]),
        "fnr_directional_s1": float(row["fnr_s1"]),
        "recall_high_grade_avg": round(1.0 - float(row["fnr_hi"]), 4),
        "f1_macro": float(row["f1"]),
        "accuracy": float(row["acc"]),
        "report_file": str(report_path),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model-dir", default="artifacts/classifier_p1_v5_clean/v-fe4b386b")
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--out-dir", default=None, help="기본값: reports/recall_audit_<YYYYMMDD 인자로 필수 지정>")
    ap.add_argument("--label", required=True, help="산출 폴더명에 쓸 날짜/라벨 (예: 20260917) — Date.now() 등 자동시각 금지 규율에 따라 호출자가 명시")
    args = ap.parse_args()

    out_dir = Path(args.out_dir) if args.out_dir else (POC_ROOT / "reports" / f"recall_audit_{args.label}")
    out_dir.mkdir(parents=True, exist_ok=True)

    summary = {
        "model_dir": args.model_dir,
        "label": args.label,
        "definitions": {
            "fnr_directional": "고등급(TS/S1) 정답이 더 낮은 등급으로 예측된 비율만 센다(과분류/저->고 오류는 제외). 표준 sklearn recall과 다른 정의다.",
            "recall_high_grade_avg": "1 - fnr_directional_high_grade_avg. RFP '재현율 90%'를 이 값으로 잠정 해석했다(원문이 방향성/표준을 구분하지 않아 확정 아님).",
        },
        "measurement_boundary": {
            "tool": "scripts/eval_serving_path.py (오프라인, InferencePipeline.run 경유)",
            "includes": ["temperature 보정", "청크 most-severe 집계", "FNR-safe override", "source-prior 게이트", "escalation(tau=0=argmax)"],
            "excludes": ["합의게이트(agreement gate)", "실 API 왕복", "사람 검수 라우팅(needs_review)"],
            "note": "needs_review로 빠지는 건도 최종 예측 라벨만으로 방향성 FNR을 센다 — API 경로(measure_serving_fnr.py)의 silent_miss_rate(사람이 본 것은 미탐 아님으로 제외)와는 분모/분자가 다르다. 두 수치를 같은 것으로 인용하지 말 것.",
        },
        "api_path_note": "scripts/measure_serving_fnr.py(합의게이트 포함 유일한 완전 경로)는 API 키가 필요해 이 스크립트가 자동 실행하지 않는다. 값을 채우려면 그 스크립트를 별도로 돌려 이 JSON의 faces.<면>.api_path 에 사람이 채워 넣을 것.",
        "faces": {},
    }

    for face, cfg in FACES.items():
        result = run_face(args.python, args.model_dir, face, cfg["gold"], out_dir)
        summary["faces"][face] = {
            "gold_file": cfg["gold"],
            "known_contaminated_for_accuracy_claims": cfg["contaminated"],
            "contamination_note": cfg["note"],
            "offline_serving_path": result,
            "api_path": None,
        }

    summary_path = out_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    md_lines = [
        f"# 재현율/미탐률 회귀 리포트 — {args.label}",
        "",
        f"- model_dir: `{args.model_dir}`",
        f"- 측정 도구: `{summary['measurement_boundary']['tool']}` (τ=0, argmax)",
        f"- 이 도구가 **빼먹는 것**: {', '.join(summary['measurement_boundary']['excludes'])}",
        "- ⚠ API 경로(합의게이트 포함) 값은 별도로 채워야 한다 — 아래 표의 'API경로' 열은 비어 있다.",
        "",
        "| 면 | 오염(정확도 근거로 못 씀) | 방향성FNR(고등급평균) | 재현율(=1-FNR) | F1 | Acc | API경로 |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for face, data in summary["faces"].items():
        r = data["offline_serving_path"]
        if "error" in r:
            md_lines.append(f"| {face} | - | 오류 | - | - | - | (미측정) |")
            continue
        contam = "⚠ 예" if data["known_contaminated_for_accuracy_claims"] else "-"
        md_lines.append(
            f"| {face} | {contam} | {r['fnr_directional_high_grade_avg']:.1%} | "
            f"{r['recall_high_grade_avg']:.1%} | {r['f1_macro']:.3f} | {r['accuracy']:.1%} | (미측정) |"
        )
    md_lines += [
        "",
        "## 오염 셋 주의",
        *[f"- **{f}**: {c['note']}" for f, c in FACES.items() if c["contaminated"]],
        "",
        "## 다음 값을 채우려면",
        "`python scripts/measure_serving_fnr.py --gold <면> ...` (API 키 필요)를 돌려 summary.json 의 "
        "faces.<면>.api_path 에 결과를 사람이 채운다. 두 경로(오프라인/API) 값이 다른 것은 버그가 아니라 "
        "측정 경계가 다르기 때문이다 — 하나로 뭉쳐 인용하지 말 것.",
    ]
    (out_dir / "summary.md").write_text("\n".join(md_lines), encoding="utf-8")

    print(f"[recall_regression_report] 산출: {summary_path}")
    print(f"[recall_regression_report] 산출: {out_dir / 'summary.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
