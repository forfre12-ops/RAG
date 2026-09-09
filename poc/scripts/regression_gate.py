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
    "ts_tie_break_enabled", "auto_rollback_enabled",
    "drift_detection_enabled", "enable_training", "enable_incremental_retrain",
    # [2026-09-05] 보존기간 삭제 — 켜지면 감사 증빙이 지워진다. 조용히 바뀌면
    # 안 되는 파괴적 플래그라 감시 대상에 넣는다.
    "retention_enabled", "retention_audit_log_days", "retention_llm_usage_days",
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


# 판정에 영향을 주면서 **환경변수로 덮이는** 값들. 기준을 뜬 셸과 비교하는 셸이 다르면
# 코드를 한 줄도 안 건드려도 축 ③ 에 델타가 뜬다 — 유령 회귀다.
#
# 실측 2026-09-10: 기준선을 METADATA_FLOOR_ENABLED 없이 떠 놓고 그 변수를 주고 비교하니
# `metadata_floor_enabled: False → True` 가 회귀 후보 1건으로 잡혔다. 프로파일 불일치는
# 이미 막고 있었는데(아래 deploy_profile 검사), **개별 플래그는 안 막고 있었다.**
# 대응이 다르다 — 코드 회귀는 코드를 고치고, 환경 불일치는 조건을 맞춘다.
_JUDGMENT_ENV = (
    "METADATA_FLOOR_ENABLED",
    "AGREEMENT_GATE_ENABLED",
    "SOURCE_PRIOR_ENABLED",
    "CLASSIFIER_TEMPERATURE",
    "CLASSIFIER_ESCALATION_TAU",
    "REVIEW_CONFIDENCE_THRESHOLD",
)

# 축 ③ 의 파라미터가 아니라 **비교 가능성**을 적어 두는 자리. 값 비교에서 제외한다.
_ENV_KEY = "_env_overrides"


def snap_settings() -> dict:
    from koipa.config import settings

    out = {}
    for n in WATCHED_SETTINGS:
        v = getattr(settings, n, None)
        out[n] = v if isinstance(v, (str, int, float, bool, type(None))) else str(v)
    out[_ENV_KEY] = {k: os.environ[k] for k in _JUDGMENT_ENV if k in os.environ}
    return out


def snap_model() -> dict:
    """배포 모델의 가중치 해시. **잴 수 없으면 그렇게 적는다.**

    [2026-09-06] 종전에는 classifier_model_dir 이 비어 있으면 `_ROOT / ""` 가 리포 루트가
    되어 is_dir() 을 통과하고, 루트에 가중치 파일이 없으니 해시를 하나도 안 담은 채
    {"dir": ""} 만 남겼다. 그 값은 매번 같으므로 비교가 늘 통과했고, 출력은
    "④ 배포 모델 · 변화 없음" 이었다 — **잴 수 없었던 것을 통과로 보고한 것이다.**

    "잴 수 없다"와 "재 봤더니 같다"는 다른 사실이다. 섞으면 게이트를 믿을 수 없다.
    """
    from koipa.config import settings

    raw_dir = str(getattr(settings, "classifier_model_dir", "") or "").strip()
    info: dict = {"dir": raw_dir}
    if not raw_dir:
        # 빈 값이면 리포 루트로 떨어지지 않도록 여기서 끊는다.
        info["measured"] = False
        # [2026-09-06] 사유만 적으면 운영자는 "여긴 못 재는 환경"으로 읽고 넘긴다.
        # 실제로는 환경변수 하나다 — 모델은 리포에 있다. 할 일을 같이 적는다.
        info["why"] = (
            "classifier_model_dir 이 비어 있다 — 환경변수를 주면 잰다: "
            "CLASSIFIER_MODEL_DIR=artifacts/classifier_p1_v5_clean/v-fe4b386b "
            "(Makefile 의 P1_MODEL 과 같은 값)"
        )
        return info

    d = _ROOT / raw_dir
    if not d.is_dir():
        info["measured"] = False
        info["why"] = "모델 디렉터리가 없다: %s" % d
        return info

    info["measured"] = True
    if True:
        for w in ("model.safetensors", "pytorch_model.bin", "temperature.json"):
            f = d / w
            if f.exists():
                h = hashlib.sha256()
                with open(f, "rb") as fh:
                    for blk in iter(lambda: fh.read(1 << 20), b""):
                        h.update(blk)
                info[w] = h.hexdigest()[:16]
    if not any(k.endswith((".safetensors", ".bin", ".json")) for k in info):
        info["measured"] = False
        info["why"] = "디렉터리는 있는데 가중치 파일이 없다: %s" % d
    return info


# 검수 라우팅 게이트가 여전히 열리는가 — 탐침표.
#
# ⚠ 이 탐침은 **모델 없이** 돈다(룰 폴백). 그래서 모델 신뢰도에 걸리는 게이트
#   (저신뢰 임계 · 합의 · 2차의견)는 여기서 안 잡힌다. 잡히는 것만 잡는다고 적어 두고,
#   못 잡는 것을 잡은 척하지 않는다.
GATE_PROBES = [
    # (이름, 본문, 메타데이터)
    ("m0_conflict",
     "국방용 전자전투 시스템의 초고주파 레이더 설계 파라미터와 시험 결과를 정리한 문서입니다.",
     {"access_scope": "all_employees"}),
    ("high_grade_plain",
     "국방용 전자전투 시스템의 초고주파 레이더 설계 파라미터와 시험 결과를 정리한 문서입니다.",
     None),
    ("restricted_access_conflict",
     "사내 공지사항입니다. 다음 주 전사 워크숍 일정을 안내드립니다. 참석 부탁드립니다.",
     {"access_scope": "approved_only"}),
    ("security_marking_floor",
     "사내 공지사항입니다. 다음 주 전사 워크숍 일정을 안내드립니다. 참석 부탁드립니다.",
     {"security_marking": "secret"}),
    ("abbrev_only",
     "본 문서는 EUV, ALD, CMP 공정에 관한 내부 자료입니다. 상세 조건은 별첨 참조.",
     None),
    ("no_metadata",
     "당사의 차세대 반도체 식각 공정 레시피와 수율 개선 파라미터를 정리한 문서입니다.",
     None),
]

# 라우팅에 쓰이는 경고 토큰 — classify_service 의 게이트가 읽는 그 문자열이다.
GATE_TOKENS = (
    "cap-conflict", "sparse-evidence", "abbrev-only-escalation",
    "body_below_classifiable_threshold", "metadata-access-conflict",
    "metadata-management-conflict", "s2-underclass-risk", "gate-fail-open",
    "metadata-floor", "source-prior", "fnr-safe override",
)


def snap_gates() -> dict:
    """탐침 문서마다 **어느 게이트가 발동했는가**.

    [2026-09-06 신설] 종전 4축은 이 축을 안 봤다. 룰 등급이 그대로여도 게이트가 조용히
    안 열리면 문서는 자동확정으로 나간다 — 등급은 안 변했으니 판정면 축에도 안 걸린다.
    실제로 오늘 이 자리에서 **시험이 하나도 없는 게이트 둘**을 찾았다.

    플래그(metadata_floor_enabled 등)는 축 ③ 이 따로 감시하므로 여기서는 **강제로 켜고
    게이트 로직 자체**를 잰다. 플래그가 꺼져 있어서 안 열린 것과 로직이 깨져서 안 열린 것은
    다른 사실이고, 섞으면 어느 쪽인지 알 수 없다.
    """
    os.environ.setdefault("TESTING", "1")
    from koipa.config import settings

    saved = {}
    for flag in ("metadata_floor_enabled", "agreement_gate_enabled"):
        saved[flag] = getattr(settings, flag, None)
        try:
            setattr(settings, flag, True)
        except Exception:  # noqa: BLE001 — 못 켜면 못 켠 대로 잰다(아래 note 에 남는다)
            pass

    out: dict = {}
    try:
        from koipa.modules.m5_inference.pipeline import InferencePipeline

        pipe = InferencePipeline()
        for name, text, md in GATE_PROBES:
            res = pipe.run(text, metadata=md)
            label = res.label.value if hasattr(res.label, "value") else str(res.label)
            fired = sorted({t for t in GATE_TOKENS if any(t in w for w in (res.warnings or []))})
            out[name] = "%s|%.3f|%s" % (label, float(res.confidence), ",".join(fired))
    except Exception as exc:  # noqa: BLE001
        # 못 재면 **못 쟀다고 적는다.** 조용히 빈 dict 를 남기면 다음 비교가 통과한다.
        out = {"__measured__": "False", "__why__": "%s: %s" % (type(exc).__name__, exc)}
    finally:
        for flag, val in saved.items():
            if val is not None:
                try:
                    setattr(settings, flag, val)
                except Exception:  # noqa: BLE001
                    pass
    return out


def take() -> dict:
    return {
        "decisions": snap_decisions(),
        "gates": snap_gates(),
        "api": snap_api(),
        "settings": snap_settings(),
        "model": snap_model(),
    }


def compare(base: dict, now: dict) -> int:
    regressions = 0
    # 못 잰 축이 있으면 요약에서 "전부 그대로"라고 말하지 않는다.
    unmeasured = False
    # [2026-09-06] 축마다 **실제로 쟀는지**를 기록한다. 종전에는 요약의 '잰 것' 목록이
    # 고정 문자열이었고, 그래서 재지 못한 축까지 잰 것처럼 실렸다. 세어서 만든다.
    measured_axes: list[str] = ["판정면", "API 계약", "운영 파라미터"]
    skipped_axes: list[str] = []
    # 기준을 뜬 프로파일과 지금 프로파일이 다르면 비교가 성립하지 않는다.
    # 온도·합의게이트·메타데이터 floor 가 프로파일 소속이라 판정면이 통째로 달라지고,
    # 코드를 한 줄도 안 건드려도 회귀가 수십 건 뜬다(2026-08-27 실측: 기준 full-train
    # 인데 기본값 lite-noapi 로 돌려 26건). 세지 말고 조건을 맞추라고 말한다.
    _bp = base["settings"].get("deploy_profile")
    _np = now["settings"].get("deploy_profile")
    if _bp != _np:
        print("=" * 74)
        print(" 비교 불가 — 프로파일이 다르다")
        print("=" * 74)
        print(f"  기준 스냅샷 : {_bp!r}")
        print(f"  이번 실행   : {_np!r}")
        print("")
        print("  프로파일이 온도보정·합의게이트·메타데이터 floor 를 함께 바꾸므로")
        print("  판정면이 통째로 달라진다. 이 상태의 델타는 회귀가 아니다.")
        print(f"  같은 조건으로 다시 실행할 것:  DEPLOY_PROFILE={_bp} python scripts/regression_gate.py")
        return 2

    # 프로파일이 같아도 **개별 환경변수**가 다르면 같은 일이 벌어진다(실측 2026-09-10:
    # metadata_floor_enabled False→True 가 회귀 후보로 잡혔다). 프로파일과 같은 대응이다 —
    # 세지 말고 조건을 맞추라고 말한다.
    _be = base["settings"].get(_ENV_KEY) or {}
    _ne = now["settings"].get(_ENV_KEY) or {}
    if _be != _ne:
        print("=" * 74)
        print(" 비교 불가 — 판정에 영향을 주는 환경변수가 다르다")
        print("=" * 74)
        for key in sorted(set(_be) | set(_ne)):
            print(f"  {key:<30} 기준 {_be.get(key, '(없음)')!r}  →  이번 {_ne.get(key, '(없음)')!r}")
        print("")
        print("  이 값들은 게이트 발동과 임계를 바꾸므로 판정면이 함께 움직인다.")
        print("  이 상태의 델타는 코드 회귀가 아니다. 기준과 같은 환경으로 다시 실행할 것:")
        cmd = " ".join(f"{k}={v}" for k, v in sorted(_be.items())) or "(환경변수 없이)"
        print(f"    {cmd} python scripts/regression_gate.py")
        return 2
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
    # _ENV_KEY 는 파라미터가 아니라 **비교 가능성** 기록이다. 위에서 따로 검사했고,
    # 여기서 또 세면 같은 사실이 회귀 1건으로 둔갑한다.
    diff = [(k, base["settings"].get(k), now["settings"].get(k))
            for k in base["settings"]
            if k != _ENV_KEY and base["settings"].get(k) != now["settings"].get(k)]
    # 프로파일이 다르면 여기 값 대부분이 함께 움직인다. 그걸 회귀로 세면 코드를 하나도
    # 안 건드려도 "회귀 26건" 이 뜬다(2026-08-27 실측: 기준은 full-train, 실행은 기본값
    # lite-noapi 였다). 코드 문제와 환경 문제는 대응이 다르므로 갈라서 말한다.
    for k, a, c in diff:
        print(f"  [델타] {k}: {a!r} → {c!r}")
        regressions += 1
    if not diff:
        print("  변화 없음")

    print("\n" + "=" * 74)
    print(" ④ 배포 모델")
    print("=" * 74)
    # [2026-09-06] why 는 사람에게 하는 설명이지 측정값이 아니다. 문구를 고치면 회귀로
    # 잡혀서, 안내를 개선할수록 게이트가 시끄러워진다 — 그러면 아무도 안 고친다.
    _b_model = {k: v for k, v in base["model"].items() if k != "why"}
    _n_model = {k: v for k, v in now["model"].items() if k != "why"}
    if _b_model != _n_model:
        for k in sorted(set(_b_model) | set(_n_model)):
            if _b_model.get(k) != _n_model.get(k):
                print(f"  [델타] {k}: {_b_model.get(k)} → {_n_model.get(k)}")
                regressions += 1
    elif not now["model"].get("measured", False):
        # [2026-09-06] **못 잰 것을 '변화 없음' 이라 말하지 않는다.**
        # classifier_model_dir 이 비어 있으면 해시를 하나도 담지 못하는데, 그 값은 매번
        # 같으므로 비교가 늘 통과했다. 그래서 이 축이 비어 있는 채로 통과를 보고했다.
        unmeasured = True
        print("  ⚠ **재지 못했다** — %s" % now["model"].get("why", "사유 불명"))
        skipped_axes.append("배포 모델")
        print("     이 환경에서는 모델 축이 회귀를 잡지 못한다. 배포 서버에서 다시 돌릴 것.")
    else:
        print("  변화 없음")

    print("\n" + "=" * 74)
    print(" ⑤ 검수 라우팅 게이트")
    print("=" * 74)
    bg = base.get("gates") or {}
    ng = now.get("gates") or {}
    if not bg:
        # 옛 기준 파일에는 이 축이 없다 — 회귀로 세지 않고 갱신을 안내한다.
        print("  ⚠ 기준에 이 축이 없다 — --accept 로 기준을 갱신하면 다음부터 잰다.")
        unmeasured = True
        skipped_axes.append("검수 라우팅")
    elif ng.get("__measured__") == "False":
        print("  ⚠ **재지 못했다** — %s" % ng.get("__why__", "사유 불명"))
        unmeasured = True
        skipped_axes.append("검수 라우팅")
    else:
        measured_axes.append("검수 라우팅")
        gdiff = 0
        for k in sorted(set(bg) | set(ng)):
            if bg.get(k) != ng.get(k):
                gdiff += 1
                regressions += 1
                print("  %s\n      전: %s\n      후: %s" % (k, bg.get(k), ng.get(k)))
        if not gdiff:
            print("  변화 없음 — 탐침 %d건" % len(ng))

    print("\n" + "=" * 74)
    if regressions:
        print(f" 회귀 후보 {regressions}건 — 의도한 변경이면 --accept 로 기준을 갱신한다.")
    elif unmeasured:
        # [2026-09-06] 못 잰 축이 있으면 **'전부 그대로'라고 말하지 않는다.**
        # 이 문장이 근거로 인용된다 — 오늘 하루 이 게이트를 열 번 넘게 인용했는데
        # 모델 축은 비어 있었다.
        print(" 회귀 없음 — 다만 **재지 못한 축이 있다**: %s" % " · ".join(skipped_axes))
        print(" 잰 것: %s" % " · ".join(measured_axes))
    else:
        print(" 회귀 없음 — %s · 배포 모델 모두 그대로다." % " · ".join(measured_axes))
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
        print(f"  판정면 {n}건 · 게이트 탐침 {len(now.get('gates') or {})}건"
              f" · API 경로 {len(now['api']['paths'])} · 스키마 {len(now['api']['schemas'])}"
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
