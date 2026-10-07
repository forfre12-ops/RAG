"""요소값 0 사람 표본 감사 — 표본 추출 + 블라인드 검수팩 생성.

무엇을 재는가. 학습셋의 `expected_factor_scores` 에서 0 은 두 가지를 동시에 뜻한다:
"부재가 입증됐다"(proven_absent) 와 "본문에 언급이 없다"(unknown). 정본 원칙은 앞의
경우에만 0 을 허용한다. 이 스크립트는 그 구분을 사람이 판정할 표본을 뽑는다.

왜 무작위 100건이 아닌가(실측 2026-08-22, datasets/labeled_v6_factor_grounded/train.jsonl):

    management=0  732건인데 근거 span 이 **고유 12문장**뿐이다
    secrecy=0      92건 / 고유 16문장
    value=0       156건 / 고유 16문장

무작위로 뽑으면 같은 문장을 12번 다시 읽는다. 판정 단위는 문서가 아니라 **근거 문장 x
문서 맥락**이므로 문장 템플릿을 층으로 쓴다.

세 갈래로 뽑는다:
    A_zero    요소값 0 인 문서. 감사 대상.
    A_control 요소값 1 또는 2 인 문서. 같은 질문을 던져 검수자 신뢰도를 잰다
              (present span 에 "부재 단정" 이라 답하면 그 검수자 응답은 못 쓴다).
    B_real    실문서. 요소 라벨이 아예 없으므로 "본문만으로 요소를 정할 수 있는가" 를
              직접 묻는다. business_work 는 이미 소비된 면이라 써도 된다
              (v8_real_probe.py:85). ⛔ business_sealed 는 봉인이므로 쓰지 않는다.

출력은 세 파일이다. blind_key 는 집계 전까지 검수자에게 주지 않는다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

# 요소 -> evidence_card.factors 의 키
FACTOR_TO_CARD_KEY = {
    "secrecy": "nonpublicity",
    "value": "competitive_value",
    "management": "access_controls",
}
FACTOR_KO = {"secrecy": "비공지성(S)", "value": "경제적 유용성(V)", "management": "비밀관리성(M)"}


def _load(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]


def _span(rec: dict, factor: str) -> dict | None:
    spans = (
        (rec.get("evidence_card") or {}).get("factors", {})
        .get(FACTOR_TO_CARD_KEY[factor], {})
        .get("spans")
    )
    return spans[0] if spans else None


def _stratify(rows: list[dict], factor: str, level_pred) -> dict[tuple, list[dict]]:
    """층 = (근거문장, 등급). 근거가 없으면 층 키에 '' 를 쓴다(그 자체가 결함 신호)."""
    out: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        fs = r.get("expected_factor_scores") or {}
        if factor not in fs or not level_pred(fs[factor]):
            continue
        sp = _span(r, factor)
        out[(sp["quote"] if sp else "", r.get("label"))].append(r)
    return out


def _take(cells: dict[tuple, list[dict]], per_cell: int, rng: random.Random) -> list[tuple]:
    """층마다 per_cell 건. 층이 그보다 작으면 전수."""
    picked = []
    for key in sorted(cells):
        pool = sorted(cells[key], key=lambda r: r["doc_id"])
        rng.shuffle(pool)
        for r in pool[: min(per_cell, len(pool))]:
            picked.append((key, r))
    return picked


def build(args) -> int:
    rng = random.Random(args.seed)
    syn_path = _ROOT / args.synthetic
    syn = _load(syn_path)

    items: list[dict] = []
    key: list[dict] = []

    def emit(arm: str, rec: dict, factor: str, asked_level: int, stratum: str, corpus: str) -> None:
        sp = _span(rec, factor)
        rid = "AF%04d" % (len(items) + 1)
        items.append({
            "review_id": rid,
            "corpus": corpus,
            "factor": factor,
            "factor_ko": FACTOR_KO[factor],
            "document_type": rec.get("document_type"),
            "text": rec.get("text", ""),
            # 검수자에게 보이는 근거. 없으면 None -> 화면이 "근거 없음" 으로 표시한다.
            "evidence_quote": sp["quote"] if sp else None,
            "evidence_start": sp["start"] if sp else None,
            "evidence_end": sp["end"] if sp else None,
        })
        key.append({
            "review_id": rid, "arm": arm, "corpus": corpus, "doc_id": rec.get("doc_id"),
            "factor": factor, "asked_level": asked_level, "stratum": stratum,
            "label": rec.get("label"), "svm": rec.get("expected_factor_scores"),
            "factor_profile_id": rec.get("factor_profile_id"),
            "text_sha256": hashlib.sha256(rec.get("text", "").strip().encode()).hexdigest(),
        })

    # ── A_zero ─────────────────────────────────────────────────────────────
    plan = {"management": args.n_mgmt, "secrecy": args.n_secrecy, "value": args.n_value}
    for factor, per_cell in plan.items():
        cells = _stratify(syn, factor, lambda lv: lv == 0)
        for (quote, label), rec in _take(cells, per_cell, rng):
            emit("A_zero", rec, factor, 0, f"{factor}=0|{label}|{quote[:24]}", "synthetic_v6")

    # ── A_control : 같은 요소가 1·2 인 문서 ─────────────────────────────────
    # 신뢰도 확인용이라 추정치를 낼 필요가 없다. 층은 고르게 훑되 총량만 잡는다.
    ctrl_cells = _stratify(syn, args.control_factor, lambda lv: lv in (1, 2))
    ctrl = _take(ctrl_cells, 1, rng)
    rng.shuffle(ctrl)
    for (quote, label), rec in ctrl[: args.n_control_total]:
        lv = rec["expected_factor_scores"][args.control_factor]
        emit("A_control", rec, args.control_factor, lv,
             f"{args.control_factor}={lv}|{label}|{quote[:24]}", "synthetic_v6")

    # ── B_real : 실문서. 요소 라벨이 없으니 근거도 없다 ──────────────────────
    real = _load(_ROOT / args.real)
    by_grade: dict[str, list[dict]] = defaultdict(list)
    for r in real:
        by_grade[r.get("label")].append(r)
    for g in sorted(by_grade):
        pool = sorted(by_grade[g], key=lambda r: r.get("doc_id", ""))
        rng.shuffle(pool)
        for rec in pool[: args.n_real_per_grade]:
            emit("B_real", rec, "management", -1, f"real|{g}", "v8_real_business_work")

    out_dir = _ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    rng.shuffle(items)                      # 화면 순서에서 층을 못 읽게 섞는다
    (out_dir / "review_pack.jsonl").write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in items) + "\n", encoding="utf-8")
    (out_dir / "blind_key.jsonl").write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in key) + "\n", encoding="utf-8")
    (out_dir / "manifest.json").write_text(json.dumps({
        "schema": "factor-zero-audit-v1",
        "seed": args.seed,
        "synthetic_source": args.synthetic,
        "real_source": args.real,
        "n_items": len(items),
        "by_arm": Counter(k["arm"] for k in key),
        "by_factor": Counter(k["factor"] for k in key),
        "n_strata": len({k["stratum"] for k in key}),
    }, ensure_ascii=False, indent=2, default=int), encoding="utf-8")
    print(json.dumps({
        "out_dir": str(out_dir), "n_items": len(items),
        "by_arm": dict(Counter(k["arm"] for k in key)),
        "by_factor": dict(Counter(k["factor"] for k in key)),
        "n_strata": len({k["stratum"] for k in key}),
    }, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="요소값 0 사람 감사 표본 추출")
    ap.add_argument("--synthetic", default="datasets/labeled_v6_factor_grounded/train.jsonl")
    ap.add_argument("--real", default="datasets/v8_real/business_work.jsonl")
    ap.add_argument("--out-dir", default="datasets/audit/factor_zero_v1")
    ap.add_argument("--seed", type=int, default=20260822)
    ap.add_argument("--n-mgmt", type=int, default=3, help="management=0 층(문장x등급)당 건수")
    ap.add_argument("--n-secrecy", type=int, default=1)
    ap.add_argument("--n-value", type=int, default=1)
    ap.add_argument("--control-factor", default="management")
    ap.add_argument("--n-control-total", type=int, default=20, help="신뢰도 확인용 present-span 문항 수")
    ap.add_argument("--n-real-per-grade", type=int, default=6)
    return build(ap.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
