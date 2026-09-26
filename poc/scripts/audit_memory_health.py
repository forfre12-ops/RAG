#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""메모리(에이전트 장기기억) 건강 검사 — 색인·링크·frontmatter·죽은 참조를 센다.

왜 있나
    2026-09-14 에 "메모리에 잘못된 정보가 올라가 있다"는 물음을 받고 158개를 전수
    조사했다. 그때 기억으로 몇 개 훑는 대신 세는 도구를 만들었고, 다음에 같은 질문을
    받으면 다시 세지 말고 이것을 다시 돌린다(CLAUDE.md §4).

무엇을 세나 (기계로 판정 가능한 것만)
    A 색인에 없는 파일 / B 색인이 가리키는데 없는 파일 / C 색인 중복
    D frontmatter 결함(name 불일치·description 없음)
    E 깨진 [[위키링크]]
    F 파일명 날짜보다 본문 최신 날짜가 뒤인 것(= 파일명이 낡음)
    G 메모리가 언급한 리포 경로가 git추적·디스크 두 방법 모두에서 없는 것

무엇을 못 세나 (사람이 읽어야 한다)
    본문 서술이 사실과 다른지. 그건 대상 파일·코드를 열어야 안다.
    이 도구는 "어디를 열어 볼지"를 좁혀 줄 뿐이다.

사용
    poc/.venv/Scripts/python.exe scripts/audit_memory_health.py
    poc/.venv/Scripts/python.exe scripts/audit_memory_health.py --memory-dir <경로> --repo <경로>
    (윈도 콘솔이 cp949 면 PYTHONIOENCODING=utf-8 를 함께 줄 것)
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections import Counter

try:  # 스크립트로 직접 실행 - scripts/ 가 sys.path 에 들어온다
    from _cli_io import force_utf8_stdio  # noqa: E402
except ImportError:  # 패키지로 import - 릴리스 번들의 import 폐쇄 검사가 이 경로다
    from scripts._cli_io import force_utf8_stdio  # noqa: E402

force_utf8_stdio()

DEFAULT_MEMORY = os.path.expanduser(
    r"~\.claude\projects\f--antigravity-rag\memory"
)
SKIP_DIRS = {".git", "node_modules", ".venv", ".venv-gpu", "__pycache__",
             ".mypy_cache", ".pytest_cache"}
PATH_RE = re.compile(
    r"(?<![\w/.-])((?:[\w.-]+/)*[\w.-]+\.(?:py|yml|yaml|json|sql|html|sh|toml|csv|jsonl))(?![\w])"
)
DATE_RE = re.compile(r"20\d\d-\d\d-\d\d")


def _load(memory_dir: str):
    files = sorted(
        f for f in os.listdir(memory_dir) if f.endswith(".md") and f != "MEMORY.md"
    )
    bodies = {
        f: open(os.path.join(memory_dir, f), encoding="utf-8").read() for f in files
    }
    index = open(os.path.join(memory_dir, "MEMORY.md"), encoding="utf-8").read()
    return files, bodies, index


def _repo_inventory(repo: str):
    out = subprocess.run(["git", "-C", repo, "ls-files", "-z"], capture_output=True)
    tracked = {p for p in out.stdout.decode("utf-8").split("\0") if p}
    disk: set[str] = set()
    disk_base: dict[str, list[str]] = {}
    for root, dirs, fs in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        rel = os.path.relpath(root, repo).replace(os.sep, "/")
        rel = "" if rel == "." else rel + "/"
        for f in fs:
            p = rel + f
            disk.add(p)
            disk_base.setdefault(f, []).append(p)
    return tracked, disk, disk_base


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--memory-dir", default=DEFAULT_MEMORY)
    ap.add_argument("--repo", default=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
    ap.add_argument("--today", default=None, help="F 검사의 상한 날짜(기본: 오늘)")
    args = ap.parse_args()

    if not os.path.isdir(args.memory_dir):
        print(f"메모리 폴더가 없습니다: {args.memory_dir}", file=sys.stderr)
        return 2

    files, bodies, index = _load(args.memory_dir)
    stems = {f[:-3] for f in files}
    linked = re.findall(r"\]\(([^)]+\.md)\)", index)
    fileset = set(files)

    findings = 0
    print(f"== 총계 ==\n메모리 {len(files)}개 · 색인 링크 {len(linked)}개(고유 {len(set(linked))})\n")

    orphans = sorted(fileset - set(linked))
    dead = sorted(set(linked) - fileset)
    dups = sorted(k for k, v in Counter(linked).items() if v > 1)
    for title, rows in (
        ("A. 색인에 없는 파일(고아)", orphans),
        ("B. 색인이 가리키는데 없는 파일", dead),
        ("C. 색인에 두 번 이상 나온 파일", dups),
    ):
        print(f"== {title} — {len(rows)}건 ==")
        for r in rows:
            print(f"   {r}")
        findings += len(rows)
        print()

    print("== D. frontmatter 결함 ==")
    bad = []
    for f in files:
        m = re.match(r"^---\n(.*?)\n---\n", bodies[f], re.S)
        if not m:
            bad.append((f, "frontmatter 없음"))
            continue
        fm = m.group(1)
        nm = re.search(r"^name:\s*(.+)$", fm, re.M)
        de = re.search(r"^description:\s*(.+)$", fm, re.M)
        if not nm or nm.group(1).strip().strip('"') != f[:-3]:
            bad.append((f, f"name 불일치/누락: {nm.group(1).strip() if nm else '-'}"))
        if not de:
            bad.append((f, "description 없음"))
    for f, why in bad:
        print(f"   {f}: {why}")
    print(f"   소계 {len(bad)}\n")
    findings += len(bad)

    print("== E. 깨진 [[위키링크]] ==")
    broken = [
        (f, w)
        for f in files
        for w in re.findall(r"\[\[([^\]]+)\]\]", bodies[f])
        if w not in stems
    ]
    for f, w in broken:
        print(f"   {f} -> [[{w}]]")
    print(f"   소계 {len(broken)}\n")
    findings += len(broken)

    today = args.today or __import__("datetime").date.today().isoformat()
    print("== F. 파일명 날짜보다 본문이 더 최신인 것(파일명이 낡음) ==")
    stale_name = []
    for f in files:
        m = re.search(r"(\d{4}-\d{2}-\d{2})\.md$", f)
        if not m:
            continue
        later = [d for d in set(DATE_RE.findall(bodies[f])) if m.group(1) < d <= today]
        if later:
            stale_name.append((f, m.group(1), max(later)))
    for f, fd, bd in stale_name:
        print(f"   {f}  파일명 {fd} → 본문 {bd}")
    print(f"   소계 {len(stale_name)}  (경고일 뿐 결함은 아니다 — 정정이 누적된 자연스러운 모습)\n")

    print("== G. 메모리가 언급한 리포 경로 대조(git추적 + 디스크 두 방법) ==")
    tracked, disk, disk_base = _repo_inventory(args.repo)
    gone = []
    total = 0
    for f in files:
        seen = set()
        for m in PATH_RE.finditer(bodies[f]):
            tok = m.group(1)
            if tok in seen:
                continue
            seen.add(tok)
            total += 1
            norm = tok.replace(chr(92), "/").lstrip("./")
            in_tracked = norm in tracked or any(p.endswith("/" + norm) for p in tracked)
            in_disk = norm in disk or any(p.endswith("/" + norm) for p in disk)
            base_ok = ("/" not in tok) and (os.path.basename(norm) in disk_base)
            if not (in_tracked or in_disk or base_ok):
                gone.append((f, tok))
    cur = None
    for f, tok in gone:
        if f != cur:
            print(f"   [{f}]")
            cur = f
        print(f"       X {tok}")
    print(f"   언급 {total}건 중 두 방법 모두 없음 {len(gone)}건")
    print("   ⚠ 여기 뜬다고 결함이 아니다 — reports/ 처럼 git 밖 산출물이거나 서술형 토막일 수 있다.")
    print("     '없다'고 말하기 전에 그 파일을 직접 찾아볼 것.\n")

    # ── H. 요약(description)이 본문 정정을 안 따라간 후보 ───────────────
    # 2026-09-14 전수조사에서 틀린 것의 대부분이 이 모양이었다:
    #   본문에는 "✅ 완료/해소" 가 적혀 있는데 description 은 "미배포·없음·안 됨" 그대로.
    print("== H. 요약이 본문 정정을 안 따라간 후보 ==")
    NEG = ["미배포", "미반영", "미재생성", "미완", "미착수", "안 됨", "안됨", "없다", "없음",
           "불가", "끊겨", "막혀", "0건", "드리프트"]
    POS = ["✅", "해소됨", "해소 (", "완료했다", "전환 완료", "배포 완료", "고쳤다", "닫았다",
           "정정", "이제", "더 이상"]
    susp = []
    for f in files:
        m = re.search(r"^description:\s*(.+)$", bodies[f], re.M)
        if not m:
            continue
        desc = m.group(1)
        body = bodies[f][m.end():]
        neg = [w for w in NEG if w in desc]
        if not neg:
            continue
        pos = [w for w in POS if w in body]
        if pos:
            susp.append((f, neg[:3], pos[:3]))
    for f, neg, pos in susp:
        print(f"   {f}\n       요약에 {neg}  ↔  본문에 {pos}")
    print(f"   소계 {len(susp)}")
    print("   ⚠ 후보일 뿐이다 — 여는 것이 규율이지 전부 결함이라는 뜻이 아니다.")
    print("     정정을 적었으면 description·MEMORY.md 줄·**코드 블록**까지 같이 고칠 것.\n")

    # ── I. 복사용 명령 블록이 든 위험 ────────────────────────────────
    # 서술은 고치면서 복사용 명령은 그대로 두기 쉽다 — 주소·compose 파일이 그렇게 낡는다.
    print("== I. 코드 블록 안의 옛 서버 주소 / 죽은 파일 ==")
    # 211.233.204.32 는 2026-09-26 사용자 지시("211은 이제 안 쓸 거야")로 더했다 — 그 주소가 든 복사용 명령은 머리말에 「대상 아님」 표시가 있어야 한다.
    DEAD_HOSTS = ["223.130.156.134", "182.212.163.182", "211.233.204.32"]
    BANNER = ["대상 아님", "대상이 아니다", "그대로 돌리지 말", "옛 서버", "이력"]
    risky = []
    for f in files:
        blocks = re.findall(r"```.*?```", bodies[f], re.S) + re.findall(r"^(?: {4}|\t).+$", bodies[f], re.M)
        joined = "\n".join(blocks)
        hosts = sorted({h for h in DEAD_HOSTS if h in joined})
        if not hosts:
            continue
        head = bodies[f][:1600]
        if any(b in head for b in BANNER):
            continue
        risky.append((f, hosts))
    for f, hosts in risky:
        print(f"   {f}  :: {', '.join(hosts)}  (머리말에 '대상 아님' 표시 없음)")
    print(f"   소계 {len(risky)}\n")
    findings += len(risky)

    print("== 요약 ==")
    print(f"구조 결함(A~E,I) {findings}건 · 낡은 파일명(F) {len(stale_name)}건 "
          f"· 죽은 경로 후보(G) {len(gone)}건 · 요약↔본문 어긋남 후보(H) {len(susp)}건")
    if findings:
        print("\n⛔ 구조 결함이 있습니다. 고친 뒤 다시 돌리세요.")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
