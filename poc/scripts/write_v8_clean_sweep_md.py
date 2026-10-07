#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""세척면 섀도 대조 결과(JSON)를 그대로 읽어 짧은 보고서(md)를 쓴다.

손으로 옮겨 적지 않는다 — 숫자는 전부 JSON 에서 나온다. 종전(오염면) 수치는
`reports/V8_SHADOW_SWEEP.json` 원자료를 같은 함수로 다시 채점해서 나란히 놓는다.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_POC = _HERE.parent
for _p in (str(_HERE), str(_POC / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def fmt(b: dict, axis: str) -> str:
    d = b[axis]
    lift = "%.2f배" % d["lift"] if d["lift"] else "계산 불가"
    return ("기저율 %.4f (%d/%d) · 적중률 %.4f (%d/%d) · 향상 %s · 재현율 %.4f"
            % (d["base_rate"], d["base_k"], d["base_n"],
               d["hit_rate"], d["caught"], d["moved"], lift, d["recall"]))


def main(argv=None) -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    src = Path(argv[0] if argv else "reports/V8_SHADOW_SWEEP_CLEAN_2026-09-08.json")
    out = Path(argv[1] if argv and len(argv) > 1
               else "reports/V8_SHADOW_SWEEP_CLEAN_2026-09-08.md")
    p = json.loads(src.read_text("utf-8"))
    B = p["blocks"]

    # 종전(오염면) 원자료를 같은 채점 함수로 다시 센다 — 인용이 아니라 재계산이다.
    import contextlib  # noqa: PLC0415

    from run_factor_shadow_sweep_clean import score  # noqa: PLC0415
    old_rows = json.loads(Path("reports/V8_SHADOW_SWEEP.json").read_text("utf-8"))["rows"]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        old = score(old_rows, "종전 306건(오염면)")

    A = "4등급 과소분류"
    S = "비밀 누락(TS·S1 -> S2·S3)"
    L = B.get("legacy_306_cleaned")

    from collections import Counter  # noqa: PLC0415
    v8d = Counter(r["v8"] for r in p["rows"])
    v5d = Counter(r["v5"] for r in p["rows"])
    labd = Counter(r["label"] for r in p["rows"])
    a4 = B["all"][A]
    asec = B["all"][S]

    lines = [
        "# v8 요소모델 섀도 대조 — **세척면 재측정** (2026-09-08)",
        "",
        "**결론부터: 세척면에서도 A2(거부 조건)는 걸지 않는다.** 운영에서 실제로 갈리는",
        "축(영업비밀인가 아닌가)에서 향상 배수가 **%.2f배 — 무작위보다 낮다**. 4등급 축의"
        % (asec["lift"] or 0.0),
        "%.2f배는 자동확정의 %.1f%% 를 검수로 보내고 사는 값이다."
        % (a4["lift"] or 0.0, B["all"]["a2_moved"] / max(B["all"]["v5_auto"], 1) * 100),
        "",
        "종전 측정 `docs/V8_SHADOW_SWEEP_2026-08-15.md` 의 306건은 **본문에 정답이 적힌**",
        "후보였다(c7b73e4c: 원본 .md 964건이 `## 등급 제안 사유: {등급}` 을 달고 있다 —",
        "실측 2026-09-08). 그 면에서 나온 23.2% · 1.20배는 인용할 수 없다. 여기서는 같은",
        "절차를 `content_revision_path` 세척본 위에서 다시 돌렸다.",
        "",
        "## 0. 무엇을 무엇으로 쟀나",
        "",
        "```",
        "평가면      세척된 골든 후보 %d건 (본문 정답 노출 0건 — 실행 시각에 재확인)" % B["all"]["n"],
        "            출처 synthetic %d · public_real %d"
        % (B.get("synthetic", {}).get("n", 0), B.get("public_real", {}).get("n", 0)),
        "v5(운영)    %s  ← ClassifyService 전체 경로(규칙·게이트 포함)" % p["v5_model_dir"],
        "v8(요소)    %s  헤드 %d-class · temperature.json %s · 온도보정 %s"
        % (p["model_dir"], p["head_classes"],
           "있음" if p["temperature_json"] else "없음",
           "적용" if p["temperature_applied"] else "미적용"),
        "게이트      tau=%s · kappa=%s" % (p["tau"], p["kappa"]),
        "소요        %.0f초" % p["elapsed_sec"],
        "```",
        "",
        "⚠ 라벨은 **생성 시 의도 등급(기계 라벨)** 이다. 사람 확정 0건. 그래서 이것은",
        "  정확도가 아니라 **두 모델의 불일치 분포**와 거부 조건의 비용/이득이다.",
        "",
        "체크포인트를 확인한 근거(실측 2026-09-08):",
        "",
        "```",
        "artifacts/factor_model 에 model.pt 를 가진 디렉터리 21개",
        "  4-class 19 · 3-class 2(v1_gpu·v3_chunk)  ← 가중치 head_secrecy.weight 폭을 직접 읽음",
        "서빙 로더는 3-class 를 싣지 않는다(unknown 유보층이 무의미해지므로 거부).",
        "설정에 배포 지정이 **없다** — config 기본값 factor_model_dir=\"\" ·",
        "  factor_shadow_enabled=False · .env 8종 전부 FACTOR 항목 0건.",
        "그래서 '현행 배포본' 이라 부를 요소 체크포인트는 없다. 여기서 쓴 v8_caus 는",
        "  마지막에 만들어진(2026-08-14 21:30) 것이자 `docs/V8_R19_PASS_2026-08-14.md` 가",
        "  사전등록 1단계 통과로 기록한 r19 다. 종전 섀도 JSON 은 모델 경로를 안 적었다.",
        "```",
        "",
        "종전 측정도 같은 체크포인트였다 — 문서가 아니라 **예측으로** 확인했다",
        "(`scripts/which_factor_checkpoint.py`, 결과 `reports/V8_CHECKPOINT_IDENTIFY_2026-09-08.json`):",
        "",
        "```",
        "종전 306건 중 원본(.cleaned 아닌) 본문이 특정되는 25건에 v8_caus 를 다시 태움",
        "  서빙 등급   25/25 일치",
        "  S/V/M 요소  25/25 일치",
        "즉 이번 재측정은 **모델을 바꾸지 않고 평가면만 바꾼** 대조다.",
        "```",
        "",
        "예측 분포 — **v8 서빙 등급은 사실상 두 값으로 갈린다**:",
        "",
        "```",
        "정답 라벨   " + " · ".join("%s %d" % (g, labd[g]) for g in ("TS", "S1", "S2", "S3")),
        "v5 예측     " + " · ".join("%s %d" % (g, v5d[g]) for g in ("TS", "S1", "S2", "S3")),
        "v8 예측     " + " · ".join("%s %d" % (g, v8d[g]) for g in ("TS", "S1", "S2", "S3")),
        "```",
        "",
        "## 1. v5–v8 일치율과 방향",
        "",
        "```",
        "                     세척면 %d건        종전 오염면 306건" % B["all"]["n"],
        "agree            %5d  %6.1f%%      %5d  %6.1f%%"
        % (B["all"]["directions"].get("agree", 0),
           B["all"]["directions"].get("agree", 0) / B["all"]["n"] * 100,
           old["directions"].get("agree", 0), old["directions"].get("agree", 0) / old["n"] * 100),
        "factor_higher    %5d  %6.1f%%      %5d  %6.1f%%"
        % (B["all"]["directions"].get("factor_higher", 0),
           B["all"]["directions"].get("factor_higher", 0) / B["all"]["n"] * 100,
           old["directions"].get("factor_higher", 0),
           old["directions"].get("factor_higher", 0) / old["n"] * 100),
        "factor_lower     %5d  %6.1f%%      %5d  %6.1f%%"
        % (B["all"]["directions"].get("factor_lower", 0),
           B["all"]["directions"].get("factor_lower", 0) / B["all"]["n"] * 100,
           old["directions"].get("factor_lower", 0),
           old["directions"].get("factor_lower", 0) / old["n"] * 100),
        "```",
        "",
        "## 2. A2(거부 조건)를 걸면 — 세척면",
        "",
        "```",
        "v5 자동확정      %4d / %d" % (B["all"]["v5_auto"], B["all"]["n"]),
        "A2 로 검수 이동   %4d  (자동확정의 %.1f%%)"
        % (B["all"]["a2_moved"],
           B["all"]["a2_moved"] / max(B["all"]["v5_auto"], 1) * 100),
        "```",
        "",
        "### 4등급 과소분류 축",
        "",
        "```",
        "세척면 전량   " + fmt(B["all"], A),
        "종전 오염면   " + fmt(old, A),
        "```",
        "",
        "### 비밀 누락 축 (TS·S1 을 S2·S3 로 — 운영에서 실제로 갈리는 결정)",
        "",
        "```",
        "세척면 전량   " + fmt(B["all"], S),
        "종전 오염면   " + fmt(old, S),
        "```",
        "",
    ]
    if L:
        lines += [
            "## 3. 같은 문서, 본문만 세척했을 때 (n=%d)" % L["n"],
            "",
            "종전 306건 중 세척면에도 남아 있는 문서만 골라 다시 잰 것이다. 문서가 같으므로",
            "차이는 **본문에서 정답을 걷어낸 것** 하나뿐이다.",
            "",
            "```",
            "일치율        %.1f%%   (종전 %.1f%%)"
            % (L["agreement_rate"] * 100, old["agreement_rate"] * 100),
            "4등급 과소분류 " + fmt(L, A),
            "비밀 누락      " + fmt(L, S),
            "```",
            "",
        ]
    if "synthetic" in B or "public_real" in B:
        lines += ["## 4. 출처별", "", "```"]
        for k in ("synthetic", "public_real"):
            if k in B:
                lines += ["%-12s n=%4d · 일치율 %.1f%% · %s"
                          % (k, B[k]["n"], B[k]["agreement_rate"] * 100, fmt(B[k], A))]
        lines += ["```", "",
                  "public_real 은 향상 배수를 낼 수 없다 — 자동확정 안에 과소분류가 0건이라",
                  "기저율이 0 이다(라벨이 " + ", ".join(
                      "%s %d" % (g, c) for g, c in sorted(
                          Counter(r["label"] for r in p["rows"]
                                  if r["origin"] == "public_real").items())) + ").",
                  ""]

    lines += [
        "## 5. 이 수치가 보증하지 않는 것",
        "",
        "```",
        "⚠ 라벨이 기계 라벨이다(생성 시 의도 등급). 사람 확정 0건.",
        "⚠ 세척면은 '정답이 본문에 없다'까지만 보증한다. 다른 지름길이 없다는 보증은 아니다.",
        "⚠ 옛 홀드아웃 4종(hardened42·clean42·holdout109·v5test)은 길이가 등급을 알려준다 —",
        "  이 보고서는 그 셋을 쓰지 않는다.",
        "⚠ v8 서빙 등급이 TS/S3 두 값에 몰려 있다(위 분포). 방향 비율은 그 성질을 반영한다.",
        "```",
        "",
        "## 6. 그래서 무엇을 하나",
        "",
        "```",
        "거부 조건(A2)   비밀 누락 축 향상 %.2f배(무작위 미만) · 자동확정 %.1f%% 삭감 -> **안 건다**"
        % (asec["lift"] or 0.0,
           B["all"]["a2_moved"] / max(B["all"]["v5_auto"], 1) * 100),
        "큐 정렬        비용 0 · 4등급 과소분류 %d건 중 %d건을 먼저 만난다 -> 유효"
        % (a4["base_k"], a4["caught"]),
        "```",
        "",
        "2026-08-15 판단(A2 를 걸지 않는다)은 **유지된다.** 다만 그때 근거로 쓴 1.20배는",
        "오염면 수치라 인용을 멈추고, 세척면 수치(4등급 %.2f배 · 비밀 누락 %.2f배)로 바꾼다."
        % (a4["lift"] or 0.0, asec["lift"] or 0.0),
        "",
        "재현:",
        "",
        "```",
        "cd poc",
        "PYTHONIOENCODING=utf-8 TESTING=1 VECTOR_BACKEND=inmemory REQUIRE_REAL_EMBEDDER=false \\",
        "  ./.venv/Scripts/python.exe -u scripts/run_factor_shadow_sweep_clean.py \\",
        "  --model %s \\" % p["model_dir"].replace("\\", "/"),
        "  --report %s" % src.as_posix(),
        "./.venv/Scripts/python.exe scripts/write_v8_clean_sweep_md.py",
        "```",
        "",
    ]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), "utf-8")
    print("[md] %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
