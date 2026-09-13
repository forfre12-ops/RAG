#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""코드가 참조하는 데이터셋을 전수로 세어 한 표로 낸다 — 평가면 총계를 손으로 고르지 않는다.

## 왜 만들었나 (2026-09-14)

`audit_eval_ground_truth.py` 는 평가면 4개를 **파일 머리에 손으로 적어** 두고 그것만 본다.
그래서 "우리 평가면이 몇 개냐" 는 물음에 늘 다른 답이 나왔다.

    손으로 고른 목록         4개 (audit_eval_ground_truth.EVAL_SETS)
    또 다른 손 목록          4개 (regression_gate.EVAL_SETS — holdout109 가 **다른 파일**이다)
    한 세션이 세어 본 값     27개
    같은 기준을 다시 돌린 값  92경로 / 실재 69 / 평가성 38

같은 이름이 서로 다른 파일을 가리키는 것도 손 목록이라 안 보였다 —
`holdout109` 는 코드에서 **세 경로**를 가리키고, 그중 두 파일은 본문이 109/109 같은데
**라벨이 24건 다르다**. 두 도구가 낸 "holdout109 정확도" 는 같은 축의 값이 아니었다.

CLAUDE.md 제4조: 범위를 묻는 질문에는 **세는 도구를 먼저 만들어 돌린다.**
그래서 손 목록을 지우고 코드에서 뽑는다. 다음에 같은 질문을 받으면 다시 세지 않고 다시 돌린다.

## 무엇을 세는가

    1) 코드가 참조하는 데이터셋 경로     AST 문자열 상수 + 줄단위 정규식 (둘을 합집합)
    2) 그 경로의 실재 여부                없으면 '기준선' 으로 남긴다(이동·정리 후 비교용)
    3) 실재 파일의 행수·정답 등급 분포    audit_eval_ground_truth.tier_of() 를 그대로 쓴다
    4) 역할                               참조한 코드의 성격으로 정한다(채점·학습·제작·시험·런타임)
    5) 이름 충돌                          한 별칭이 여러 경로를 가리키는가

⚠ 이 도구는 **분모를 반드시 찍는다.** "평가면 N개" 만 적고 "안 본 것 M개" 를 안 적으면
   그 수치는 손으로 고른 것과 다르지 않다. 미실재·미판정도 표에 남긴다.

사용:

    PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe scripts/audit_eval_inventory.py
    ... --json reports/EVAL_INVENTORY.json
    ... --role eval          # 채점에 쓰이는 것만
    ... --conflicts-only     # 이름 충돌만
"""
from __future__ import annotations

import argparse
import ast
import collections
import json
import re
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

POC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(POC / "scripts"))

from audit_eval_ground_truth import TIER_ORDER, tier_of  # noqa: E402

# 참조를 찾을 곳. 여기 없는 곳에서 오는 경로는 못 센다 — 아래 '못 센 것' 에 적는다.
SCAN_DIRS = ("scripts", "src", "tests", "deploy", "infra")
SCAN_GLOBS = ("Makefile", "*.yml", "*.yaml", "*.sh", "*.toml")
SKIP_PARTS = (".venv", ".venv-gpu", "__pycache__", "node_modules", ".git")

# datasets/ 로 시작하는 경로 조각. 확장자가 없으면 디렉터리로 본다.
PATH_RE = re.compile(r"datasets/[A-Za-z0-9_./@+-]+")

# 경로 바로 앞의 CLI 플래그. 누출 판정은 **파일 이름이 아니라 이 플래그**로 한다.
# (2026-09-14 첫 판에서 파일명 규칙으로 판정했더니 p1_train_classifier.py 가
#  classification_gold.jsonl 을 '평가' 로 읽는 자리를 학습 입력으로 오탐했다.)
FLAG_RE = re.compile(r"(--[a-z][a-z0-9-]*)[ =]+[\"']?(datasets/[A-Za-z0-9_./@+-]+)")
TRAIN_INPUT_FLAGS = ("--train-path", "--val-path", "--train", "--val", "--train-file")
EVAL_INPUT_FLAGS = ("--test-path", "--eval", "--eval-path", "--gold", "--test", "--holdout")

# 참조한 코드의 성격 -> 역할. 위에서부터 먼저 맞는 것을 쓴다.
ROLE_RULES = (
    ("runtime", ("src/koipa/",)),
    ("eval", (
        "regression_gate.py", "audit_eval_ground_truth.py", "audit_dataset_leakage.py",
        "eval_", "evaluate_", "score_", "measure_", "compare_", "report_", "check_",
    )),
    ("train", ("p1_train_", "train_", "calibrate_", "retrain")),
    ("build", (
        "build_", "assemble_", "gen_", "make_", "collect_", "harden_", "depollute_",
        "promote_", "export_", "regate_",
    )),
    ("test", ("tests/",)),
    ("ci", ("Makefile", ".yml", ".yaml", ".sh")),
)

ROLE_ORDER = ("eval", "train", "runtime", "build", "test", "ci", "other")

# 가장 낮은 등급이 그 셋의 주장 한계다 — audit_eval_ground_truth 와 같은 규칙
WORST_FIRST = ("CIRCULAR", "UNKNOWN", "NONE", "BRONZE", "SILVER", "GOLD")


def _iter_source_files() -> list[Path]:
    out: list[Path] = []
    for d in SCAN_DIRS:
        base = POC / d
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if not p.is_file() or any(s in p.parts for s in SKIP_PARTS):
                continue
            if p.suffix in (".py", ".yml", ".yaml", ".sh", ".toml") or p.name == "Makefile":
                out.append(p)
    for g in SCAN_GLOBS:
        for p in POC.glob(g):
            if p.is_file():
                out.append(p)
    return sorted(set(out))


def _role_of(rel: str) -> str:
    for role, needles in ROLE_RULES:
        if any(n in rel for n in needles):
            return role
    return "other"


def collect_refs() -> tuple[dict[str, list[tuple[str, int]]], dict]:
    """코드에서 datasets/ 경로를 뽑는다. AST 상수와 줄단위 정규식의 합집합."""
    refs: dict[str, list[tuple[str, int]]] = collections.defaultdict(list)
    flags: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    files = _iter_source_files()
    ast_fail: list[str] = []
    for p in files:
        rel = p.relative_to(POC).as_posix()
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # (a) 줄단위 — 주석·f-string 조각·yaml 값까지 잡는다
        for i, line in enumerate(text.splitlines(), 1):
            for m in PATH_RE.finditer(line):
                refs[m.group(0).rstrip("/.")].append((rel, i))
            # 경로가 어느 CLI 플래그로 넘어가는지 — 누출 판정의 유일한 근거
            for fm in FLAG_RE.finditer(line):
                flags[fm.group(2).rstrip("/.")][fm.group(1)] += 1
        # (b) AST 상수 — 줄바꿈으로 쪼개진 문자열을 잡는다
        if p.suffix == ".py":
            try:
                tree = ast.parse(text)
            except SyntaxError:
                ast_fail.append(rel)
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    for m in PATH_RE.finditer(node.value):
                        key = m.group(0).rstrip("/.")
                        where = (rel, getattr(node, "lineno", 0))
                        if where not in refs[key]:
                            refs[key].append(where)
    meta = {
        "scanned_files": len(files),
        "ast_parse_failed": ast_fail,
        "distinct_paths": len(refs),
        "flags": {k: dict(v) for k, v in flags.items()},
    }
    return dict(refs), meta


def probe(path: str) -> dict:
    """실재 여부와 내용을 본다. 없으면 그 사실만 남긴다."""
    p = POC / path
    d: dict = {"path": path, "exists": p.exists()}
    if not p.exists():
        return d
    if p.is_dir():
        d["kind"] = "dir"
        d["jsonl"] = sorted(x.name for x in p.glob("*.jsonl"))
        return d
    d["kind"] = "file"
    if p.suffix != ".jsonl":
        d["bytes"] = p.stat().st_size
        return d
    tiers: collections.Counter[str] = collections.Counter()
    labels: collections.Counter[str] = collections.Counter()
    keys: set[str] = set()
    n = 0
    with p.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            n += 1
            tiers[tier_of(row)[0]] += 1
            for cand in ("label", "grade", "target", "gold", "y"):
                if row.get(cand):
                    labels[str(row[cand])] += 1
                    break
            if n <= 200:
                keys.update(row.keys())
    d.update(
        n=n,
        tiers={k: tiers[k] for k in TIER_ORDER if tiers[k]},
        labels=dict(labels.most_common()),
        claim_ceiling=next((k for k in WORST_FIRST if tiers[k]), "NONE"),
        has_text_hash=bool(keys & {"text_sha256", "document_sha256", "content_sha256"}),
        has_evidence=bool(keys & {"evidence_spans", "evidence_card"}),
    )
    return d


def alias_conflicts(rows: list[dict]) -> dict[str, list[str]]:
    """한 별칭이 여러 경로를 가리키는가. 별칭 = 파일명에서 판 표시를 뗀 것."""
    by_alias: dict[str, set[str]] = collections.defaultdict(set)
    for r in rows:
        if r.get("kind") != "file" or not r["path"].endswith(".jsonl"):
            continue
        alias = Path(r["path"]).stem
        alias = re.sub(r"\.(clean|hardened|consensus|locked|corrected|v\d+)$", "", alias)
        alias = re.sub(r"_(clean|hardened|corrected|provenance_corrected)$", "", alias)
        by_alias[alias].add(r["path"])
    return {a: sorted(ps) for a, ps in sorted(by_alias.items()) if len(ps) > 1}


def build(refs: dict[str, list[tuple[str, int]]], flags: dict[str, dict]) -> list[dict]:
    rows: list[dict] = []
    for path, where in sorted(refs.items()):
        roles = collections.Counter(_role_of(f) for f, _ in where)
        d = probe(path)
        d["refs"] = len(where)
        d["ref_files"] = len({f for f, _ in where})
        d["roles"] = {r: roles[r] for r in ROLE_ORDER if roles[r]}
        d["role"] = next((r for r in ROLE_ORDER if roles[r]), "other")
        fl = flags.get(path, {})
        d["cli_flags"] = fl
        # 누출 후보 = 같은 파일이 **학습 입력**으로도 **채점 입력**으로도 넘어간다.
        # 파일 이름이 아니라 플래그로 본다(파일명 규칙은 오탐을 낸다 — FLAG_RE 주석 참조).
        d["as_train_input"] = sorted(f for f in fl if f in TRAIN_INPUT_FLAGS)
        d["as_eval_input"] = sorted(f for f in fl if f in EVAL_INPUT_FLAGS)
        d["leak_candidate"] = bool(d["as_train_input"] and d["as_eval_input"])
        rows.append(d)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--json", default=None, help="결과를 JSON 으로 저장")
    ap.add_argument("--role", default=None, choices=ROLE_ORDER, help="이 역할만 본다")
    ap.add_argument("--conflicts-only", action="store_true", help="이름 충돌만 본다")
    args = ap.parse_args()

    refs, meta = collect_refs()
    rows = build(refs, meta.get("flags", {}))
    conflicts = alias_conflicts(rows)

    on_disk = sorted(p.relative_to(POC).as_posix() for p in (POC / "datasets").rglob("*.jsonl"))
    referenced = {r["path"] for r in rows}
    unreferenced = [p for p in on_disk if p not in referenced]

    if args.conflicts_only:
        print("=" * 78)
        print(f"한 이름이 여러 파일을 가리키는 자리 — {len(conflicts)}건")
        print("=" * 78)
        for alias, paths in conflicts.items():
            print(f"\n  [{alias}]")
            for p in paths:
                d = next((r for r in rows if r["path"] == p), {})
                print(f"      {p}  n={d.get('n', '?')}  {d.get('labels', {})}")
        return 0

    shown = [r for r in rows if not args.role or r["role"] == args.role]
    missing = [r for r in rows if not r["exists"]]

    print("=" * 78)
    print("코드가 참조하는 데이터셋 — 전수")
    print("=" * 78)
    print(f"  검사한 소스 파일 {meta['scanned_files']}개 → 서로 다른 경로 {meta['distinct_paths']}개")
    print(f"  실재 {len(rows) - len(missing)}개 · 부재 {len(missing)}개")
    print(f"  디스크의 jsonl {len(on_disk)}개 중 코드가 안 보는 것 {len(unreferenced)}개")
    if meta["ast_parse_failed"]:
        print(f"  ⚠ AST 파싱 실패 {len(meta['ast_parse_failed'])}건: {meta['ast_parse_failed']}")

    for role in ROLE_ORDER:
        grp = [
            r for r in shown
            if r["role"] == role and r["exists"] and r.get("kind") == "file"
            and r["path"].endswith(".jsonl")
        ]
        if not grp:
            continue
        print(f"\n── {role} ({len(grp)}개) " + "─" * max(4, 56 - len(role)))
        for r in sorted(grp, key=lambda x: -(x.get("n") or 0)):
            flag = " ⚠학습·채점 동시입력" if r["leak_candidate"] else ""
            print(
                f"  {r['path']:<56} n={r.get('n', 0):>6}  "
                f"{r.get('claim_ceiling', '?'):<8} 참조 {r['refs']}곳/{r['ref_files']}파일{flag}"
            )

    if conflicts:
        print("\n" + "=" * 78)
        print(f"⚠ 한 이름이 여러 파일을 가리킨다 — {len(conflicts)}건")
        print("=" * 78)
        for alias, paths in conflicts.items():
            print(f"  [{alias}] {len(paths)}개")
            for p in paths:
                d = next((r for r in rows if r["path"] == p), {})
                print(f"      {p}  n={d.get('n', '?')}")

    if missing:
        print("\n" + "=" * 78)
        print(f"코드가 가리키는데 지금 없는 경로 — {len(missing)}개 (이동·정리 전 기준선)")
        print("=" * 78)
        for r in sorted(missing, key=lambda x: -x["refs"])[:40]:
            print(f"  {r['path']:<56} 참조 {r['refs']}곳/{r['ref_files']}파일 [{r['role']}]")
        if len(missing) > 40:
            print(f"  … 외 {len(missing) - 40}개 (전체는 --json)")

    print("\n" + "=" * 78)
    print("못 센 것 — 이 도구의 한계")
    print("=" * 78)
    print("  · 변수로 조립되는 경로(폴더명만 문자열로 쓰는 자리)는 못 센다. 위 수치는 하한선이다.")
    print("  · 역할은 참조한 코드 이름으로 추정한 것이다. 한 파일이 여러 역할이면 첫 역할로 적었다.")
    print(f"  · 디스크의 jsonl {len(unreferenced)}개는 코드 참조가 없어 역할을 판정하지 않았다.")

    if args.json:
        out = POC / args.json
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(
                {
                    "meta": meta,
                    "rows": rows,
                    "alias_conflicts": conflicts,
                    "unreferenced_on_disk": unreferenced,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\n저장: {out.relative_to(POC)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
