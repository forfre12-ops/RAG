# -*- coding: utf-8 -*-
"""골든 검수 후보 원장(datasets/proxy_gold/single_document_candidates)이 몇 건이고 무엇이 섞여 있는지 센다.

왜 필요한가(2026-09-25). "후보가 왜 3,598건이냐, 1,731건 아니냐"가 세 번 되물어졌다. 그때마다
기억나는 묶음을 더해 설명했고, 두 번 틀렸다("800건은 옛 회차" — 실제로는 1,731건 안의
문서와 본문이 같은 사본이었다). 세는 일은 기억이 아니라 도구가 한다.

분모는 화면이 세는 것과 같아야 하므로 **화면과 같은 서비스**(ProxyGoldCandidateService)의
행을 그대로 쓴다. 파일을 따로 읽어 세면 화면 숫자와 어긋난다.

센 것
  ① 총 행 수 · 고유 doc_id · 고유 본문(sha256)
  ② doc_id 꼴별 · review_batch별 · 출처(실문서/합성) · 등급 확정 건수
  ③ 본문이 글자 그대로 같은 묶음(서로 다른 doc_id 가 같은 문서를 가리키는 것)
  ④ 같은 본문 사본 중 **학습에 쓴 문서의 사본인데 평가정답 차단 목록에 없는 것** —
     승격 검사가 doc_id 로 걸러서(console_signoff.build_promotion_inputs), 사본은 검사를 통과한다.
  ⑤ 이번 회차 문서 중 **실제 학습·검증·개발 파일에 본문이 들어 있는 것** — 대장(training_use) 라벨이
     아니라 파일 본문의 sha256 대조다. 지금 배포된 모델의 학습셋(v5_clean)과의 겹침도 함께 센다.
  ⑥ 전문가 검수를 요청할 문서 — 재학습이 목적이라 학습 여부가 아니라 **문서 품질**(블라인드 판정자 일치 ·
     프로그램 검사 · 작성 모델)로 고른 목록과 제외 사유(--review-order 로 문서 id 목록을 파일로 쓴다).
     --write-exclusions 는 제외 20건을 evidence/review_request_exclusions.jsonl 에 남기고, 지재원에 가는 번들
     (scripts/build_offline_bundle.py)이 그 문서를 안 싣는다 — 넘기는 후보가 정확히 요청 대상이 되게 한다.

안 센 것: 글자가 조금 다른 근접 중복(문서 쌍 전수 비교라 무겁다). 본문 완전일치만 본다 — 하한이다.

이 스크립트는 후보 폴더를 바꾸지 않는다(--write-exclusions·--review-order 만 파일을 쓴다). 요청 대상이 아닌 후보를 치우는 것은
archive_non_requested_candidates.py 다.

사용:  poc/.venv/Scripts/python.exe -X utf8 scripts/audit_golden_candidate_pool.py [--json]
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
import sys
from pathlib import Path

POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(POC / "src"))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

from koipa.console_signoff import eval_blocked_doc_ids  # noqa: E402
from koipa.services.proxy_gold_candidate_service import ProxyGoldCandidateService  # noqa: E402

# 이번 검수 회차(전문가 전달본 1,731건)의 내부 대장 — review_id 별 학습 사용 구분이 들어 있다.
REVIEW_MANIFEST = POC / "datasets" / "expert_review_all_1731_20260924" / "internal_manifest.jsonl"
DELIVERED_BATCH = "expert_review_1731_20260924"
# 학습·검증에 쓴 문서는 평가정답으로 못 간다(scripts/block_1731_trained_docs_from_eval.py 와 같은 기준).
TRAINED_USES = frozenset({"fs_train_loss", "fs_val_holdout"})

_KIND_RE = re.compile(r"^(MD|FD|GOLD-B\d|GOLD-PILOT|GOLD-CAND|GOLD-UPL)")


def kind_of(doc_id: str) -> str:
    """doc_id 꼴. 해시 이름은 공개 실문서 계열이다."""
    m = _KIND_RE.match(doc_id)
    if not m:
        return "해시 이름"
    k = m.group(1)
    return "GOLD-B" if k.startswith("GOLD-B") else k


def load_rows(root: Path | None = None) -> list[dict]:
    return ProxyGoldCandidateService(root)._candidates()


def duplicate_groups(rows: list[dict]) -> list[list[dict]]:
    """본문 sha256 이 같은 행 묶음(2건 이상)."""
    by: dict[str, list[dict]] = collections.defaultdict(list)
    for r in rows:
        by[r["document_sha256"]].append(r)
    return [g for g in by.values() if len(g) > 1]


def review_uses() -> dict[str, str]:
    """review_id(MD-####) → training_use. 대장이 없으면 빈 사전."""
    if not REVIEW_MANIFEST.exists():
        return {}
    out = {}
    for line in REVIEW_MANIFEST.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            out[r["review_id"]] = r.get("training_use", "")
    return out


# 실제로 학습·검증·개발 평가에 쓴 파일. 대장의 training_use 라벨을 믿지 않고 이번 회차 문서의
# 본문 sha256 을 이 파일들의 본문과 직접 대조한다(2026-09-25 "1,731건은 학습 안 한 건가?").
# 역할: train = 학습 손실 · val = 체크포인트 선택 · dev = 모델 비교 평가 · deployed = 지금 배포된 모델의 학습셋
_FS = "reports/mock_final_train_20260921/"
TRAIN_FILES = {
    "FS9 학습": ("train", _FS + "fs9_train.jsonl"),
    "FS7 학습": ("train", _FS + "fs7_train.jsonl"),
    "검증 fs_val": ("val", _FS + "fs_val.jsonl"),
    "개발 fs_dev_test": ("dev", _FS + "fs_dev_test.jsonl"),
    "6차 r6_v2": ("dev", _FS + "r6_v2.jsonl"),
    "8차 r8_v2": ("dev", _FS + "r8_v2.jsonl"),
    "배포모델 v5_clean train": ("deployed", "datasets/labeled_p1_v5_clean/train.jsonl"),
    "배포모델 v5_clean val": ("deployed", "datasets/labeled_p1_v5_clean/val.jsonl"),
    "배포모델 v5_clean test": ("deployed", "datasets/labeled_p1_v5_clean/test.jsonl"),
}


def _text_hashes(path: Path) -> set[str]:
    out: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            d = json.loads(line)
            t = d.get("text") or d.get("body") or d.get("content") or ""
            out.add(hashlib.sha256(t.encode("utf-8")).hexdigest())
    return out


def training_overlap(rows: list[dict], files: dict | None = None) -> dict:
    """이번 회차 문서 중 몇 건이 실제 학습·검증·개발·배포모델 학습셋 파일에 들어 있나."""
    mine = {r["document_sha256"]: r["doc_id"] for r in rows if r.get("review_batch") == DELIVERED_BATCH}
    uses = review_uses()
    seen: dict[str, dict] = {}
    by_role: dict[str, set[str]] = collections.defaultdict(set)
    for label, (role, rel) in (files or TRAIN_FILES).items():
        p = POC / rel
        if not p.exists():
            seen[label] = {"role": role, "rows": None, "overlap": None}
            continue
        hs = _text_hashes(p)
        hit = hs & mine.keys()
        seen[label] = {"role": role, "rows": len(hs), "overlap": len(hit)}
        by_role[role] |= hit
    used = by_role["train"] | by_role["val"]
    # 대장 라벨(학습·검증에 씀)과 파일 대조가 다른 문서 — 0 이어야 대장을 믿을 수 있다.
    mismatch = sum(1 for h, doc_id in mine.items()
                   if (uses.get(doc_id, "") in TRAINED_USES) != (h in used))
    return {"delivered": len(mine), "files": seen, "used": len(used),
            "not_used": len(mine) - len(used), "label_mismatch": mismatch,
            "deployed_overlap": len(by_role["deployed"]), "manifest_found": bool(uses)}


def audit(rows: list[dict]) -> dict:
    groups = duplicate_groups(rows)
    blocked = eval_blocked_doc_ids()
    uses = review_uses()
    shapes = collections.Counter("+".join(sorted(kind_of(r["doc_id"]) for r in g)) for g in groups)

    # 사본 = 묶음 안에서 이번 회차(전달본) 문서가 아닌 쪽. 전달본 쪽이 학습에 썼는지로 위험을 잰다.
    copies_of_trained = copies_unblocked = 0
    for g in groups:
        keep = [r for r in g if r.get("review_batch") == DELIVERED_BATCH]
        copies = [r for r in g if r.get("review_batch") != DELIVERED_BATCH]
        if len(keep) != 1 or not copies:
            continue
        trained = uses.get(keep[0]["doc_id"], "") in TRAINED_USES
        if trained:
            copies_of_trained += len(copies)
            copies_unblocked += sum(1 for c in copies if c["doc_id"] not in blocked)

    return {
        "rows": len(rows),
        "unique_doc_ids": len({r["doc_id"] for r in rows}),
        "unique_texts": len({r["document_sha256"] for r in rows}),
        "by_kind": collections.Counter(kind_of(r["doc_id"]) for r in rows),
        "by_batch": collections.Counter(r.get("review_batch") or "(표식 없음)" for r in rows),
        "by_origin": collections.Counter(
            ("실문서" if r.get("is_actual_document") else "합성") for r in rows),
        "grade_fixed": sum(1 for r in rows if r.get("grade_fixed")),
        "duplicate_groups": len(groups),
        "duplicate_rows": sum(len(g) for g in groups),
        "duplicate_shapes": shapes,
        "copies_of_trained_docs": copies_of_trained,
        "copies_not_in_block_list": copies_unblocked,
        "block_list_size": len(blocked),
        "manifest_found": bool(uses),
    }


# 문서 품질 기록 — 각 라운드 생성물에 문서마다 남아 있다(reports/ 는 git 밖).
#   pilot_docs_checked.jsonl  프로그램 검사 표식(flags)  ·  pilot_judge_rows.json  블라인드 판정자 2명의 S·V·M 판독
DOCGEN_ROUNDS = {"R1": "CLAUDE_DOCGEN_20260921", "R2": "CLAUDE_DOCGEN_R2_20260921", "R3": "CLAUDE_DOCGEN_R3_20260921",
                 "R4": "CLAUDE_DOCGEN_R4_20260921", "R5": "CLAUDE_DOCGEN_R5_20260921", "R6": "CLAUDE_DOCGEN_R6_20260921",
                 "R7": "CLAUDE_DOCGEN_R7_20260921", "R8": "CLAUDE_DOCGEN_R8_20260921", "R9": "CLAUDE_DOCGEN_R9_20260921"}


def _defects(rec: dict) -> list[str]:
    """문서 하나의 품질 결함. 별칭 의심(alias_code)은 "사내 Q&A"·"위험 R-"·"A4" 같은 오탐이 대부분이라 세지 않는다."""
    out = []
    if rec["judge_points"] < rec["judge_total"]:
        out.append("판정자 이견")             # 판정자 2명 중 누가 한 축이라도 명세와 다르게 읽음
    if "length_off" in rec["flags"]:
        out.append("길이 이탈")               # 본문 길이가 명세의 ±25% 밖
    if rec["writer"] == "haiku":
        out.append("haiku 작성")              # 6차 시험에서 haiku 문서만 라벨 재현율 14%(5/35)
    return out


def doc_quality(reports: Path | None = None) -> dict[str, dict]:
    """review_id(MD-####) → 품질 기록. 기록 파일이 없는 문서는 빠진다(좋다고 가정하지 않는다)."""
    if not REVIEW_MANIFEST.exists():
        return {}
    base = reports or POC / "reports"
    flags: dict[tuple, list] = {}
    judged: dict[tuple, dict] = {}
    for tag, folder in DOCGEN_ROUNDS.items():
        f_docs, f_judge = base / folder / "pilot_docs_checked.jsonl", base / folder / "pilot_judge_rows.json"
        if not (f_docs.exists() and f_judge.exists()):
            continue
        for line in f_docs.read_text(encoding="utf-8").splitlines():
            if line.strip():
                x = json.loads(line)
                flags[(tag, x["doc_key"])] = x.get("flags") or []
        for r in json.loads(f_judge.read_text(encoding="utf-8")):
            judged[(tag, r["doc_key"])] = r
    out: dict[str, dict] = {}
    for line in REVIEW_MANIFEST.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        m = json.loads(line)
        k = (m["round"], m["doc_key"])
        if k not in flags or k not in judged:
            continue
        j = judged[k]
        rec = {"round": m["round"], "writer": m.get("writer_model", ""), "flags": flags[k],
               "judge_points": sum(1 for ax in "SVM" for v in j[ax] if v == m[ax]),
               "judge_total": sum(len(j[ax]) for ax in "SVM")}
        rec["defects"] = _defects(rec)
        out[m["review_id"]] = rec
    return out


def review_order(rows: list[dict], quality: dict[str, dict] | None = None) -> dict[str, list[str]]:
    """전문가 검수를 요청할 문서 id 목록 — 검수자 배정 API(POST /golden/assignments)의 doc_ids 에 넣는다.

    목적은 검수 뒤 **재학습**이다. 그래서 학습에 쓴 문서도 빼지 않고 **문서 품질**로만 고른다
    (2026-09-25 결정). 이번 회차 문서 중 품질 결함이 없는 것을 요청하고, 결함이 있는 것은 제외로 뺀다.
    공개 실문서는 작성 모델·판정자 기준을 적용할 수 없는 별개 자료라 따로 묶고, **요청하지 않는다** — 재학습 목적으로는
    75/79건이 512토큰을 넘어 모델은 앞부분만 읽는데 검수자는 전체를 읽어야 한다(2026-09-25 실측). 다만 제안 등급이
    S1·S2 인 13건은 감리가 "공개문서는 S3"로 지적한 것이라 라벨을 사람이 확정해야 하므로 따로 묶어 둔다.
    FD 사본과 옛 합성 후보는 넣지 않는다(사본 · 등급 고정 문구로 맞춰지는 코퍼스).

    ⚠ 검수를 마친 문서 중 학습에 안 쓴 것(개발·봉인 543건)은 재학습에 넣지 않고 골든셋으로 남긴다
      (9/20 협의: 골든셋은 해당 모델 학습에 안 쓴 데이터).
    ⚠ 내부용이다. 이 파일 자체를 검수자에게 주지 않는다.
    """
    q = doc_quality() if quality is None else quality
    out: dict[str, list[str]] = {"1_요청(품질 통과)": [], "2_제외(품질 결함)": [],
                                 "3_공개 실문서 S1·S2 제안(선택)": [], "4_공개 실문서 S3 제안(요청 안 함)": [],
                                 "9_품질 기록 없음": []}
    for r in rows:
        d = r["doc_id"]
        if r.get("review_batch") == DELIVERED_BATCH:
            rec = q.get(d)
            if rec is None:
                out["9_품질 기록 없음"].append(d)
            elif rec["defects"]:
                out["2_제외(품질 결함)"].append(d)
            else:
                out["1_요청(품질 통과)"].append(d)
        elif r.get("is_actual_document"):
            key = "3_공개 실문서 S1·S2 제안(선택)" if r.get("proposed_grade") in ("S1", "S2") else "4_공개 실문서 S3 제안(요청 안 함)"
            out[key].append(d)
    return {k: sorted(v) for k, v in out.items()}


EXCLUSIONS_FILE = POC / "evidence" / "review_request_exclusions.jsonl"


def load_exclusions(path: Path | None = None) -> set[str]:
    """검수 요청에서 뺀 문서 id 집합(evidence/review_request_exclusions.jsonl). 파일이 없으면 빈 집합."""
    p = path or EXCLUSIONS_FILE
    out: set[str] = set()
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                d = str(json.loads(line).get("doc_id") or "").strip()
            except json.JSONDecodeError:
                continue
            if d:
                out.add(d)
    return out


def write_exclusions(order: dict[str, list[str]], quality: dict[str, dict], path: Path | None = None) -> int:
    """검수 요청에서 뺀 문서(품질 결함)를 evidence/ 에 남긴다 — 번들 빌더가 이 목록을 읽어 그 문서를 안 싣는다.

    날짜 칸은 안 둔다(다시 돌릴 때마다 전 줄이 바뀌어 diff 가 시끄럽다 — 날짜는 git 이력이 안다).
    """
    rows = [{"doc_id": d, "reasons": quality[d]["defects"], "tool": "audit_golden_candidate_pool.py"}
            for d in order["2_제외(품질 결함)"]]
    text = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows)
    (path or EXCLUSIONS_FILE).write_bytes(text.encode("utf-8"))
    return len(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="사람이 읽는 표 대신 JSON")
    ap.add_argument("--write-exclusions", action="store_true",
                    help="검수 요청에서 뺀 문서(품질 결함)를 evidence/review_request_exclusions.jsonl 에 쓴다 "
                         "— 번들 빌더가 읽어 그 문서를 안 싣는다")
    ap.add_argument("--review-order", type=Path, metavar="파일",
                    help="검수를 요청할 문서 id 목록(품질 기준)을 JSON 으로 쓴다(내부용, 검수자에게 주지 않는다)")
    a = ap.parse_args()

    rows = load_rows()
    r = audit(rows)
    t = training_overlap(rows)
    if a.json:
        print(json.dumps({**{k: (dict(v) if isinstance(v, collections.Counter) else v)
                             for k, v in r.items()}, "training": t}, ensure_ascii=False, indent=2))
        return 0

    print(f"후보 원장 {r['rows']:,}행 · 고유 doc_id {r['unique_doc_ids']:,} · 고유 본문 {r['unique_texts']:,}"
          f"  (세는 곳: {ProxyGoldCandidateService().root})")
    print("\n[doc_id 꼴별]")
    for k, v in r["by_kind"].most_common():
        print(f"  {k:<12}{v:>6,}")
    print("\n[review_batch 별]")
    for k, v in r["by_batch"].most_common():
        print(f"  {k:<40}{v:>6,}")
    print("\n[출처] " + " · ".join(f"{k} {v:,}" for k, v in r["by_origin"].most_common())
          + f"   [등급 확정] {r['grade_fixed']:,}")
    print(f"\n[본문이 글자 그대로 같은 묶음] {r['duplicate_groups']:,}묶음 · 관련 행 {r['duplicate_rows']:,}")
    for k, v in r["duplicate_shapes"].most_common():
        print(f"  {k:<20}{v:>6,}묶음")
    if r["duplicate_groups"]:
        if r["manifest_found"]:
            print(f"\n[사본 위험] 학습에 쓴 문서의 사본 {r['copies_of_trained_docs']:,}건 중 "
                  f"평가정답 차단 목록(현재 {r['block_list_size']:,}건)에 없는 것 {r['copies_not_in_block_list']:,}건")
        else:
            print(f"\n[사본 위험] 이번 회차 내부 대장이 없어 못 셌다: {REVIEW_MANIFEST}")

    if t["delivered"]:
        print(f"\n[이번 회차 {t['delivered']:,}건 × 실제 학습·개발 파일 — 본문 sha256 대조]")
        for label, f in t["files"].items():
            if f["rows"] is None:
                print(f"  {label:<26}파일 없음")
            else:
                print(f"  {label:<26}파일 {f['rows']:>5,}행 · 겹침 {f['overlap']:>5,}   ({f['role']})")
        pct = 100 * t["used"] / t["delivered"]
        print(f"  → 학습·검증에 씀 {t['used']:,}건({pct:.1f}%) · 안 씀 {t['not_used']:,}건 · "
              f"지금 배포된 모델 학습셋과 겹침 {t['deployed_overlap']:,}건")
        if t["manifest_found"]:
            print(f"  대장 training_use 라벨과 파일 대조가 어긋난 문서 {t['label_mismatch']:,}건")

    quality = doc_quality()
    order = review_order(rows, quality)
    print("\n[전문가 검수 요청 대상 — 문서 품질 기준, 학습 여부는 안 본다]")
    for k, v in order.items():
        print(f"  {k:<20}{len(v):>6,}건")
    why = collections.Counter(x for d in order["2_제외(품질 결함)"] for x in quality[d]["defects"])
    if why:
        print("  제외 사유(겹침 있음): " + " · ".join(f"{k} {v:,}" for k, v in why.most_common()))
    if a.write_exclusions:
        n = write_exclusions(order, quality)
        print(f"  제외 목록 {n}건 → {EXCLUSIONS_FILE}  (번들 빌더가 읽는다)")
    if a.review_order:
        payload = {**order, "제외_사유": {d: quality[d]["defects"] for d in order["2_제외(품질 결함)"]}}
        a.review_order.write_bytes(json.dumps(payload, ensure_ascii=False, indent=1).encode("utf-8"))
        print(f"  문서 id 목록 → {a.review_order}  (내부용 — 검수자에게 주지 않는다)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
