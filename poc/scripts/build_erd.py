#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ERD 개체관계도 SVG 생성 — 선이 실제로 보이게 그린다.

종전 도식의 문제(2026-08-29 지적):
  · 선을 먼저 그리고 상자를 나중에 그려 **선이 상자 뒤로 사라졌다**
  · 선 색이 옅고(#a1a1aa · opacity .85 · 굵기 1.1) 화면에서 따라갈 수 없었다
  · 5열 격자에 26개 관계를 얹어 선이 서로 교차했다

이 생성기가 하는 것:
  · **의존 깊이로 열을 나눈다** — 참조당하는 표(부모)를 왼쪽에 둔다.
    같은 열 안에서는 연결 상대와 가까운 순으로 세로 정렬해 교차를 줄인다.
  · **상자를 먼저 그리고 선을 위에 얹는다.** 선에는 흰색 테두리(casing)를 둘러
    상자 위를 지나가도 끊겨 보이지 않게 한다.
  · 자기참조(tb_model_versions)는 옆으로 도는 고리로 그린다.

사용:
    python scripts/build_erd.py            # SVG 를 표준출력으로
    python scripts/build_erd.py --apply    # ERD 문서 3벌의 <svg> 를 교체
"""
from __future__ import annotations

import argparse
import io
import re
import sys
from collections import defaultdict
from pathlib import Path

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
}


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
    return dict(cols), fks


def layer_of(tables: list[str], edges: list[tuple[str, str]]) -> dict[str, int]:
    """의존 깊이 — 부모(참조당하는 쪽)가 0, 자식이 1씩 깊어진다."""
    parents = defaultdict(set)
    for child, parent in edges:
        if child != parent:                     # 자기참조는 깊이에 넣지 않는다
            parents[child].add(parent)
    depth: dict[str, int] = {}

    def d(t: str, seen: frozenset = frozenset()) -> int:
        if t in depth:
            return depth[t]
        if t in seen:                           # 순환 방지
            return 0
        ps = parents.get(t) or ()
        v = 0 if not ps else 1 + max(d(p, seen | {t}) for p in ps)
        depth[t] = v
        return v

    for t in tables:
        d(t)
    return depth


# 외래키 제약은 없으나 논리적으로 참조하는 관계. 월별 파티션 테이블에는 제약을 걸지
# 않는다(파티션 추가·분리 비용) — 그래서 FK 목록에는 안 잡히지만 관계는 실재한다.
# 도식에서 빠지면 "청크가 어디에 속하는지" 가 보이지 않으므로 점선으로 그린다.
LOGICAL_FK = [
    ("tb_chunks", "doc_id", "tb_documents"),
]


def build_svg() -> str:
    cols, fks = parse_models()
    fks = fks + LOGICAL_FK
    tables = sorted(cols)
    edges = [(c, p) for c, _f, p in fks]
    depth = layer_of(tables, edges)

    # 열별로 묶고, 연결이 많은 표를 위로 올려 선이 짧아지게 한다.
    deg = defaultdict(int)
    for c, p in edges:
        deg[c] += 1
        deg[p] += 1
    by_layer: dict[int, list[str]] = defaultdict(list)
    for t in tables:
        by_layer[depth[t]].append(t)
    for k in by_layer:
        by_layer[k].sort(key=lambda t: (-deg[t], t))

    BW, BH = 208, 46          # 상자 크기
    GAP_X, GAP_Y = 108, 26    # 열 간격(선이 지나갈 통로) · 행 간격
    PAD = 24

    pos: dict[str, tuple[float, float]] = {}
    max_rows = max(len(v) for v in by_layer.values())
    for layer in sorted(by_layer):
        x = PAD + layer * (BW + GAP_X)
        items = by_layer[layer]
        # 열마다 세로 가운데 정렬
        total = len(items) * BH + (len(items) - 1) * GAP_Y
        top = PAD + (max_rows * BH + (max_rows - 1) * GAP_Y - total) / 2
        for i, t in enumerate(items):
            pos[t] = (x, top + i * (BH + GAP_Y))

    W = PAD * 2 + (max(by_layer) + 1) * BW + max(by_layer) * GAP_X
    H = PAD * 2 + max_rows * BH + (max_rows - 1) * GAP_Y

    out: list[str] = []
    out.append(
        f'<svg viewBox="0 0 {W:.0f} {H:.0f}" width="100%" '
        f'style="max-width:{W:.0f}px;height:auto" xmlns="http://www.w3.org/2000/svg" '
        f'role="img" aria-label="개체 관계도">'
    )
    out.append(
        '<defs><marker id="erd-arrow" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="8" markerHeight="8" orient="auto-start-reverse">'
        '<path d="M0 0 L10 5 L0 10 z" fill="#18181b"/></marker></defs>'
    )

    # ── ① 상자 먼저 ────────────────────────────────────────────────
    for t in tables:
        x, y = pos[t]
        out.append(
            f'<rect x="{x:.0f}" y="{y:.0f}" width="{BW}" height="{BH}" rx="4" '
            f'fill="#ffffff" stroke="#18181b" stroke-width="1.2"/>'
        )
        out.append(
            f'<text x="{x + 10:.0f}" y="{y + 19:.0f}" font-size="12.5" '
            f'font-weight="700" fill="#0a0a0a">{LOGICAL.get(t, t)}</text>'
        )
        out.append(
            f'<text x="{x + 10:.0f}" y="{y + 35:.0f}" font-size="10" '
            f'font-family="ui-monospace,monospace" fill="#52525b">'
            f'{t} · {cols[t]}칼럼</text>'
        )

    # ── ② 선을 위에 얹는다 — 흰 테두리를 둘러 상자 위에서도 끊기지 않는다 ──
    lane = defaultdict(int)   # 같은 열 사이를 지나는 선이 겹치지 않도록 통로를 나눈다
    for child, field, parent in fks:
        if child not in pos or parent not in pos:
            continue
        cx, cy = pos[child]
        px, py = pos[parent]

        if child == parent:                       # 자기참조 — 오른쪽으로 도는 고리
            d = (f'M{cx + BW:.0f},{cy + 14:.0f} '
                 f'C{cx + BW + 44:.0f},{cy + 6:.0f} {cx + BW + 44:.0f},{cy + BH - 6:.0f} '
                 f'{cx + BW:.0f},{cy + BH - 14:.0f}')
        else:
            # 자식(오른쪽) → 부모(왼쪽) 방향. 열 사이 통로에서 한 번 꺾는다.
            key = (min(depth[child], depth[parent]), max(depth[child], depth[parent]))
            lane[key] += 1
            off = (lane[key] % 5 - 2) * 9         # 통로 안에서 좌우로 흩는다
            sx, sy = cx, cy + BH / 2
            ex, ey = px + BW, py + BH / 2
            mid = (sx + ex) / 2 + off
            d = f'M{sx:.0f},{sy:.0f} C{mid:.0f},{sy:.0f} {mid:.0f},{ey:.0f} {ex:.0f},{ey:.0f}'

        # 흰 테두리(아래) → 본선(위). 상자 위를 지나가도 선이 읽힌다.
        out.append(f'<path d="{d}" fill="none" stroke="#ffffff" stroke-width="4.5" '
                   f'stroke-linecap="round" opacity="0.95"/>')
        dash = ' stroke-dasharray="6 4"' if (child, field, parent) in LOGICAL_FK else ''
        out.append(f'<path d="{d}" fill="none" stroke="#3f3f46" stroke-width="1.5"{dash} '
                   f'marker-end="url(#erd-arrow)"/>')

    out.append('</svg>')
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="ERD 문서의 <svg> 를 교체한다")
    args = ap.parse_args(argv)

    svg = build_svg()
    if not args.apply:
        sys.stdout.write(svg + "\n")
        return 0

    targets = [
        _ROOT.parent / "doc" / "감리문서" / "ERD_개체관계도.html",
        _ROOT.parent / "doc" / "result" / "KL_회신_2026-08-28" / "첨부" / "ERD_개체관계도.html",
    ]
    n = 0
    for p in targets:
        if not p.exists():
            print(f"  [없음] {p}")
            continue
        s = io.open(p, encoding="utf-8", newline="").read()
        i, j = s.find("<svg"), s.find("</svg>")
        if i < 0 or j < 0:
            print(f"  [svg 없음] {p.name}")
            continue
        s = s[:i] + svg + s[j + len("</svg>"):]
        io.open(p, "w", encoding="utf-8", newline="").write(s)
        n += 1
        print(f"  교체 {p.name}")
    print(f"{n}개 문서 갱신")
    return 0


if __name__ == "__main__":
    sys.exit(main())
