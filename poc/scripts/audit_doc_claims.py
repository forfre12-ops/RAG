#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""문서에 적힌 수치가 서로 맞는지, 코드와 맞는지 대조한다.

왜 필요한가(2026-08-29). 같은 사실이 여러 문서에 흩어져 적힌다 - 표 개수 · 칼럼 수 ·
키워드 수 · 게이트 수 · 응답 필드 수. 한 곳을 고치면 나머지가 남는다. 실제로 놓쳤다.

  · 첨부를 5종에서 4종으로 줄이면서 §4 표만 고치고 머리말을 안 고쳤다
    (같은 페이지에 5 와 4 가 함께 있었다)
  · 칼럼 주석을 테이블 정의서는 0개, 회신서는 13개라고 적었다(실제 13개)
  · ICD 반영을 한쪽은 완료형, 다른 쪽은 미래형으로 적었다

문장 단위로 읽으면 걸리지 않는다. **같은 사실이 여러 곳에 흩어져 있을 때 모아서
대조**해야 잡힌다. 그래서 도구로 만든다.

하는 일
  1. 코드·DB 에서 참값을 뽑는다(표 수 · 칼럼 수 · 키워드 수 · 등급 수 ...)
  2. 문서에서 그 수치를 주장하는 문장을 정규식으로 걷는다
  3. 문서끼리 다른 값을 말하거나 참값과 어긋나면 보고한다

⚠ 이 도구는 판정하지 않는다. "다르다"는 것을 드러낼 뿐이고, 어느 쪽이 맞는지는
사람이 근거를 열어 정한다. 문맥상 정당하게 다른 값도 있다(예: 시점이 다른 스냅샷).

사용:
    python scripts/audit_doc_claims.py
    python scripts/audit_doc_claims.py --dir doc/result/KL_회신_2026-08-28
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent          # poc/
_REPO = _ROOT.parent

# 기본 점검 범위 — 발주처가 보는 것들. archive·releases 는 시점 스냅샷이라 제외한다.
DEFAULT_DIRS = [
    'doc/result/KL_회신_2026-08-28',
    'doc/result/KL_AI자료_2026-08',
    'doc/result/감리정본',
    'doc/감리문서',
]

# (이름, 정규식, 설명) — 문서가 그 수를 주장하는 자리를 찾는다.
CLAIMS = [
    # [2026-08-29] 옛 패턴은 '표'·'테이블' 뒤의 아무 두 자리나 잡아 날짜·판번호까지 걸렸다
    # (한 폴더에서 29종이 갈린 것처럼 보였고, 전부 오검출이었다). 수가 단위 앞에 오는
    # 실제 표기('21표'·'21개 테이블')만 잡는다.
    # 'ORM 19테이블' 은 부분 집계(나머지 2표는 alembic 원시 SQL)라 총계와 갈리는 것이
    # 정상이다. 절 번호('01 테이블 목록')는 0 으로 시작하므로 함께 걸러진다.
    ('표 개수',      r'(?<!ORM )([1-9]\d)\s*(?:개\s*)?(?:표|테이블)(?![가-힣])(?!\s*\+\s*RAG)', '전체 테이블 수'),
    ('ORM 매핑 표',  r'ORM\s*매핑\s*(\d+)', 'ORM 매핑 표 수'),
    ('칼럼 수',      r'(\d{3})\s*칼럼', '전체 칼럼 수'),
    ('키워드 수',    r'(?:키워드|시드)\s*(?:정확\s*)?(\d{3})\s*개', '룰 시드 키워드 수'),
    ('게이트 수',    r'게이트\s*(\d{1,2})\s*개', '검수 라우팅 게이트 수'),
    # 'Response 200 필드 타입 설명' 은 응답표 머리글이지 필드 수가 아니다.
    ('응답 필드 수', r'(?<!\d)(\d{2})\s*필드', 'API 응답 필드 수'),
    ('평가셋 건수',  r'(\d{3})\s*건\s*(?:실측|평가)', '판정식 실측 건수'),
]


# 문장 안에 끼는 태그. 이것만 공백으로 지운다 - "<b>21</b>표" 는 한 문장이다.
_INLINE = ("b", "i", "em", "strong", "code", "span", "a", "sub", "sup", "small", "u", "mark")


def strip_html(raw: str) -> str:
    """태그를 지우되 **칸·문단 경계는 남긴다.**

    [2026-08-29] 종전에는 태그를 전부 공백 하나로 바꿨다. 그래서 표의 한 칸이
    `/api/v1/healthz 200` 으로 끝나고 다음 칸이 `칼럼 10개 삭제` 로 시작하면
    `200 칼럼` 이 되어 "이 문서는 칼럼 수를 200 이라 주장한다" 는 오탐이 났다
    (「DB 대리키 표준화 전환계획」 실측). 경계에 | 를 세워 정규식이 못 넘게 한다.
    검사기가 거짓 경보를 내면 다음 사람이 진짜 불일치를 흘려 넘긴다.
    """
    raw = re.sub(r"(?is)<(style|script)[^>]*>.*?</\1>", " ", raw)
    inline = "|".join(_INLINE)
    raw = re.sub(rf"(?i)</?(?:{inline})(?:\s[^>]*)?/?>", " ", raw)   # 문장 안 태그 = 공백
    text = re.sub(r"<[^>]+>", " | ", raw)                            # 그 밖 = 칸 경계
    text = text.replace("&mdash;", "-").replace("&sect;", "#").replace("&nbsp;", " ")
    text = re.sub(r"\s+", " ", text)
    return re.sub(r"(?: \|)+ ", " | ", text)


def truth_from_code() -> dict:
    """코드·시드에서 뽑는 참값. DB 가 필요한 것은 넣지 않는다(오프라인 동작)."""
    out: dict[str, int] = {}
    models = (_ROOT / 'src' / 'koipa' / 'db' / 'models.py').read_text(encoding='utf-8')
    out['ORM 매핑 표'] = len(re.findall(r'__tablename__\s*=', models))
    out['ORM 칼럼'] = len(re.findall(r'^\s+[a-z_]+\s*:.*mapped_column', models, re.M))
    try:
        sys.path.insert(0, str(_ROOT / 'src'))
        from koipa.modules.m3_labeling.seeds import KEYWORD_SEEDS  # noqa: PLC0415
        out['키워드 수'] = len(KEYWORD_SEEDS)
    except Exception as exc:  # noqa: BLE001
        print(f'  (키워드 수 산출 실패: {type(exc).__name__})')
    spec = _REPO / 'doc' / '03_openapi_koipa_kl.yaml'
    if spec.exists():
        s = spec.read_text(encoding='utf-8')
        out['규약서 경로'] = len(set(re.findall(r'^  (/\S*):\s*$', s, re.M)))
        out['규약서 오퍼레이션'] = len(re.findall(r'^    (?:get|post|put|patch|delete):\s*$', s, re.M))
    return out


def collect(dirs: list[str]) -> dict:
    """{주장이름: {값: [파일...]}}"""
    found: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for d in dirs:
        base = _REPO / d
        if not base.exists():
            continue
        for p in sorted(base.rglob('*.html')):
            text = strip_html(p.read_text(encoding='utf-8', errors='replace'))
            rel = str(p.relative_to(_REPO))
            for name, pattern, _desc in CLAIMS:
                for m in re.finditer(pattern, text):
                    found[name][m.group(1)].append(rel)
    return found


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='문서 수치 주장 대조 — 충돌을 드러낸다')
    ap.add_argument('--dir', dest='dirs', action='append', default=None,
                    help='점검할 폴더. 반복 지정 가능. 미지정 시 기본 4곳')
    args = ap.parse_args(argv)
    dirs = args.dirs or DEFAULT_DIRS

    print('=' * 76)
    print(' 코드에서 뽑은 참값')
    print('=' * 76)
    truth = truth_from_code()
    for k, v in truth.items():
        print(f'  {k:<20} {v}')

    print()
    print('=' * 76)
    print(' 문서가 주장하는 값 — 값이 갈리면 ★')
    print('=' * 76)
    found = collect(dirs)
    conflicts = 0
    for name, _pat, desc in CLAIMS:
        vals = found.get(name)
        if not vals:
            continue
        mark = '★' if len(vals) > 1 else ' '
        if len(vals) > 1:
            conflicts += 1
        print(f'\n{mark} {name} ({desc}) — 서로 다른 값 {len(vals)}종')
        for v, files in sorted(vals.items(), key=lambda kv: -len(kv[1])):
            print(f'    {v:>5}  ({len(files)}곳)')
            for f in sorted(set(files))[:4]:
                print(f'           {f}')
            if len(set(files)) > 4:
                print(f'           ... 외 {len(set(files)) - 4}곳')

    print()
    print('=' * 76)
    print(f' 값이 갈리는 항목 {conflicts}건')
    print('=' * 76)
    print(" 주의 - '갈린다'가 곧 '틀렸다'는 아니다. 시점이 다른 스냅샷이거나 서로 다른")
    print(' 대상을 세는 경우가 섞인다. 각 자리를 열어 근거를 확인할 것.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
