#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ERD 개체관계도 SVG 생성 — 선이 상자를 넘지 않게 그리고, 클릭하면 관계만 남긴다.

이 도식이 두 번 지적받은 이유(2026-08-29):
  1판  선을 먼저 그리고 상자를 나중에 그려 선이 상자 뒤로 사라졌다
  2판  순서를 뒤집어 선을 위에 얹었더니 이번엔 **선이 상자와 글자를 가로질렀다.**
       흰 테두리(casing)를 둘러 선은 살렸지만, 그 흰 띠가 상자 테두리와 표 이름을
       지웠다. 선을 보이게 하려다 상자를 망가뜨린 셈이다.

3판이 하는 것 — 선이 상자를 지나갈 일 자체를 없앤다.

  · 관계 27개 중 **21개는 이웃한 열 사이**다. 열과 열 사이 통로에는 상자가 없으므로
    거기서만 휘면 아무것도 가리지 않는다.
  · 상자를 넘어가는 것은 **5개뿐**이다(L2 -> L0). 이 다섯만 상자 아래 전용 띠로
    내려 보내 가로지른다. 위를 지나가지 않으니 가릴 것이 없다.
  · 자기참조 1개는 오른쪽 고리로 돈다.
  · 흰 테두리를 걷어낸다. 더 이상 필요 없고, 그것이 상자를 지우던 원인이다.

  · 같은 열 안의 세로 순서는 **연결 상대의 평균 높이(barycenter)** 로 두 번 정렬해
    선이 서로 엇갈리는 횟수를 줄인다.

  · 상자를 클릭하면 그 표에 걸린 관계와 상대 표만 색으로 남기고 나머지는 흐려진다.
    27개를 한 번에 읽을 수는 없다 — 한 번에 하나씩 보게 하는 것이 목적이다.
    배경을 클릭하면 원래대로 돌아온다. 인쇄물에는 영향이 없다(상호작용 시에만 적용).

사용:
    python scripts/build_erd.py            # SVG 를 표준출력으로
    python scripts/build_erd.py --apply    # ERD 문서의 <svg> 를 교체
    python scripts/build_erd.py --apply --spec       # 테이블정의서_ERD.html 에 쓴다
"""
from __future__ import annotations

import argparse
import io
import re
import sys
from collections import defaultdict
from pathlib import Path

# [2026-09-03] Windows 기본 콘솔(cp949)에서 SVG·진단 문구를 못 찍고 죽는 것을 막는다.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

_ROOT = Path(__file__).resolve().parent.parent
_MODELS = _ROOT / "src" / "koipa" / "db" / "models.py"

# 표의 한국어 논리명 — 도식에 물리명과 함께 보인다.
LOGICAL = {
    "tb_classification_levels": "등급 체계",
    "tb_evaluation_factors": "평가요소",
    "tb_level_keywords": "등급 키워드",
    "tb_documents": "문서",
    "tb_chunks": "문서 청크",
    "tb_document_labels": "문서 라벨",
    "tb_document_factor_scores": "문서 평가요소 점수",
    "tb_classifications": "분류 결과",
    "tb_classification_evidence": "분류 판정 근거",
    "tb_corrections": "검수 교정 이력",
    "tb_model_versions": "모델 버전",
    "tb_training_runs": "학습 실행",
    "tb_training_epochs": "학습 에폭 지표",
    "tb_training_datasets": "학습셋 구성",
    "tb_prompt_versions": "프롬프트 버전",
    "tb_sample_documents": "합성·샘플 문서",
    "tb_audit_log": "감사 로그",
    "tb_guides": "가이드 문서",
    "tb_llm_usage": "LLM 사용량",
    "tb_advisory_locks": "동시성 잠금",
}
# [2026-09-11] 표준 명명 — 위 논리명은 옛 물리명으로 적어 두었다. models.py 의 새 이름으로 옮긴다.
# 대응표 정본은 koipa/db/standard_names.py(migration 7b3e9d2a4f10).
sys.path.insert(0, str(_ROOT / "src"))
from koipa.db.standard_names import TABLES as _STD_TABLES  # noqa: E402

LOGICAL = {_STD_TABLES.get(k, (k,))[0]: v for k, v in LOGICAL.items()}

# 외래키 제약은 없으나 논리적으로 참조하는 관계. FK 목록에는 안 잡히지만 관계는 실재한다.
#
# [2026-09-03] 정본을 scripts/table_spec_meta.py 로 옮겼다. 종전에는 여기와 build_table_spec.py
# 가 각각 목록을 들고 있어 도식은 점선 1개, 캡션·본문은 2건으로 갈렸다.
def _logical_fk() -> list[tuple[str, str, str]]:
    try:
        sys.path.insert(0, str(_ROOT.parent / "scripts"))
        import table_spec_meta as _meta        # noqa: PLC0415
        refs = getattr(_meta, "SOFT_REFS", None)
        if refs:
            return [tuple(r) for r in refs]
    except Exception as exc:                   # noqa: BLE001
        print("[!] 제약 없는 참조 정본을 못 읽었다: %s" % exc, file=sys.stderr)
    return []


LOGICAL_FK = _logical_fk()

BW, BH = 208, 46           # 상자 크기
GAP_X, GAP_Y = 132, 30     # 열 통로 폭 · 행 간격
PAD = 26
BAND_GAP = 26              # 상자 아래 띠까지의 여백
BAND_LANE = 15             # 띠 안의 선 간격


def parse_models() -> tuple[dict[str, int], list[tuple[str, str, str]]]:
    """(표 -> 칼럼 수), [(자식표, 칼럼, 부모표)] 를 돌려준다."""
    src = io.open(_MODELS, encoding="utf-8").read()
    cur = None
    cols: dict[str, int] = defaultdict(int)
    fks: list[tuple[str, str, str]] = []
    for ln in src.split("\n"):
        m = re.match(r'\s*__tablename__\s*=\s*"([^"]+)"', ln)
        if m:
            cur = m.group(1)
            cols.setdefault(cur, 0)
            continue
        if not cur:
            continue
        if re.match(r"\s*[a-z_]+\s*:.*mapped_column", ln):
            cols[cur] += 1
        m2 = re.match(r'\s*([a-z_]+)\s*:.*ForeignKey\("([^"]+)"', ln)
        if m2:
            fks.append((cur, m2.group(1), m2.group(2).split(".")[0]))
    # [2026-09-03] 정의서에서 뺀 표는 도식에서도 뺀다 — 안 그러면 상자 수가 본문과 갈린다.
    # 제외 목록의 정본은 scripts/table_spec_meta.py 한 곳이다.
    # [2026-09-03] 정의서에서 뺀 표·칼럼은 도식에서도 뺀다. 정본은 scripts/table_spec_meta.py
    # 한 곳이다 — 여기서 따로 세면 표지의 "N테이블 M칼럼" 과 도식이 갈린다(실측 223 대 214).
    excluded: set[str] = set()
    ex_cols: dict[str, dict] = {}
    try:
        sys.path.insert(0, str(_ROOT.parent / "scripts"))
        import table_spec_meta as _meta        # noqa: PLC0415
        excluded = set(getattr(_meta, "EXCLUDED_TABLES", {}))
        ex_cols = getattr(_meta, "EXCLUDED_COLUMNS", {}) or {}
    except Exception as exc:                   # noqa: BLE001
        # 조용히 넘어가면 상자 수가 어긋난 채로 그려진다 — 말은 한다.
        print("[!] 제외 목록을 못 읽었다(전체를 그린다): %s" % exc, file=sys.stderr)
    if excluded:
        cols = {k: v for k, v in cols.items() if k not in excluded}
        fks = [f for f in fks if f[0] not in excluded and f[2] not in excluded]
    for tname, drop in ex_cols.items():
        if tname in cols:
            cols[tname] = max(0, cols[tname] - len(drop))
    return dict(cols), fks


def layer_of(tables: list[str], edges: list[tuple[str, str]]) -> dict[str, int]:
    """의존 깊이 — 부모(참조당하는 쪽)가 0, 자식이 1씩 깊어진다."""
    parents = defaultdict(set)
    for child, parent in edges:
        if child != parent:
            parents[child].add(parent)
    depth: dict[str, int] = {}

    def d(t: str, seen: frozenset = frozenset()) -> int:
        if t in depth:
            return depth[t]
        if t in seen:
            return 0
        ps = parents.get(t) or ()
        v = 0 if not ps else 1 + max(d(p, seen | {t}) for p in ps)
        depth[t] = v
        return v

    for t in tables:
        d(t)
    return depth


def order_rows(by_layer: dict[int, list[str]], edges: list[tuple[str, str]]) -> None:
    """barycenter 정렬 — 연결 상대의 평균 위치로 세로 순서를 잡아 교차를 줄인다.

    자리 바꾸기를 두 번만 돈다. 더 돌려도 이 규모(19표)에서는 결과가 같다.
    """
    nb = defaultdict(list)
    for c, p in edges:
        if c != p:
            nb[c].append(p)
            nb[p].append(c)
    rank = {t: i for layer in by_layer for i, t in enumerate(by_layer[layer])}
    for _ in range(2):
        for layer in sorted(by_layer):
            items = by_layer[layer]
            def key(t: str) -> tuple[float, str]:
                near = [rank[o] for o in nb.get(t, []) if o in rank]
                return (sum(near) / len(near) if near else rank[t], t)
            items.sort(key=key)
            for i, t in enumerate(items):
                rank[t] = i


def build_svg() -> str:
    cols, fks = parse_models()
    fks = fks + LOGICAL_FK
    logical_set = set(LOGICAL_FK)
    tables = sorted(cols)
    edges = [(c, p) for c, _f, p in fks]
    depth = layer_of(tables, edges)

    by_layer: dict[int, list[str]] = defaultdict(list)
    for t in tables:
        by_layer[depth[t]].append(t)
    deg = defaultdict(int)
    for c, p in edges:
        deg[c] += 1
        deg[p] += 1
    for k in by_layer:
        by_layer[k].sort(key=lambda t: (-deg[t], t))
    order_rows(by_layer, edges)

    pos: dict[str, tuple[float, float]] = {}
    max_rows = max(len(v) for v in by_layer.values())
    grid_h = max_rows * BH + (max_rows - 1) * GAP_Y
    for layer in sorted(by_layer):
        x = PAD + layer * (BW + GAP_X)
        items = by_layer[layer]
        total = len(items) * BH + (len(items) - 1) * GAP_Y
        top = PAD + (grid_h - total) / 2
        for i, t in enumerate(items):
            pos[t] = (x, top + i * (BH + GAP_Y))

    # 상자를 넘어가는 관계(열 간격 2 이상)만 아래 띠로 보낸다.
    long_edges = [e for e in fks if e[0] in pos and e[2] in pos
                  and abs(depth[e[0]] - depth[e[2]]) >= 2]
    band_top = PAD + grid_h + BAND_GAP
    W = PAD * 2 + (max(by_layer) + 1) * BW + max(by_layer) * GAP_X
    H = band_top + max(len(long_edges), 1) * BAND_LANE + PAD

    # 같은 상자에 여러 선이 붙을 때 접점을 흩는다.
    in_idx: dict[str, list] = defaultdict(list)
    out_idx: dict[str, list] = defaultdict(list)
    for e in fks:
        if e[0] in pos and e[2] in pos and e[0] != e[2]:
            in_idx[e[2]].append(e)
            out_idx[e[0]].append(e)

    def anchor(table: str, bucket: dict, e, span: float = 26.0) -> float:
        """상자 세로 중심에서 흩어진 접점 y."""
        lst = bucket[table]
        n = len(lst)
        i = lst.index(e)
        y = pos[table][1] + BH / 2
        return y if n == 1 else y + (i - (n - 1) / 2) * min(span / max(n - 1, 1), 11.0)

    out: list[str] = []
    out.append(
        f'<svg id="erd-root" viewBox="0 0 {W:.0f} {H:.0f}" width="100%" '
        f'style="max-width:{W:.0f}px;height:auto" xmlns="http://www.w3.org/2000/svg" '
        f'role="img" aria-label="개체 관계도 — 상자를 클릭하면 그 표의 관계만 표시된다">'
    )
    out.append(
        '<defs>'
        '<marker id="erd-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
        'markerHeight="7" orient="auto-start-reverse">'
        '<path d="M0 0 L10 5 L0 10 z" fill="#3f3f46"/></marker>'
        '<marker id="erd-arrow-on" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
        'markerHeight="7" orient="auto-start-reverse">'
        '<path d="M0 0 L10 5 L0 10 z" fill="#1d4ed8"/></marker>'
        '</defs>'
    )
    # 상호작용은 **CSS 로만** 한다. SVG 안의 <script> 는 실행하지 않는 뷰어가 있고
    # (jsdom 실측 2026-08-29: HTML script 는 실행, SVG script 는 미실행) 그러면 도식이
    # 조용히 정적 그림으로 남는다. :hover 와 :focus 는 브라우저 기본 동작이라 그럴 일이 없다.
    # tabindex 가 있으므로 **클릭하면 focus 가 잡혀 선택이 유지되고**, 다른 곳을 클릭하면 풀린다.
    # 상자가 선보다 먼저 그려지므로 형제 결합자 ~ 로 상자에서 선을 고를 수 있다.
    css = [
        '.erd-box{cursor:pointer;outline:none}',
        '.erd-box rect{fill:#fff;stroke:#18181b;stroke-width:1.2}',
        '.erd-edge{fill:none;stroke:#52525b;stroke-width:1.4;marker-end:url(#erd-arrow)}',
        '.erd-edge.logical{stroke-dasharray:6 4}',
        # 선택이 일어난 동안만 나머지를 죽인다. :has 를 못 읽는 뷰어에서는 흐려지지
        # 않을 뿐 강조는 그대로 걸린다(퇴화해도 읽을 수 있다).
        '#erd-root:has(.erd-box:hover) .erd-edge,'
        '#erd-root:has(.erd-box:focus) .erd-edge'
        '{stroke:#e4e4e7;stroke-width:1;marker-end:none}',
        '#erd-root:has(.erd-box:hover) .erd-box rect,'
        '#erd-root:has(.erd-box:focus) .erd-box rect{stroke:#d4d4d8}',
        '#erd-root:has(.erd-box:hover) .erd-box text,'
        '#erd-root:has(.erd-box:focus) .erd-box text{fill:#a1a1aa}',
        '@media print{#erd-root .erd-edge{stroke:#52525b;stroke-width:1.4}'
        '#erd-root .erd-box rect{stroke:#18181b}}',
    ]
    for t in tables:
        sel_edge = (f'#erd-root .erd-box[data-t="{t}"]:hover ~ .erd-edge[data-c="{t}"],'
                    f'#erd-root .erd-box[data-t="{t}"]:hover ~ .erd-edge[data-p="{t}"],'
                    f'#erd-root .erd-box[data-t="{t}"]:focus ~ .erd-edge[data-c="{t}"],'
                    f'#erd-root .erd-box[data-t="{t}"]:focus ~ .erd-edge[data-p="{t}"]')
        css.append(sel_edge + '{stroke:#1d4ed8;stroke-width:2.2;'
                              'marker-end:url(#erd-arrow-on)}')
        css.append(f'#erd-root .erd-box[data-t="{t}"]:hover rect,'
                   f'#erd-root .erd-box[data-t="{t}"]:focus rect'
                   '{fill:#eff6ff;stroke:#1d4ed8;stroke-width:2.4}')
        css.append(f'#erd-root .erd-box[data-t="{t}"]:hover text,'
                   f'#erd-root .erd-box[data-t="{t}"]:focus text{{fill:#1e3a8a}}')
    out.append('<style>' + "".join(css) + '</style>')

    # ── ① 상자 ─────────────────────────────────────────────────────
    for t in tables:
        x, y = pos[t]
        out.append(f'<g class="erd-box" data-t="{t}" tabindex="0">')
        out.append(f'<rect x="{x:.0f}" y="{y:.0f}" width="{BW}" height="{BH}" rx="4"/>')
        out.append(
            f'<text class="t1" x="{x + 10:.0f}" y="{y + 19:.0f}" font-size="12.5" '
            f'font-weight="700" fill="#0a0a0a">{LOGICAL.get(t, t)}</text>'
        )
        out.append(
            f'<text class="t2" x="{x + 10:.0f}" y="{y + 35:.0f}" font-size="10" '
            f'font-family="ui-monospace,monospace" fill="#52525b">{t} · {cols[t]}칼럼</text>'
        )
        out.append("</g>")

    # ── ② 관계선 ───────────────────────────────────────────────────
    band = 0
    for e in fks:
        child, field, parent = e
        if child not in pos or parent not in pos:
            continue
        cx, cy = pos[child]
        px, py = pos[parent]
        cls = "erd-edge logical" if e in logical_set else "erd-edge"

        if child == parent:
            # 자기참조 — 오른쪽 바깥으로 도는 고리. 상자 위를 지나지 않는다.
            d = (f'M{cx + BW:.0f},{cy + 13:.0f} '
                 f'C{cx + BW + 40:.0f},{cy + 4:.0f} {cx + BW + 40:.0f},{cy + BH - 4:.0f} '
                 f'{cx + BW:.0f},{cy + BH - 13:.0f}')
        elif abs(depth[child] - depth[parent]) >= 2:
            # 상자를 넘어야 하는 관계. 세로로 곧장 내리면 **같은 열 아래 상자들을 관통한다**
            # (실측: 5개 전부 관통했다). 그래서 열과 열 사이 통로로 빠져나간 뒤 내려간다.
            # 통로와 아래 띠에는 상자가 없으므로 어느 구간도 상자를 지나지 않는다.
            lane = band_top + band * BAND_LANE
            off = (band % 3 - 1) * 11
            band += 1
            xc = PAD + depth[child] * (BW + GAP_X) - GAP_X / 2 + off      # 자식 열 왼쪽 통로
            xp = PAD + (depth[parent] + 1) * (BW + GAP_X) - GAP_X / 2 + off  # 부모 열 오른쪽 통로
            sy = anchor(child, out_idx, e)
            ey = anchor(parent, in_idx, e)
            r = 9
            d = (f'M{cx:.0f},{sy:.0f} H{xc + r:.0f} Q{xc:.0f},{sy:.0f} {xc:.0f},{sy + r:.0f} '
                 f'V{lane - r:.0f} Q{xc:.0f},{lane:.0f} {xc - r:.0f},{lane:.0f} '
                 f'H{xp + r:.0f} Q{xp:.0f},{lane:.0f} {xp:.0f},{lane - r:.0f} '
                 f'V{ey + r:.0f} Q{xp:.0f},{ey:.0f} {xp - r:.0f},{ey:.0f} '
                 f'H{px + BW:.0f}')
        else:
            # 이웃한 열 — 통로 안에서만 휜다. 통로에는 상자가 없다.
            sy = anchor(child, out_idx, e)
            ey = anchor(parent, in_idx, e)
            sx, ex = cx, px + BW
            mid = (sx + ex) / 2
            d = f'M{sx:.0f},{sy:.0f} C{mid:.0f},{sy:.0f} {mid:.0f},{ey:.0f} {ex:.0f},{ey:.0f}'

        out.append(f'<path class="{cls}" data-c="{child}" data-p="{parent}" d="{d}"><title>'
                   f'{LOGICAL.get(child, child)}.{field} → {LOGICAL.get(parent, parent)}'
                   f'</title></path>')

    out.append(
        f'<text x="{PAD}" y="{H - 8:.0f}" font-size="10.5" fill="#71717a">'
        f'상자에 마우스를 올리거나 클릭하면 그 표에 걸린 관계만 파랗게 남습니다. 다른 곳을 클릭하면 풀립니다.'
        f'</text>'
    )
    out.append("</svg>")
    return "\n".join(out)


# ── 관계 목록·개수 (2026-09-05) ────────────────────────────────────────────
#
# 왜 여기서 쓰나. 종전에는 이 스크립트가 <svg> 만 갈아 끼우고 문서 본문의 관계 목록과
# 머리말 개수는 손으로 적혀 있었다. 그래서 실측 2026-09-05 에 셋이 서로 달랐다 —
# 머리 배지 "관계 25" · 캡션 "관계 27(외래키 26 · 논리 참조 1)" · 목록 27행인데
# 코드의 진실은 외래키 24 · 논리 참조 3 이었다. 도식만 코드에서 뽑고 숫자는 손으로
# 적으면 이렇게 갈린다. 이제 넷 다 같은 함수가 만든다.

def _area(child: str) -> str:
    """관계의 영역 — 자식 표가 속한 그룹 이름. 정본은 table_spec_meta.GROUPS 다."""
    try:
        sys.path.insert(0, str(_ROOT.parent / "scripts"))
        import table_spec_meta as _meta        # noqa: PLC0415

        gid = _meta.TABLES.get(child, ("", "", ""))[0]
        for g, gname, _desc in _meta.GROUPS:
            if g == gid:
                return gname
    except Exception:                          # noqa: BLE001
        pass
    return "—"


def build_relations() -> tuple[str, int, int]:
    """(관계 목록 tbody, 외래키 수, 논리 참조 수)."""
    _cols, fks = parse_models()
    rows = []
    for child, col, parent in fks:
        area = "자기참조" if child == parent else _area(child)
        rows.append((child, col, parent, area))
    for child, col, parent in LOGICAL_FK:
        rows.append((child, col, parent, "논리 참조"))
    rows.sort(key=lambda r: (r[3] == "논리 참조", r[0], r[1]))

    def cell(t: str) -> str:
        return f'{LOGICAL.get(t, t)}<br /><span class="src">{t}</span>'

    body = "\n".join(
        f'      <tr><td>{cell(c)}</td><td><code>{col}</code></td>'
        f'<td>{cell(pa)}</td><td class="c">{ar}</td></tr>'
        for c, col, pa, ar in rows
    )
    return body, len(fks), len(LOGICAL_FK)


def _replace_between(s: str, start: str, end: str, new: str) -> str | None:
    i = s.find(start)
    if i < 0:
        return None
    j = s.find(end, i + len(start))
    if j < 0:
        return None
    return s[: i + len(start)] + new + s[j:]


def apply_body(s: str, ntab: int) -> tuple[str, list[str]]:
    """관계 목록·개수 문구를 코드값으로 맞춘다. 바꾼 자리 이름을 함께 돌려준다."""
    body, nfk, nsoft = build_relations()
    total = nfk + nsoft
    done = []

    out = _replace_between(s, '<h2>2. 관계 목록</h2>', '</tbody>', "")
    if out is not None:                       # 표 전체를 다시 쓴다
        head = ('\n  <div class="tw">\n  <table>\n'
                '    <thead><tr><th style="width:32%">자식 테이블</th>'
                '<th style="width:22%">외래키 컬럼</th>'
                '<th style="width:32%">부모 테이블</th>'
                '<th style="width:14%">영역</th></tr></thead>\n    <tbody>\n')
        s = _replace_between(s, '<h2>2. 관계 목록</h2>', '</tbody>', head + body + "\n    ")
        done.append("관계 목록 %d행" % (nfk + nsoft))

    s2 = re.sub(r"테이블 \d+ 개 · 관계 \d+ 개\(외래키 \d+ · 논리 참조 \d+\)",
                f"테이블 {ntab} 개 · 관계 {total} 개(외래키 {nfk} · 논리 참조 {nsoft})", s)
    if s2 != s:
        done.append("캡션")
        s = s2
    s2 = re.sub(r"테이블 \d+ · 관계 \d+ ·", f"테이블 {ntab} · 관계 {total} ·", s)
    if s2 != s:
        done.append("머리 배지")
        s = s2
    return s, done


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="ERD 문서의 <svg> 를 교체한다")
    ap.add_argument("--spec", action="store_true",
                    help="테이블정의서_ERD.html 에 쓴다(기본은 ERD_개체관계도.html)")
    args = ap.parse_args(argv)

    svg = build_svg()
    if not args.apply:
        sys.stdout.write(svg + "\n")
        return 0

    name = "테이블정의서_ERD.html" if args.spec else "ERD_개체관계도.html"
    targets = [
        # [2026-09-05] 감리문서 사본은 이름의 밑줄 위치가 다르다(테이블_정의서.html).
        # build_table_spec.OUTS 와 같은 목록을 봐야 한 쪽만 뒤처지지 않는다.
        _ROOT.parent / "doc" / "감리문서" / ("테이블_정의서.html" if args.spec else name),
        _ROOT.parent / "doc" / "감리문서" / name,
        _ROOT.parent / "doc" / "result" / "KL_회신_2026-08-28" / "첨부" / name,
        _ROOT.parent / "doc" / "result" / "KL_AI자료_2026-08" / name,
        _ROOT.parent / "doc" / "result" / "KL_AI자료_2026-08" / "첨부문서" / name,
    ]
    n = 0
    seen = set()
    for p in targets:
        if p in seen:            # --spec 이 아닐 때 감리문서 두 항목이 같은 파일을 가리킨다
            continue
        seen.add(p)
        if not p.exists():
            print(f"  [없음] {p}")
            continue
        s = io.open(p, encoding="utf-8", newline="").read()
        i, j = s.find("<svg"), s.find("</svg>")
        if i < 0 or j < 0:
            print(f"  [svg 없음] {p.name}")
            continue
        s = s[:i] + svg + s[j + len("</svg>"):]
        s, done = apply_body(s, len(parse_models()[0]))
        io.open(p, "w", encoding="utf-8", newline="").write(s)
        n += 1
        print(f"  교체 {p.name}" + (" · " + " · ".join(done) if done else ""))
    print(f"{n}개 문서 갱신")
    return 0


if __name__ == "__main__":
    sys.exit(main())
