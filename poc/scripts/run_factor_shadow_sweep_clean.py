#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""v5(운영) vs v8(요소) 섀도 대조를 **세척된 후보면**에서 다시 잰다.

왜 다시 재는가(2026-09-08). 종전 측정 `docs/V8_SHADOW_SWEEP_2026-08-15.md` 는 후보
306건에서 나왔는데, 그 후보 본문에 `## 등급 제안 사유: {등급}` 이 그대로 적혀 있었다
(c7b73e4c, 988건 중 964건). 답이 본문에 있는 문서로 잰 값은 모델이 아니라 지름길을
잰 것이다. 그래서 그 문서의 23.2% · 1.20배는 인용할 수 없다.

이 스크립트는 같은 절차를 **`content_revision_path` 로 세척본을 읽는 면**에서 돌린다.
후보 적재는 `eval_on_clean_candidates.load_candidates()` 를 그대로 쓴다 — 콘솔과 같은
우선순위이고, 본문 정답 노출 0건임을 실행 시각에 다시 확인한다.

원본 스크립트(`run_factor_shadow_sweep.py`)와 다른 점은 셋뿐이다:

    1. 후보를 ProxyGoldCandidateService(원장·상태 필터) 대신 파일 메타에서 전량 적재
    2. 적재 직후 본문 정답 노출을 세고, 0건이 아니면 **멈춘다**
    3. 체크포인트를 인자로 받고, 실제 헤드 폭(3/4-class)을 리포트에 적는다

⚠ 라벨은 생성 시 의도 등급(기계 라벨)이다. 사람 확정은 0건이다. 그래서 여기 나오는
  것은 정확도가 아니라 **두 모델의 불일치 분포**와 거부 조건의 비용/이득이다.

사용:
    python scripts/run_factor_shadow_sweep_clean.py --model artifacts/factor_model/v8_caus
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_POC = _HERE.parent
for _p in (str(_HERE), str(_POC / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_T0 = time.perf_counter()
ORDER = {"TS": 0, "S1": 1, "S2": 2, "S3": 3}
SECRET = {"TS", "S1"}  # 영업비밀 축 — 이 둘을 S2/S3 로 보면 '비밀 누락'이다


def wilson_upper(k: int, n: int, z: float = 1.96) -> float:
    from koipa.modules.m6_evaluation.eval_cards import wilson_interval  # noqa: PLC0415

    return wilson_interval(k, n, z=z)[1]


def _head_classes(model_dir: Path) -> int:
    import torch  # noqa: PLC0415

    from koipa.modules.m5_inference.factor_model import head_classes  # noqa: PLC0415

    # mmap=True — 헤드 폭 하나 읽자고 741MB 를 메모리에 올리지 않는다(실측 v8_caus).
    try:
        state = torch.load(model_dir / "model.pt", map_location="cpu",
                           weights_only=True, mmap=True)
    except (RuntimeError, TypeError, ValueError):
        state = torch.load(model_dir / "model.pt", map_location="cpu", weights_only=True)
    return head_classes(state)


def main(argv=None) -> int:
    # line_buffering — 긴 실행이라 진행이 보여야 한다(감싸면서 -u 를 덮어쓴다).
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace",
                                  line_buffering=True)
    ap = argparse.ArgumentParser(description="세척면에서 v5/v8 섀도 전수 대조")
    ap.add_argument("--model", default="artifacts/factor_model/v8_caus",
                    help="요소 모델 체크포인트 디렉터리")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--report", default="reports/V8_SHADOW_SWEEP_CLEAN_2026-09-08.json")
    ap.add_argument("--legacy-ids", default="reports/V8_SHADOW_SWEEP.json",
                    help="종전 306건 doc_id 목록(부분집합 재집계용). 없으면 건너뛴다")
    ap.add_argument("--classifier", default="artifacts/classifier_p1_v5_clean/v-fe4b386b",
                    help="v5 서빙 가중치. 비우면 rule-fallback 이 되고 status 가 전부 "
                         "needs_review 로 나와 A2 계산이 무의미해진다(실측 2026-09-08)")
    ap.add_argument("--resume", default="", help="중간 저장 JSONL. 있으면 이어서 돈다")
    a = ap.parse_args(argv)

    os.environ.setdefault("TESTING", "1")
    os.environ.setdefault("VECTOR_BACKEND", "inmemory")
    os.environ.setdefault("REQUIRE_REAL_EMBEDDER", "false")
    if a.classifier:
        os.environ["CLASSIFIER_MODEL_DIR"] = a.classifier

    def step(msg: str) -> None:
        print("[step] %-28s %6.1fs" % (msg, time.perf_counter() - _T0))

    step("start")
    from eval_on_clean_candidates import _ANSWER, load_candidates  # noqa: PLC0415
    step("import eval_on_clean")
    from koipa.config import settings  # noqa: PLC0415
    step("import koipa.config")
    from koipa.modules.m5_inference.factor_model import (  # noqa: PLC0415
        apply_serving_gate,
        get_factor_inference,
        shadow_compare,
    )
    step("import factor_model")
    from koipa.schemas.classify import ClassifyRequest  # noqa: PLC0415
    step("import schemas")
    from koipa.services.classify_service import ClassifyService  # noqa: PLC0415
    step("import classify_service")

    mdir = Path(a.model)
    if not (mdir / "model.pt").is_file():
        print("[error] 체크포인트 없음: %s" % mdir)
        return 2
    n_head = _head_classes(mdir)
    has_temp = (mdir / "temperature.json").is_file()
    print("[model] %s · 헤드 %d-class · temperature.json %s"
          % (mdir, n_head, "있음" if has_temp else "없음"))

    inf = get_factor_inference(str(mdir), base=settings.factor_model_base,
                               max_len=settings.factor_model_max_len)
    if not inf.load():
        print("[error] 요소 모델 로드 실패: %s" % inf.load_error)
        return 2
    print("[model] 로드 성공 · device=%s · 온도보정=%s"
          % (inf._device, "적용" if inf._temperature else "없음"))

    rows = load_candidates()
    leaked = sum(1 for r in rows if _ANSWER.search(r["text"]))
    print("[data] 후보 %d건 · 본문 정답 노출 %d건" % (len(rows), leaked))
    if leaked:
        print("[error] 세척면이 아니다 — clean_candidate_answer_leak.py 먼저 돌릴 것")
        return 2
    if a.limit:
        rows = rows[:a.limit]

    from koipa.services.classify_service import (  # noqa: PLC0415
        _resolve_serving_model_dir,
    )
    resolved = _resolve_serving_model_dir()
    print("[v5] 서빙 가중치 = %s" % (resolved or "rule-fallback(모델 없음)"))
    svc = ClassifyService()

    out: list[dict] = []
    done: set[str] = set()
    rpath = Path(a.resume) if a.resume else None
    if rpath and rpath.is_file():
        for line in rpath.read_text("utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                out.append(rec)
                done.add(rec["doc_id"])
        print("[resume] 이어받기 %d건" % len(out))
    fh = rpath.open("a", encoding="utf-8") if rpath else None

    t0 = time.perf_counter()
    for i, r in enumerate(rows):
        if r["doc_id"] in done:
            continue
        try:
            resp = svc.classify(ClassifyRequest(doc_id=r["doc_id"], content=r["text"],
                                                return_evidence=False))
            v5 = resp.label.value if hasattr(resp.label, "value") else str(resp.label)
            status = resp.status
        except Exception as exc:  # noqa: BLE001
            print("  [skip] %s: %s" % (r["doc_id"], type(exc).__name__))
            continue
        pred = inf.predict(r["text"])
        if pred is None:
            continue
        codes, probs = pred
        fp = apply_serving_gate(codes, probs, metadata=None,
                                tau=settings.factor_tau, kappa=settings.factor_kappa)
        cmp_ = shadow_compare(v5, fp)
        rec = {
            "doc_id": r["doc_id"], "label": r["label"], "origin": r["origin"],
            "v5": v5, "v8": fp.serving_grade, "direction": cmp_["direction"],
            "v5_status": status, "v8_auto": fp.auto_confirmable,
            "min_conf": cmp_["min_confidence"], "factors": fp.named,
        }
        out.append(rec)
        if fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
        if (i + 1) % 25 == 0:
            print("  %d/%d · %.0fs" % (i + 1, len(rows), time.perf_counter() - t0),
                  flush=True)
    if fh:
        fh.close()

    payload = {
        "generated": "2026-09-08",
        "surface": "cleaned candidate pool (content_revision_path)",
        "surface_leak_checked": True,
        "v5_model_dir": resolved or "rule-fallback",
        "python": sys.executable,
        "model_dir": str(mdir), "head_classes": n_head, "temperature_json": has_temp,
        "temperature_applied": bool(inf._temperature),
        "tau": settings.factor_tau, "kappa": settings.factor_kappa,
        "elapsed_sec": round(time.perf_counter() - t0, 1),
        "rows": out,
    }
    payload["blocks"] = {"all": score(out, "전량")}
    for org in ("synthetic", "public_real"):
        sub = [r for r in out if r["origin"] == org]
        if sub:
            payload["blocks"][org] = score(sub, "출처=" + org)

    legacy = Path(a.legacy_ids)
    if legacy.is_file():
        old = json.loads(legacy.read_text("utf-8"))
        ids = {r["doc_id"] for r in old.get("rows", [])}
        sub = [r for r in out if r["doc_id"] in ids]
        payload["legacy_overlap"] = {"legacy_n": len(ids), "matched_n": len(sub)}
        if sub:
            payload["blocks"]["legacy_306_cleaned"] = score(
                sub, "종전 306건과 겹치는 문서 — 본문만 세척")

    Path(a.report).parent.mkdir(parents=True, exist_ok=True)
    Path(a.report).write_text(json.dumps(payload, ensure_ascii=False, indent=1), "utf-8")
    print("\n[report] %s" % a.report)
    print("\n⚠ 라벨이 기계 라벨(생성 시 의도 등급)이라 이 수치는 정확도가 아니라 불일치 분포다.")
    return 0


def under(r: dict, key: str) -> bool:
    """r[key] 등급이 라벨보다 **낮게** 보였는가(=과소분류)."""
    lab = r.get("label")
    return bool(lab) and ORDER.get(r[key], 9) > ORDER.get(lab, 9)


def secret_miss(r: dict, key: str) -> bool:
    """영업비밀(TS·S1)을 비-영업비밀(S2·S3)로 봤는가 = 비밀 누락."""
    return r.get("label") in SECRET and r.get(key) not in SECRET


def score(rows: list[dict], title: str) -> dict:
    n = len(rows)
    dirs = Counter(r["direction"] for r in rows)
    print("\n" + "=" * 74)
    print(" %s   n=%d" % (title, n))
    print("=" * 74)
    print(" 1. 두 모델이 얼마나 다른가")
    for k in ("agree", "factor_higher", "factor_lower"):
        print("    %-16s %4d  %6.1f%%" % (k, dirs[k], dirs[k] / max(n, 1) * 100))

    v5_auto = [r for r in rows if r["v5_status"] != "needs_review"]
    moved = [r for r in v5_auto if r["direction"] == "factor_higher"]
    moved_ids = {r["doc_id"] for r in moved}
    na = len(v5_auto)
    print("\n 2. A2(v8 이 더 높게 보면 검수로) 를 걸면")
    print("    현재 v5 자동확정   %4d / %d  %6.1f%%" % (na, n, na / max(n, 1) * 100))
    print("    A2 로 검수 이동    %4d       자동확정의 %.1f%%"
          % (len(moved), len(moved) / max(na, 1) * 100))
    print("    남는 자동확정      %4d / %d  %6.1f%%"
          % (na - len(moved), n, (na - len(moved)) / max(n, 1) * 100))

    res = {"title": title, "n": n, "directions": dict(dirs),
           "agreement_rate": round(dirs["agree"] / max(n, 1), 4),
           "v5_auto": na, "a2_moved": len(moved)}

    for axis, fn in (("4등급 과소분류", under), ("비밀 누락(TS·S1 -> S2·S3)", secret_miss)):
        base_k = [r for r in v5_auto if fn(r, "v5")]
        caught = [r for r in moved if fn(r, "v5")]
        base = len(base_k) / max(na, 1)
        hit = len(caught) / max(len(moved), 1)
        remain_pool = [r for r in v5_auto if r["doc_id"] not in moved_ids]
        remain = [r for r in remain_pool if fn(r, "v5")]
        print("\n 3. 거래 판정 — %s" % axis)
        print("    기저율(자동확정 안)   %6.4f   (%d / %d)" % (base, len(base_k), na))
        print("    A2 지목 적중률        %6.4f   (%d / %d)" % (hit, len(caught), len(moved)))
        print("    향상 배수(lift)       %6.2f배" % (hit / base if base else 0.0))
        print("    재현율                %6.4f   (%d / %d)"
              % (len(caught) / max(len(base_k), 1), len(caught), len(base_k)))
        print("    지목 비용             자동확정의 %.1f%%" % (len(moved) / max(na, 1) * 100))
        print("    남는 오류             %d / %d = %6.4f · 95%% 상한 %.4f"
              % (len(remain), len(remain_pool),
                 len(remain) / max(len(remain_pool), 1),
                 wilson_upper(len(remain), max(len(remain_pool), 1))))
        res[axis] = {
            "base_k": len(base_k), "base_n": na, "base_rate": round(base, 4),
            "caught": len(caught), "moved": len(moved), "hit_rate": round(hit, 4),
            "lift": round(hit / base, 3) if base else None,
            "recall": round(len(caught) / max(len(base_k), 1), 4),
            "remaining": len(remain), "remaining_n": len(remain_pool),
            "remaining_rate": round(len(remain) / max(len(remain_pool), 1), 4),
            "remaining_upper95": round(wilson_upper(len(remain), max(len(remain_pool), 1)), 4),
        }
    return res


if __name__ == "__main__":
    sys.exit(main())
