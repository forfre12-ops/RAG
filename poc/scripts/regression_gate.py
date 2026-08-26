# -*- coding: utf-8 -*-
"""회귀 게이트 — 고치기 전 상태를 찍어 두고, 고친 뒤 무엇이 달라졌는지 센다.

왜 필요한가(사용자 지시 2026-08-26). "결함 고치다가 · 배선 추가하다가 잘 되던 기능을
망가뜨리면 안 된다." 약속으로는 못 막는다. 고치기 전과 후를 같은 방법으로 재서
**달라진 것이 0 인지** 확인해야 한다.

집계 수치만 보면 안 된다 — 등급일치율이 그대로여도 문서별로는 서로 상쇄되며 바뀔 수 있다.
그래서 **문서 단위**로 비교한다.

찍는 것(스냅샷):
  ① 판정면   평가셋 4종 993건의 문서별 룰 등급 · 신뢰도 · 경고 유무
  ② API 계약  경로 집합 · 응답 스키마 필드 집합
  ③ 운영 파라미터  프로파일이 결정하는 플래그·임계값
  ④ 모델      배포 모델 디렉터리와 가중치 파일 해시

사용:
    python scripts/regression_gate.py --snapshot        # 고치기 전에 한 번
    ... 코드 수정 ...
    python scripts/regression_gate.py                   # 델타 확인 (회귀면 exit 1)

판정:
    등급이 바뀐 문서 > 0        → 회귀 후보. 의도한 변경이면 --accept 로 새 기준 채택
    API 경로·필드가 사라짐      → 회귀 (추가는 허용)
    플래그·임계가 바뀜          → 회귀 후보
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

BASELINE = _ROOT / "reports" / "regression_baseline.json"

EVAL_SETS = {
    "hardened42": "datasets/gold_real/holdout_eval.hardened.jsonl",
    "clean42": "datasets/gold_real/holdout_eval.clean.jsonl",
    "holdout109": "datasets/gold_real/_rejudge_claude/holdout109_provenance_corrected.jsonl",
    "v3_final800": "datasets/proxy_eval/direct_authored_proxy_eval_split.v3/final_800.locked.jsonl",
}

WATCHED_SETTINGS = [
    "deploy_profile", "review_confidence_threshold", "classifier_temperature",
    "classifier_escalation_tau", "agreement_gate_enabled", "metadata_floor_enabled",
    "source_prior_enabled", "source_prior_cap_grade", "storage_encryption_enabled",
    "require_safety_gates", "rule_semantic_threshold", "rule_high_risk_weight_multiplier",
    "rule_fallback_min_evidence", "severe_agg_codes", "model_secondopinion_llm_enabled",
    "similarity_escalation_enabled", "ts_tie_break_enabled", "auto_rollback_enabled",
    "drift_detection_enabled", "enable_training", "enable_incremental_retrain",
]


def seed_probe_rows() -> list[dict]:
    """시드마다 그 시드 하나만 담은 짧은 문서를 만든다 — 회귀 탐지 전용 탐침.

    왜 필요한가(실측 2026-08-26). 평가셋 993건은 시드 404개 중 **16개(4.0%)만** 건드린다.
    나머지 388개는 한 번도 매칭되지 않아, 그 시드를 고쳐도 판정면 델타가 0 으로 나온다.
    실제로 '특급기밀' 가중치를 1.0 → 0.2 로 바꿔 봤는데 게이트가 아무것도 잡지 못했다.
    커버리지 4% 짜리 가드는 가드가 아니다.

    ⚠ 이것은 **평가셋이 아니다.** 정답 라벨이 없고 정확도를 재지 않는다. 오직 "시드를
    건드리면 판정이 달라지는가"만 본다. 성능 수치로 인용 금지.
    """
    from koipa.modules.m3_labeling.seeds import KEYWORD_SEEDS

    rows = []
    for i, s in enumerate(KEYWORD_SEEDS):
        kw = s["keyword"]
        rows.append({
            "doc_id": f"probe-{i:04d}",
            # 앞뒤에 중립 문장을 둬서 시드가 문장 안에서 매칭되게 한다(실사용과 같은 형태).
            "text": f"본 문서는 사내 검토 자료다. {kw} 관련 사항을 아래에 정리한다. 이상.",
        })
    return rows


def snap_decisions() -> dict:
    """문서별 룰 판정 — 빠르고 결정적이라 회귀 신호로 쓴다.

    평가셋 4종 + 시드 탐침(seed_probe). 탐침이 시드 전수를 덮어 커버리지 구멍을 막는다.
    """
    from koipa.modules.m3_labeling.rule_engine import LabelRuleEngine, has_real_evidence
    from koipa.modules.m3_labeling.seeds import KEYWORD_SEEDS

    eng = LabelRuleEngine(seeds=KEYWORD_SEEDS)
    out: dict[str, dict] = {}

    probe: dict[str, str] = {}
    for r in seed_probe_rows():
        res = eng.label(r["text"])
        probe[r["doc_id"]] = (
            f"{res.grade}|{res.confidence:.3f}|{int(has_real_evidence(res))}|"
            f"{len(res.matched_keywords)}|{res.total_score:.3f}"
        )
    out["seed_probe"] = probe

    for name, rel in EVAL_SETS.items():
        p = _ROOT / rel
        if not p.exists():
            continue
        rows = [json.loads(x) for x in p.read_text("utf-8").splitlines() if x.strip()]
        per: dict[str, str] = {}
        for i, r in enumerate(rows):
            res = eng.label(r.get("text") or "")
            key = str(r.get("doc_id") or r.get("id") or i)
            # 등급 · 신뢰도(소수 3자리) · 실근거 유무 · 경고 개수 — 이 넷이 바뀌면 판정이 달라진 것
            per[key] = f"{res.grade}|{res.confidence:.3f}|{int(has_real_evidence(res))}|{len(res.warnings)}"
        out[name] = per
    return out


def snap_api() -> dict:
    os.environ.setdefault("TESTING", "1")
    from koipa.api.app import app

    spec = app.openapi()
    paths = sorted(
        f"{m.upper()} {p}"
        for p, d in spec["paths"].items()
        for m in d
        if m in ("get", "post", "put", "patch", "delete")
    )
    schemas = {
        n: sorted((s.get("properties") or {}).keys())
        for n, s in (spec.get("components", {}).get("schemas") or {}).items()
    }
    return {"paths": paths, "schemas": schemas}


def snap_settings() -> dict:
    from koipa.config import settings

    out = {}
    for n in WATCHED_SETTINGS:
        v = getattr(settings, n, None)
        out[n] = v if isinstance(v, (str, int, float, bool, type(None))) else str(v)
    return out


def snap_model() -> dict:
    from koipa.config import settings

    d = _ROOT / str(getattr(settings, "classifier_model_dir", "") or "")
    info = {"dir": str(getattr(settings, "classifier_model_dir", ""))}
    if d.is_dir():
        for w in ("model.safetensors", "pytorch_model.bin", "temperature.json"):
            f = d / w
            if f.exists():
                h = hashlib.sha256()
                with open(f, "rb") as fh:
                    for blk in iter(lambda: fh.read(1 << 20), b""):
                        h.update(blk)
                info[w] = h.hexdigest()[:16]
    return info


def take() -> dict:
    return {
        "decisions": snap_decisions(),
        "api": snap_api(),
        "settings": snap_settings(),
        "model": snap_model(),
    }


def compare(base: dict, now: dict) -> int:
    regressions = 0
    print("=" * 74)
    print(" ① 판정면 — 문서별 등급·신뢰도·근거·경고")
    print("=" * 74)
    for name in sorted(set(base["decisions"]) | set(now["decisions"])):
        b = base["decisions"].get(name, {})
        n = now["decisions"].get(name, {})
        if not b or not n:
            print(f"  {name:<14} 한쪽에만 있음 — 비교 불가")
            continue
        changed = [k for k in b if k in n and b[k] != n[k]]
        gone = [k for k in b if k not in n]
        added = [k for k in n if k not in b]
        mark = "OK " if not (changed or gone) else "델타"
        print(f"  [{mark}] {name:<14} 변경 {len(changed):>3} · 사라짐 {len(gone):>3} · 추가 {len(added):>3} / {len(b)}건")
        for k in changed[:5]:
            print(f"          {k}:  {b[k]}  →  {n[k]}")
        if len(changed) > 5:
            print(f"          … 외 {len(changed)-5}건")
        regressions += len(changed) + len(gone)

    print("\n" + "=" * 74)
    print(" ② API 계약 — 사라진 것만 회귀로 본다(추가는 허용)")
    print("=" * 74)
    bp, np_ = set(base["api"]["paths"]), set(now["api"]["paths"])
    lost, new = sorted(bp - np_), sorted(np_ - bp)
    for p in lost:
        print(f"  [회귀] 경로 사라짐: {p}")
    for p in new:
        print(f"  [추가] {p}")
    regressions += len(lost)
    for sname, fields in base["api"]["schemas"].items():
        nf = now["api"]["schemas"].get(sname)
        if nf is None:
            print(f"  [회귀] 스키마 사라짐: {sname}")
            regressions += 1
            continue
        missing = sorted(set(fields) - set(nf))
        if missing:
            print(f"  [회귀] {sname} 필드 사라짐: {missing}")
            regressions += len(missing)
    if not lost and not new:
        print("  변화 없음")

    print("\n" + "=" * 74)
    print(" ③ 운영 파라미터")
    print("=" * 74)
    diff = [(k, base["settings"].get(k), now["settings"].get(k))
            for k in base["settings"] if base["settings"].get(k) != now["settings"].get(k)]
    for k, a, c in diff:
        print(f"  [델타] {k}: {a!r} → {c!r}")
        regressions += 1
    if not diff:
        print("  변화 없음")

    print("\n" + "=" * 74)
    print(" ④ 배포 모델")
    print("=" * 74)
    if base["model"] != now["model"]:
        for k in sorted(set(base["model"]) | set(now["model"])):
            if base["model"].get(k) != now["model"].get(k):
                print(f"  [델타] {k}: {base['model'].get(k)} → {now['model'].get(k)}")
                regressions += 1
    else:
        print("  변화 없음")

    print("\n" + "=" * 74)
    if regressions:
        print(f" 회귀 후보 {regressions}건 — 의도한 변경이면 --accept 로 기준을 갱신한다.")
    else:
        print(" 회귀 없음 — 판정면·계약·파라미터·모델 모두 그대로다.")
    print("=" * 74)
    return 1 if regressions else 0


def main(argv=None) -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="회귀 게이트")
    ap.add_argument("--snapshot", action="store_true", help="현재 상태를 기준으로 저장")
    ap.add_argument("--accept", action="store_true", help="델타를 의도된 변경으로 보고 기준 갱신")
    ap.add_argument("--out", default=str(BASELINE))
    a = ap.parse_args(argv)

    out = Path(a.out)
    now = take()
    if a.snapshot or (a.accept and not out.exists()):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(now, ensure_ascii=False, indent=1), encoding="utf-8")
        n = sum(len(v) for v in now["decisions"].values())
        print(f"기준 저장: {out}")
        print(f"  판정면 {n}건 · API 경로 {len(now['api']['paths'])} · 스키마 {len(now['api']['schemas'])}"
              f" · 파라미터 {len(now['settings'])}")
        return 0

    if not out.exists():
        print(f"기준 파일이 없다: {out}\n  먼저 실행: python scripts/regression_gate.py --snapshot")
        return 2
    base = json.loads(out.read_text("utf-8"))
    rc = compare(base, now)
    if a.accept:
        out.write_text(json.dumps(now, ensure_ascii=False, indent=1), encoding="utf-8")
        print("\n--accept — 현재 상태를 새 기준으로 저장했다.")
        return 0
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
