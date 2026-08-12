"""평가 리포트 렌더러 CLI — HTML(+혼동행렬 PNG) 산출 (FUN-004-⑤ · FUN-024-③).

`m6_evaluation.report` 의 렌더러(render_html_report·render_confusion_matrix_png)에 대한
실행 진입점이다. 이 스크립트가 생기기 전에는 두 함수의 호출자가 단위 테스트뿐이어서
"리포트를 HTML/PNG 형태로 제공한다"는 산출물 주장에 실행 경로가 없었다.

입력은 둘 중 하나:
  --from-db MODEL_VERSION  : DB classifications+corrections 로 진실/예측 페어를 뽑아 산출
  --pairs PATH.jsonl       : {"y_true": "...", "y_pred": "..."} 줄 단위 파일 (DB 없이 산출)

사용:
  python scripts/render_eval_report.py --from-db v-dd3abab9 --out reports/eval_v-dd3abab9.html
  python scripts/render_eval_report.py --pairs datasets/eval_pairs.jsonl --out reports/eval.html
  python scripts/render_eval_report.py --pairs ... --png-out reports/cm.png   # PNG 별도 저장

종료코드: 0=산출 성공 · 2=입력 없음/표본 0 (무음 성공 금지 — 명시 실패)
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from koipa.modules.m6_evaluation.confusion_matrix import build_confusion_matrix  # noqa: E402
from koipa.modules.m6_evaluation.metrics import (  # noqa: E402
    compute_metrics_from_arrays,
    compute_metrics_from_db,
)
from koipa.modules.m6_evaluation.report import (  # noqa: E402
    render_confusion_matrix_png,
    render_html_report,
)


def _load_pairs(path: Path) -> tuple[list[str], list[str]]:
    """jsonl → (y_true, y_pred). 라벨 키가 없는 줄은 건너뛰되 개수를 알린다."""
    y_true: list[str] = []
    y_pred: list[str] = []
    skipped = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        t, p = row.get("y_true"), row.get("y_pred")
        if not t or not p:
            skipped += 1
            continue
        y_true.append(str(t))
        y_pred.append(str(p))
    if skipped:
        print(f"[report] skipped {skipped} rows without y_true/y_pred", file=sys.stderr)
    return y_true, y_pred


def main() -> int:
    ap = argparse.ArgumentParser(description="평가 리포트 HTML/PNG 렌더러")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--from-db", metavar="MODEL_VERSION", help="DB에서 진실/예측 페어 추출")
    src.add_argument("--pairs", metavar="PATH", help="jsonl 페어 파일 (y_true·y_pred)")
    ap.add_argument("--out", default="reports/eval_report.html", help="HTML 출력 경로")
    ap.add_argument("--png-out", default=None, help="혼동행렬 PNG 별도 저장 경로(선택)")
    ap.add_argument(
        "--no-cm-image", action="store_true",
        help="HTML 내 CM 이미지 임베드 생략(matplotlib 미설치 환경)",
    )
    args = ap.parse_args()

    if args.from_db:
        metrics = compute_metrics_from_db(args.from_db)
        if metrics is None:
            print(
                f"[report] no classifications for model_version={args.from_db!r} "
                "(DB 미가용이거나 해당 버전 분류 0건) — 리포트를 만들지 않습니다.",
                file=sys.stderr,
            )
            return 2
        # from-db 경로는 CM 재구성을 위해 페어를 다시 필요로 하므로 metrics 의 CM 을 그대로 쓴다.
        y_true, y_pred = _pairs_from_metrics(metrics)
        model_version = args.from_db
    else:
        pairs_path = Path(args.pairs)
        if not pairs_path.exists():
            print(f"[report] pairs file not found: {pairs_path}", file=sys.stderr)
            return 2
        y_true, y_pred = _load_pairs(pairs_path)
        if not y_true:
            print("[report] 표본 0건 — 리포트를 만들지 않습니다.", file=sys.stderr)
            return 2
        metrics = compute_metrics_from_arrays(y_true, y_pred, model_version=pairs_path.stem)
        model_version = pairs_path.stem

    cm_result = build_confusion_matrix(y_true, y_pred, labels=metrics.labels)
    measured_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

    html = render_html_report(
        metrics, cm_result,
        measured_at=measured_at,
        include_cm_image=not args.no_cm_image,
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"[report] HTML → {out} (model={model_version} · n={metrics.sample_count})")

    if args.png_out:
        png_out = Path(args.png_out)
        png_out.parent.mkdir(parents=True, exist_ok=True)
        try:
            png_out.write_bytes(render_confusion_matrix_png(cm_result))
            print(f"[report] CM PNG → {png_out}")
        except Exception as exc:  # noqa: BLE001
            # matplotlib 미설치 등 — HTML 은 이미 산출됐으므로 실패를 명시만 하고 성공 유지.
            print(f"[report] PNG 렌더 실패(HTML은 산출됨): {exc}", file=sys.stderr)
    return 0


def _pairs_from_metrics(metrics) -> tuple[list[str], list[str]]:
    """MetricsResult.confusion_matrix 를 (y_true, y_pred) 페어로 되풀어낸다.

    from-db 경로는 페어를 두 번 조회하지 않으려고 이미 계산된 CM 을 역전개한다.
    셀 (i, j) 개수만큼 (labels[i], labels[j]) 를 만들면 CM·지표가 동일하게 재현된다.
    """
    labels = list(metrics.labels)
    y_true: list[str] = []
    y_pred: list[str] = []
    for i, row in enumerate(metrics.confusion_matrix or []):
        for j, count in enumerate(row):
            y_true.extend([labels[i]] * int(count))
            y_pred.extend([labels[j]] * int(count))
    return y_true, y_pred


if __name__ == "__main__":
    raise SystemExit(main())
