# -*- coding: utf-8 -*-
"""전문가 검수 결과를 역할별(골든셋 · 학습셋 · 평가셋)로 나눠 재학습용 데이터셋으로 만든다.

왜 필요한가(2026-09-25). 검수를 받는 목적은 재학습이다. 그런데 콘솔의 검수 결정(결정 원장)을 학습 데이터로 바꾸는 코드가
리포에 없었고, 평가정답 승격은 학습에 쓴 문서 1,177건을 막는다(console_signoff.py). 그래서 학습 문서를 검수받아도 그 결정은
원장에만 남았다. 이 스크립트가 그 다리다 — 결정 원장을 **읽기만** 하고, 역할별 파일과 MANIFEST 를 새 폴더에 쓴다.

역할은 문서마다 **고정**이다(검수 배치 대장 internal_manifest.jsonl 의 training_use, 실제 학습 파일과 본문 sha256 으로 대조):
    fs_train_loss         → 1_학습셋/train.jsonl        재학습 입력. 검수 전 AI 라벨을 전문가 확정 라벨로 바꾼다
    fs_val_holdout        → 3_평가셋/val.jsonl          체크포인트(에폭) 선택
    never_trained         → 3_평가셋/dev.jsonl          개발 중 반복 평가·모델 비교·문턱 조정(학습엔 안 씀)
    never_trained_sealed  → 2_골든셋/golden.jsonl       최종 평가 — **한 번만** 연다. --include-golden 을 줘야 만든다
역할이 바뀌는 문서는 없다. 그래서 골든셋은 어떤 재학습에도 안 들어가고(9/20 협의 확정: 골든셋은 학습에 안 쓴 데이터),
학습셋은 검수를 거쳐 라벨만 좋아진다.

들어가는 문서(전문가 확정분만): 결정 상태가 approved_proxy·grade_fixed_unlocked 이고 확정 등급이 있으며, 결정한 사람이 실계정
검수자(golden_tiers.is_human_reviewer)이고, 검수 뒤 본문이 안 바뀌었고, 두 검수자가 서로 다른 등급을 내지 않은 문서.
나머지는 3_평가셋 밖 `검토_필요.jsonl` 에 사유와 함께 남는다 — 학습에도 평가에도 안 쓴다:
    pending(아직 검수 전) · deferred · discarded · out_of_scope · machine_actor · reviewer_conflict ·
    text_changed_after_review · no_grade
같은 문서를 두 전문가가 봐서 등급이 갈리면(reviewer_conflict) 자동으로 고르지 않는다 — 결정 원장은 최신 결정 하나만 남기므로
조정(adjudication)이 필요하다.

역할 폴더 사이에 같은 본문이 있으면(학습 문서와 평가 문서가 글자 그대로 같으면) 멈춘다 — train-on-test 차단.

리허설: --label-source ai_provisional 은 전문가 결정 없이 AI 잠정 라벨로 **역할 분할만** 확인한다(현재 실데이터로 각 역할이
몇 건인지, 실제 학습 파일과 맞는지). 산출물에 학습 금지 표식을 붙이고 학습 명령은 내보내지 않는다.

출력 행은 기존 FS 학습 파일(fs9_train.jsonl)과 같은 키(doc_id·text·label·family·round·source_name)에 출처 칸을 더한다
(label_origin · ai_label · label_changed · expert_id · decided_at). label_source 라는 이름은 일부러 안 쓴다 —
p1_train_classifier.py 가 그 키로 행을 거른다.

사용
    python -X utf8 scripts/build_review_role_datasets.py --out datasets/retrain_from_review_20261101            # 전문가 확정분
    python -X utf8 scripts/build_review_role_datasets.py --label-source ai_provisional --out <임시 폴더>       # 역할 분할 리허설
    python -X utf8 scripts/build_review_role_datasets.py --out <폴더> --include-golden                          # 최종 평가용 골든셋까지
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(POC / "src"))
sys.path.insert(0, str(POC / "scripts"))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

from audit_golden_candidate_pool import (  # noqa: E402
    DELIVERED_BATCH,
    REVIEW_MANIFEST,
    TRAIN_FILES,
    load_rows,
    training_overlap,
)
from koipa.golden_tiers import is_human_reviewer  # noqa: E402
from koipa.services.proxy_gold_candidate_service import ProxyGoldCandidateService  # noqa: E402

ROLES = {   # training_use → (역할 폴더, 파일)
    "fs_train_loss": ("1_학습셋", "train.jsonl"),
    "fs_val_holdout": ("3_평가셋", "val.jsonl"),
    "never_trained": ("3_평가셋", "dev.jsonl"),
    "never_trained_sealed": ("2_골든셋", "golden.jsonl"),
}
ROLE_LABEL = {"fs_train_loss": "학습셋", "fs_val_holdout": "평가셋(검증)", "never_trained": "평가셋(개발)",
              "never_trained_sealed": "골든셋"}
CONFIRMED = frozenset({"approved_proxy", "grade_fixed_unlocked"})
GRADES = frozenset({"TS", "S1", "S2", "S3"})
NOT_CONFIRMED_STATUS = {"proposed": "pending", "under_review": "pending", "deferred": "deferred",
                        "discarded": "discarded", "out_of_scope": "out_of_scope"}
EXCLUDED_FILE = "검토_필요.jsonl"
EPOCHS = 10        # run_final_train.py 의 FS7·FS9 팔과 같은 에폭
SEEDS = (42, 43, 44)   # 학습 1회 값으로 모델을 비교하지 않는다 — 3시드


def _load_jsonl(path: Path) -> list[dict]:
    out = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out


def reviewer_grades(events: list[dict]) -> dict[str, dict[str, str]]:
    """문서 → {검수자: 그 검수자가 마지막으로 낸 확정 등급}. reopen 은 그 문서의 이전 판정을 전부 무효로 한다."""
    per: dict[str, dict[str, str]] = collections.defaultdict(dict)
    for e in events:
        d, act = e.get("doc_id", ""), e.get("action")
        if act == "reopen":
            per[d].clear()
        elif act in ("approve", "change") and e.get("final_grade") in GRADES:
            per[d][str(e.get("actor_id") or "")] = e["final_grade"]
    return per


def _read_text(row: dict) -> str:
    p = Path(row["document_path"])
    return (p if p.is_absolute() else POC / p).read_text(encoding="utf-8")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def classify(rows: list[dict], manifest: dict[str, dict], events: list[dict], label_source: str):
    """검수 배치 문서를 역할별 포함 행과 제외 목록으로 나눈다."""
    per = reviewer_grades(events)
    included: dict[str, list[dict]] = {k: [] for k in ROLES}
    excluded: list[dict] = []
    for r in rows:
        if r.get("review_batch") != DELIVERED_BATCH:
            continue
        d = r["doc_id"]
        m = manifest.get(d)
        use = (m or {}).get("training_use", "")
        if use not in ROLES:
            excluded.append({"doc_id": d, "role": use or "?", "reason": "unknown_role", "detail": "대장에 역할이 없다"})
            continue
        text = _read_text(r)
        out = {"doc_id": d, "text": text, "family": m.get("family_id"), "round": m.get("round"),
               "source_name": "expert_review_batch", "ai_label": m["grade"]}
        if label_source == "ai_provisional":
            out.update(label=m["grade"], label_origin="ai_provisional", label_changed=False,
                       expert_id=None, decided_at=None)
            included[use].append(out)
            continue

        status, fg = r.get("status"), r.get("final_grade")
        latest = r.get("latest_decision") or {}
        actor = str(latest.get("actor_id") or "")
        why = None
        if status in NOT_CONFIRMED_STATUS:
            why = (NOT_CONFIRMED_STATUS[status], f"status={status}")
        elif status not in CONFIRMED or fg not in GRADES:
            why = ("no_grade", f"status={status} final_grade={fg}")
        elif not is_human_reviewer(actor):
            why = ("machine_actor", f"actor={actor!r}")
        else:
            grades = {g for g in per.get(d, {}).values()}
            ev_sha = str(latest.get("document_sha256") or "")
            if len(grades) > 1:
                why = ("reviewer_conflict", json.dumps(per[d], ensure_ascii=False))
            elif ev_sha and not (r["document_sha256"] == ev_sha or r["document_sha256"].startswith(ev_sha)):
                why = ("text_changed_after_review", "결정 당시 본문 지문과 지금 본문이 다르다")
        if why:
            excluded.append({"doc_id": d, "role": ROLE_LABEL[use], "reason": why[0], "detail": why[1]})
            continue
        out.update(label=fg, label_origin="expert_review", label_changed=(fg != m["grade"]), expert_id=actor,
                   decided_at=latest.get("decided_at"), n_reviewers=len(per.get(d, {})) or 1)
        included[use].append(out)
    return included, excluded


def check_disjoint(included: dict[str, list[dict]]) -> None:
    """역할 폴더 사이에 같은 본문이 있으면 멈춘다 — 학습 문서가 평가 문서와 같으면 평가가 부풀려진다."""
    seen: dict[str, str] = {}
    for use, docs in included.items():
        for d in docs:
            h = _sha(d["text"])
            if h in seen and seen[h] != use:
                raise SystemExit(f"⛔ 역할이 다른 두 문서의 본문이 같다: {d['doc_id']}({ROLE_LABEL[use]}) ↔ "
                                 f"{ROLE_LABEL[seen[h]]} — train-on-test 위험이라 멈춘다")
            seen[h] = use


def _by_grade(docs: list[dict]) -> dict[str, int]:
    return dict(sorted(collections.Counter(d["label"] for d in docs).items()))


def build(out: Path, *, label_source: str = "expert", include_golden: bool = False, root: Path | None = None,
          manifest_path: Path | None = None, verify_roles: bool = True, overwrite: bool = False) -> dict:
    if label_source == "ai_provisional" and include_golden:
        raise SystemExit("⛔ 골든셋은 전문가 확정 라벨로만 만든다 — ai_provisional 에서는 --include-golden 을 못 쓴다")
    manifest_path = manifest_path or REVIEW_MANIFEST
    if not manifest_path.exists():
        raise SystemExit(f"⛔ 검수 배치 대장이 없다: {manifest_path}")
    if out.exists() and any(out.iterdir()) and not overwrite:
        raise SystemExit(f"⛔ 출력 폴더가 비어 있지 않다: {out} (--overwrite 로 덮어쓸 수 있다)")

    svc = ProxyGoldCandidateService(root)
    rows = load_rows(svc.root)
    manifest = {m["review_id"]: m for m in _load_jsonl(manifest_path)}
    events = _load_jsonl(svc.ledger_path)

    verified: bool | None = None
    if verify_roles and all((POC / rel).exists() for _, rel in TRAIN_FILES.values()):
        t = training_overlap(rows)
        if t["label_mismatch"]:
            raise SystemExit(f"⛔ 대장의 학습 이력이 실제 학습 파일과 어긋난다({t['label_mismatch']}건) — 역할을 못 믿는다")
        verified = True
    included, excluded = classify(rows, manifest, events, label_source)
    check_disjoint(included)

    out.mkdir(parents=True, exist_ok=True)
    written: dict[str, dict] = {}
    for use, (folder, fname) in ROLES.items():
        docs = sorted(included[use], key=lambda d: d["doc_id"])
        entry = {"count": len(docs), "by_grade": _by_grade(docs),
                 "label_changed": sum(1 for d in docs if d.get("label_changed"))}
        if use == "never_trained_sealed" and not include_golden:
            entry["file"] = None
            entry["note"] = "만들지 않았다 — 최종 평가 때 --include-golden 으로 한 번만 만든다"
        else:
            entry["file"] = f"{folder}/{fname}"
            (out / folder).mkdir(parents=True, exist_ok=True)
            body = "".join(json.dumps({**d, "role": ROLE_LABEL[use]}, ensure_ascii=False) + "\n" for d in docs)
            (out / folder / fname).write_bytes(body.encode("utf-8"))
        written[ROLE_LABEL[use]] = entry
    (out / EXCLUDED_FILE).write_bytes("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in excluded).encode("utf-8"))

    ledger_bytes = svc.ledger_path.read_bytes() if svc.ledger_path.exists() else b""
    train = out / ROLES["fs_train_loss"][0] / ROLES["fs_train_loss"][1]
    val = out / ROLES["fs_val_holdout"][0] / ROLES["fs_val_holdout"][1]
    dev = out / ROLES["never_trained"][0] / ROLES["never_trained"][1]
    commands = [] if label_source != "expert" else [
        f"python -X utf8 scripts/p1_train_classifier.py --mode full --epochs {EPOCHS} --seed {s} "
        f"--train-path {train} --val-path {val} --test-path {dev} --output-dir {out / f'model_s{s}'} --no-mlflow"
        for s in SEEDS]
    summary = {
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "tool": "scripts/build_review_role_datasets.py",
        "label_source": label_source,
        "provisional_do_not_train": label_source == "ai_provisional",
        "ledger": {"path": str(svc.ledger_path), "events": len(events), "sha256": hashlib.sha256(ledger_bytes).hexdigest()},
        "roles_verified_against_training_files": verified,
        "roles": written,
        "not_included": dict(sorted(collections.Counter(e["reason"] for e in excluded).items())),
        "rules": ["역할은 문서마다 고정(대장 training_use) — 골든셋은 어떤 재학습에도 안 들어간다",
                  "전문가 확정분만 들어간다(사람 검수자 · 본문 불변 · 검수자 간 등급 일치)",
                  "역할 사이에 같은 본문이 있으면 멈춘다"],
        "next_train_commands": commands,
        "next_note": ("학습은 위 3시드 명령으로 돌리고 평가셋(dev)으로 비교한다. 골든셋은 최종 후보 하나가 정해진 뒤 한 번만 "
                      "연다(--include-golden)." if commands else "리허설 산출물이다 — 학습에 쓰지 않는다."),
    }
    (out / "MANIFEST.json").write_bytes(json.dumps(summary, ensure_ascii=False, indent=1).encode("utf-8"))
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, required=True, help="산출 폴더(비어 있어야 한다)")
    ap.add_argument("--label-source", choices=["expert", "ai_provisional"], default="expert")
    ap.add_argument("--include-golden", action="store_true", help="골든셋(봉인) 파일도 만든다 — 최종 평가 1회용")
    ap.add_argument("--no-verify-roles", action="store_true", help="대장의 역할을 실제 학습 파일과 대조하지 않는다")
    ap.add_argument("--overwrite", action="store_true")
    a = ap.parse_args()

    s = build(a.out, label_source=a.label_source, include_golden=a.include_golden,
              verify_roles=not a.no_verify_roles, overwrite=a.overwrite)
    print(f"라벨 출처 = {s['label_source']}" + ("  ⚠ 리허설 — 학습 금지" if s["provisional_do_not_train"] else ""))
    print(f"역할과 실제 학습 파일 대조: {s['roles_verified_against_training_files']}")
    for name, e in s["roles"].items():
        print(f"  {name:<10}{e['count']:>6,}건  등급 {e['by_grade']}  라벨 바뀜 {e['label_changed']:,}"
              + (f"  → {e['file']}" if e["file"] else f"  ({e['note']})"))
    if s["not_included"]:
        print("  안 들어간 문서(검토_필요.jsonl):", s["not_included"])
    for c in s["next_train_commands"]:
        print("  ▶", c)
    print(f"산출 폴더: {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
