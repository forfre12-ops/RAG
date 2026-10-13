#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""문서가 가리키는 것이 실제로 있는지 센다 — 파일 참조 · 재현 명령 · 링크 · 앵커.

왜 필요한가(2026-08-29). 발주처에 내는 회신 묶음은 "이 값은 이 파일 이 줄에서 나온다"를
근거로 삼는다. 받는 쪽이 그 파일을 실제로 열어 본다. 경로가 리네임·이동으로 어긋나 있으면
수치가 맞아도 문서 전체의 신뢰가 깎인다. 색인이 "각 문서에 재현 명령을 기재했다"고
적어 두었으므로 그 명령도 실재해야 한다.

수치 대조는 scripts/audit_doc_claims.py 가 한다. 이 도구는 **가리키는 대상의 실재**만 센다.

하는 일
  1. HTML 에서 소스 경로(...py:123 포함) · 재현 명령(python scripts/...) 을 걷는다
  2. 리포 기준으로 해결한다(poc/ 접두 · 리포 루트 둘 다 시도)
  3. 없는 것 · 줄 번호가 파일 길이를 넘는 것 · 깨진 링크와 앵커를 보고한다
  4. 마지막에 분모(검사한 개수)와 함께 총계를 낸다

사용:
    python scripts/audit_doc_refs.py --dir doc/result/KL_회신_2026-08-28
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import unquote

_ROOT = Path(__file__).resolve().parent.parent          # poc/
_REPO = _ROOT.parent

DEFAULT_DIRS = ['doc/result/KL_회신_2026-08-28']

# 소스 경로처럼 보이는 토큰. 확장자를 못 박아 산문 오검출을 줄인다.
RE_SRC = re.compile(r'(?<![\w/.-])((?:[\w.-]+/)*[\w.-]+\.(?:py|yaml|yml|sql|jsonl))(?::(\d+))?')
# 재현 명령 — 문서가 "이렇게 다시 돌려 보시라"고 적은 것.
RE_CMD = re.compile(r'(?:python|make)\s+([\w./-]+)')
RE_HREF = re.compile(r'href="([^"]+)"')
RE_ID = re.compile(r'\sid="([^"]+)"')

# 문서가 예시로 든 것이라 리포에 없어도 정상인 이름.
IGNORE = {'requirements.txt'}
# 명령이 **만들어 내는** 경로는 없는 것이 정상이다(--out 뒤 인자 등).
RE_OUTARG = re.compile(r'--(?:out|output|o)\s+\S+')


def strip_html(raw: str) -> str:
    raw = re.sub(r'(?is)<(style|script)[^>]*>.*?</\1>', ' ', raw)
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', raw))


# 문서는 패키지 안쪽을 'services/classify_service.py' 처럼 접두 없이 쓴다.
# 짧게 쓰는 것이 읽기 좋으므로 문서를 고치지 말고 검사기가 해결한다.
_BASES = (
    _ROOT, _REPO,
    _ROOT / 'src', _ROOT / 'src' / 'koipa',
    _ROOT / 'src' / 'koipa' / 'modules',
)
# 접미 일치로 찾을 때만 훑는 곳. 리포 전체 rglob 은 느리고 오검출이 난다.
# _REPO/'scripts' 포함 — 문서 생성기 일부(build_table_spec·table_spec_meta)는 리포
# 루트 scripts/ 에 있다. 여기를 안 보면 실재하는 파일을 '없는 파일'로 잡는다(2026-09-05).
_SEARCH = (_ROOT / 'src', _ROOT / 'scripts', _ROOT / 'tests', _ROOT / 'datasets',
           _REPO / 'scripts', _REPO / 'doc')
_index: dict[str, list[Path]] = {}


def _build_index() -> None:
    if _index:
        return
    for root in _SEARCH:
        if not root.exists():
            continue
        for p in root.rglob('*'):
            if p.is_file():
                _index.setdefault(p.name, []).append(p)


def resolve(rel: str) -> Path | None:
    """문서가 적은 상대 경로를 리포 안에서 찾는다. 접두 생략이 흔하다."""
    rel = rel.replace('\\', '/')
    for base in _BASES:
        p = base / rel
        if p.exists():
            return p
    # 접미 일치 — 'services/classify_service.py' 가 src/koipa/services/... 로 풀리는 경우
    _build_index()
    for cand in _index.get(rel.rsplit('/', 1)[-1], []):
        if cand.as_posix().endswith('/' + rel):
            return cand
    return None


def audit(doc_dir: Path) -> tuple[int, list[str]]:
    problems: list[str] = []
    checked = 0

    htmls = sorted(doc_dir.rglob('*.html'))
    ids: dict[Path, set[str]] = {}
    for p in htmls:
        ids[p] = set(RE_ID.findall(p.read_text(encoding='utf-8')))

    for p in htmls:
        raw = p.read_text(encoding='utf-8')
        text = strip_html(raw)
        # 산출물 경로는 검사 대상이 아니다 — 명령이 그것을 만든다.
        text = RE_OUTARG.sub(' ', text)
        rel_doc = p.relative_to(doc_dir)

        # ① 소스 경로 참조
        for m in RE_SRC.finditer(text):
            ref, line = m.group(1), m.group(2)
            if ref in IGNORE:
                continue
            checked += 1
            target = resolve(ref)
            if target is None:
                problems.append(f'[없는 파일] {rel_doc} → {ref}')
            elif line:
                n = len(target.read_text(encoding='utf-8', errors='replace').splitlines())
                if int(line) > n:
                    problems.append(f'[줄 번호 초과] {rel_doc} → {ref}:{line} (파일 {n}줄)')

        # ② 재현 명령
        for m in RE_CMD.finditer(text):
            arg = m.group(1)
            if not arg.endswith('.py'):
                continue
            checked += 1
            if resolve(arg) is None:
                problems.append(f'[없는 명령] {rel_doc} → {m.group(0)}')

        # ③ 링크·앵커
        for href in RE_HREF.findall(raw):
            if href.startswith(('http://', 'https://', 'mailto:')):
                problems.append(f'[외부 링크] {rel_doc} → {href}')
                continue
            # '#top' 은 HTML 표준이 문서 맨 위로 정의한 값이라 대상 요소가 없어도 된다.
            # '${...}' 는 스크립트가 만드는 문자열이지 링크가 아니다.
            if href in ('#', '#top') or '${' in href:
                continue
            checked += 1
            path_part, _, frag = href.partition('#')
            target_doc = p if not path_part else (p.parent / unquote(path_part)).resolve()
            if path_part and not target_doc.exists():
                problems.append(f'[깨진 링크] {rel_doc} → {href}')
                continue
            if frag and target_doc in ids and frag not in ids[target_doc]:
                problems.append(f'[없는 앵커] {rel_doc} → {href}')

    return checked, problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='문서 참조 실재 검사 — 분모와 함께 센다')
    ap.add_argument('--dir', dest='dirs', action='append', default=None)
    args = ap.parse_args(argv)

    total_checked, total_problems = 0, []
    for d in (args.dirs or DEFAULT_DIRS):
        base = _REPO / d
        if not base.exists():
            print(f'  (없는 폴더: {d})')
            continue
        n, probs = audit(base)
        total_checked += n
        total_problems += probs

    print('=' * 76)
    print(' 문서 참조 실재 검사')
    print('=' * 76)
    for line in total_problems:
        print(' ', line)
    print()
    print(f'  검사한 참조 {total_checked}개 · 문제 {len(total_problems)}건')
    return 1 if total_problems else 0


if __name__ == '__main__':
    sys.exit(main())
