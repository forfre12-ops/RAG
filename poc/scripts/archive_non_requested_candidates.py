# -*- coding: utf-8 -*-
"""골든 후보 원장에서 '검수 요청 대상이 아닌 후보'와 그 후보에 딸린 파일을 보관 폴더로 옮긴다 — 삭제가 아니라 이동이다.

왜 필요한가(2026-09-25). 콘솔 후보 원장이 3,598건인데 지재원에 검수를 요청하는 것은 1,711건이다
(이번 검수 배치 1,731건에서 품질 결함 20건을 뺀 것). 나머지 1,887건은
    품질 결함 20건 · FD 사본 800건(요청 대상과 본문이 같다) · 옛 합성 후보 988건 · 옛 공개 실문서 79건
이라 화면·집계·번들에 섞이면 "검수 후보가 왜 3,598건이냐"가 되풀이된다. 이동은 2026-09-25 에 승인됐다.

무엇을 옮기나: 그 후보의 메타(.metadata.json) · 본문(.md) · 개정본(revisions/) · 업로드 원본(uploaded_originals/) ·
보기 파일(_view.vN.html). 파일이 어느 후보 것인지는 **파일명이 후보 doc_id 로 시작하고 바로 뒤가 `.` 또는 `_` 인지**로 정한다
(접두 일치 오류 방지 — MD-0001 은 MD-00010 의 것이 아니다). 두 후보에 걸리면 옮기지 않고 멈춘다.

무엇을 남기나: 요청 대상 1,711건의 파일 전부 · 후보에 속하지 않는 파일(결정 원장 candidate_decisions.jsonl · 검수자 별칭 솔트
reviewer_alias.salt · 목록·미리보기 HTML 등). 원장(candidate_decisions.jsonl)은 안 건드린다(append-only) — 옮긴 후보의 결정
사건은 화면이 후보 파일을 기준으로 그리므로 무시된다(ProxyGoldCandidateService._load_candidates).

보관 폴더에 상대 경로 그대로 옮기고 MANIFEST.json 에 후보별 묶음 · 파일 · sha256 · 원장 사건 수 · 복원법을 남긴다.

멈추는 조건(하나라도 있으면 아무것도 안 옮긴다)
  · 요청 대상이 0건이거나 --expect-keep / --expect-move 와 다르다
  · 옮길 후보를 사람이 손댔다(등급 확정이거나 상태가 proposed·under_review 가 아니다)
  · 파일이 두 후보에 걸린다 · 메타 파일을 못 찾았다 · doc_id 가 중복이거나 메타를 못 읽는다

요청 대상 = 검수 배치(expert_review_1731_20260924) − evidence/review_request_exclusions.jsonl (지재원행 번들 빌더와 같은 규칙).

사용
    python -X utf8 scripts/archive_non_requested_candidates.py --expect-keep 1711 --expect-move 1887   # 검증만(기본)
    python -X utf8 scripts/archive_non_requested_candidates.py --expect-keep 1711 --expect-move 1887 --apply
    python -X utf8 scripts/archive_non_requested_candidates.py --restore <보관 폴더>                    # 되돌린다
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import shutil
import sys
from pathlib import Path

POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(POC / "src"))
sys.path.insert(0, str(POC / "scripts"))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

from audit_golden_candidate_pool import DELIVERED_BATCH, kind_of, load_exclusions, load_rows  # noqa: E402
from koipa.services import proxy_gold_candidate_service as pgs  # noqa: E402
from koipa.services.proxy_gold_candidate_service import ProxyGoldCandidateService  # noqa: E402

ROOT = ProxyGoldCandidateService().root
DEFAULT_ARCHIVE = ROOT.parent / "single_document_candidates_archive_20260925_not_requested"
MANIFEST = "MANIFEST.json"
LEDGER = "candidate_decisions.jsonl"
UNTOUCHED_STATUS = {"proposed", "under_review"}    # 이 밖의 상태는 사람이 손댄 것이다


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def owners_of(name: str, ids: set[str]) -> list[str]:
    """파일명이 어느 후보 doc_id 의 것인가 — doc_id 로 시작하고 바로 뒤가 '.' 또는 '_' (또는 이름 전체가 doc_id)."""
    hits = {name[:i] for i, ch in enumerate(name) if ch in "._" and name[:i] in ids}
    if name in ids:
        hits.add(name)
    return sorted(hits)


def _group(doc_id: str, row: dict | None, excluded: set[str]) -> str:
    if row is None:
        return "본문 없는 메타"
    if row.get("review_batch") == DELIVERED_BATCH and doc_id in excluded:
        return "품질 결함(검수 요청 제외)"
    if kind_of(doc_id) == "FD":
        return "FD 사본"
    if row.get("is_actual_document"):
        return "옛 후보 · 공개 실문서"
    return "옛 후보 · 합성"


def _ledger_events(root: Path) -> collections.Counter:
    out: collections.Counter = collections.Counter()
    p = root / LEDGER
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    out[str(json.loads(line).get("doc_id") or "")] += 1
                except json.JSONDecodeError:
                    continue
    return out


def plan(rows: list[dict], root: Path | None = None) -> dict:
    root = root or ROOT
    by_id = {r["doc_id"]: r for r in rows}
    problems: list[str] = []
    meta_of: dict[str, str] = {}
    for m in sorted(root.glob("*.metadata.json")):
        try:
            d = str(json.loads(m.read_text(encoding="utf-8")).get("doc_id") or "")
        except (OSError, json.JSONDecodeError):
            problems.append(f"메타를 읽지 못했다: {m.name}")
            continue
        if not d:
            problems.append(f"메타에 doc_id 가 없다: {m.name}")
        elif d in meta_of:
            problems.append(f"doc_id 가 중복이다: {d}")
        else:
            meta_of[d] = m.name
    ids = set(meta_of)
    excluded = load_exclusions()
    keep = {d for d in ids if (by_id.get(d) or {}).get("review_batch") == DELIVERED_BATCH and d not in excluded}
    if not keep:
        problems.append("요청 대상이 0건이다 — 기준이 어긋났다")

    files_of: dict[str, list[str]] = collections.defaultdict(list)
    protected: list[str] = []
    for f in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = f.relative_to(root).as_posix()
        own = owners_of(f.name, ids)
        if len(own) > 1:
            problems.append(f"파일이 두 후보에 걸린다: {rel} ← {own}")
        elif not own:
            protected.append(rel)
        elif own[0] not in keep:
            files_of[own[0]].append(rel)

    events = _ledger_events(root)
    moves = []
    for d in sorted(ids - keep):
        r = by_id.get(d)
        if r and (r.get("grade_fixed") or r.get("status") not in UNTOUCHED_STATUS):
            problems.append(f"{d}: 사람이 손댔다(status={r.get('status')})")
        if meta_of[d] not in files_of[d]:
            problems.append(f"{d}: 메타 파일을 못 찾았다")
        moves.append({"doc_id": d, "group": _group(d, r, excluded), "files": sorted(files_of[d]),
                      "ledger_events": events.get(d, 0)})
    return {"keep": keep, "moves": moves, "protected": protected, "problems": problems}


def apply(pl: dict, archive: Path, root: Path | None = None) -> None:
    root = root or ROOT
    archive.mkdir(parents=True, exist_ok=False)          # 이미 있으면 멈춘다 — 남의 보관분을 덮지 않는다
    for mv in pl["moves"]:
        mv["file_sha256"] = {rel: sha256_file(root / rel) for rel in mv["files"]}
    groups = collections.Counter(mv["group"] for mv in pl["moves"])
    manifest = {
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "tool": "scripts/archive_non_requested_candidates.py",
        "source_dir": str(root),
        "reason": ("전문가 검수 요청 대상이 아닌 후보 — 요청 대상은 검수 배치(%s)에서 품질 결함 "
                   "(evidence/review_request_exclusions.jsonl)을 뺀 것이다. 삭제가 아니라 이동이다." % DELIVERED_BATCH),
        "restore": "python -X utf8 scripts/archive_non_requested_candidates.py --restore <이 폴더>",
        "ledger_note": f"{LEDGER} 는 그대로다. 옮긴 후보의 결정 사건은 화면이 후보 파일 기준이라 무시된다.",
        "keep_count": len(pl["keep"]),
        "move_count": len(pl["moves"]),
        "by_group": dict(groups),
        "file_count": sum(len(mv["files"]) for mv in pl["moves"]),
        "left_in_place_not_candidates": pl["protected"],
        "moves": pl["moves"],
    }
    (archive / MANIFEST).write_bytes(json.dumps(manifest, ensure_ascii=False, indent=1).encode("utf-8"))
    for mv in pl["moves"]:
        for rel in mv["files"]:
            dst = archive / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(root / rel), str(dst))
    bad = [rel for mv in pl["moves"] for rel in mv["files"]
           if (root / rel).exists() or sha256_file(archive / rel) != mv["file_sha256"][rel]]
    if bad:
        raise SystemExit(f"⛔ 옮긴 뒤 검증 실패 {len(bad)}개 — 예: {bad[:3]}. --restore 로 되돌릴 것")


def restore(archive: Path, root: Path | None = None) -> int:
    root = root or ROOT
    man = json.loads((archive / MANIFEST).read_text(encoding="utf-8"))
    put = 0
    for mv in man["moves"]:
        for rel in mv["files"]:
            src, dst = archive / rel, root / rel
            if dst.exists():
                print(f"건너뜀 — 이미 있다: {rel}")
                continue
            if sha256_file(src) != mv["file_sha256"][rel]:
                raise SystemExit(f"⛔ 보관분이 바뀌었다(sha256 불일치): {rel}")
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            put += 1
    print(f"복원 {put}개 파일 → {root}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="실제로 옮긴다(기본은 검증만)")
    ap.add_argument("--archive-dir", type=Path, default=DEFAULT_ARCHIVE)
    ap.add_argument("--restore", type=Path, metavar="보관폴더", help="MANIFEST.json 대로 되돌린다")
    ap.add_argument("--expect-keep", type=int, help="요청 대상(남길 후보)이 정확히 이 건수여야 한다")
    ap.add_argument("--expect-move", type=int, help="옮길 후보가 정확히 이 건수여야 한다")
    a = ap.parse_args()

    if a.restore:
        return restore(a.restore)

    rows = load_rows()
    pl = plan(rows)
    problems = list(pl["problems"])
    if a.expect_keep is not None and len(pl["keep"]) != a.expect_keep:
        problems.append(f"남길 후보가 {len(pl['keep'])}건이다(기대 {a.expect_keep})")
    if a.expect_move is not None and len(pl["moves"]) != a.expect_move:
        problems.append(f"옮길 후보가 {len(pl['moves'])}건이다(기대 {a.expect_move})")

    n_files = sum(len(mv["files"]) for mv in pl["moves"])
    print(f"후보 원장 {len(rows):,}건 → 남길 후보(요청 대상) {len(pl['keep']):,}건 · 옮길 후보 {len(pl['moves']):,}건"
          f"(파일 {n_files:,}개) · 후보가 아니라 그대로 두는 파일 {len(pl['protected']):,}개")
    for g, n in collections.Counter(mv["group"] for mv in pl["moves"]).most_common():
        print(f"  {g:<26}{n:>6,}건")
    with_events = [mv["doc_id"] for mv in pl["moves"] if mv["ledger_events"]]
    if with_events:
        print(f"  원장에 사건이 있는 옮길 후보 {len(with_events)}건(원장은 안 건드린다): {with_events[:5]}")
    for pr in problems[:10]:
        print("  ⛔", pr)
    if problems:
        print(f"⛔ 멈춘다 — 문제 {len(problems)}건. 아무것도 안 옮겼다.")
        return 1
    if not a.apply:
        print(f"\n검증만 했다. 실제로 옮기려면 --apply  (보관 위치: {a.archive_dir})")
        return 0

    apply(pl, a.archive_dir)
    pgs._CANDIDATE_CACHE.clear()                          # 이 프로세스의 캐시가 옛 목록을 들고 있지 않게
    after = load_rows()
    ok = {r["doc_id"] for r in after} == pl["keep"]
    print(f"\n옮김 완료 — 보관 {a.archive_dir}\n원장 {len(rows):,} → {len(after):,}건 · 요청 대상과 정확히 일치: {ok}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
