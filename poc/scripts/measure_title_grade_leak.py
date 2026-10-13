#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""제목이 등급을 알려주는가 — **본문에서 제목을 뽑아** 잰다.

왜 별도 도구인가(2026-09-09). `measure_shortcut_bias.py` 는 **기록된 필드**를 본다.
그래서 학습셋에 대고 돌리면 이렇게 나온다:

    [문서유형 x 등급] 해당 필드가 기록되어 있지 않아 재지 못했다.

감리 회신에 "학습용 문서에도 제목과 등급이 연결된 구조가 있는지는 아직 확인하지
못하였습니다"라고 적은 자리가 정확히 여기다. 학습셋 행에는 title·document_type 칼럼이
없다 — 하지만 **본문 첫 줄이 제목인 행이 많다.** 그 제목을 꺼내서 잰다.

⚠ 두 도구를 합치지 않는다. 저쪽은 "기록된 것으로 잰다"가 계약이고 이쪽은 "본문에서
  꺼내서 잰다"가 계약이다. 합치면 '못 쟀다'와 '추출해서 쟀다'가 한 숫자로 섞인다.
  지표 정의(Cramer's V·최빈등급 적중률)는 **같은 것을 쓴다** — 두 결과를 나란히 읽어야
  하기 때문이다.

재는 축 넷:

    ① 제목 추출 가능 비율   첫 줄이 제목인 행이 몇 %인가. 이게 낮으면 나머지는 부분표본이다
    ② 제목 x 등급           제목만 보고 등급을 맞힐 수 있는가
    ③ 문서유형 x 등급       제목의 **마지막 어절**을 유형으로 본다(손으로 목록을 고르지 않는다)
    ④ 제목의 등급어 노출     '[특급기밀]' 처럼 제목이 등급을 직접 말하는가

⚠ ④는 출처를 갈라서 낸다. generator.FORBIDDEN_GRADE_TERMS 는 **생성물에서만** 금지다 —
  실문서에 찍힌 "대외비"는 비밀관리성(M)의 근거라 지우면 안 된다(generator.py:127 주석).

⚠ 적중률이 높다고 모델이 그 지름길을 쓴다는 뜻은 아니다. 이 도구가 재는 것은 **자료에
  그 신호가 있는가**이지 모델이 그것을 쓰는가가 아니다.

사용:
    python scripts/measure_title_grade_leak.py --dataset datasets/labeled_p1_v5_clean
    python scripts/measure_title_grade_leak.py --pool datasets/proxy_gold/single_document_candidates
    python scripts/measure_title_grade_leak.py --dataset ... --pool ... --json reports/title_leak.json
"""
from __future__ import annotations

# 콘솔 출구를 UTF-8 로 고정한다 — cp949 콘솔에서 em dash 하나에 죽는 것을 막는다.
try:  # 스크립트로 직접 실행 - scripts/ 가 sys.path 에 들어온다
    from _cli_io import force_utf8_stdio  # noqa: E402
except ImportError:  # 패키지로 import
    from scripts._cli_io import force_utf8_stdio  # noqa: E402

force_utf8_stdio()

import argparse  # noqa: E402
import glob  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import sys  # noqa: E402
from collections import Counter, defaultdict  # noqa: E402
from pathlib import Path  # noqa: E402

_HERE = Path(__file__).resolve().parent
_POC = _HERE.parent
if str(_POC / "src") not in sys.path:
    sys.path.insert(0, str(_POC / "src"))

GRADES = ("TS", "S1", "S2", "S3")

# 제목으로 인정할 첫 줄의 상한. 이보다 길면 제목이 아니라 본문 첫 문단으로 본다.
# 근거: 합성 생성기의 제목은 한 줄이고, 실측 학습셋에서 제목 줄 중앙값이 20자대다.
_TITLE_MAX_CHARS = 80


def _canonical_grade_terms() -> tuple[str, ...]:
    """등급어 정본 목록. 손으로 적지 않고 생성기에서 가져온다."""
    from koipa.modules.m1_synthesis.generator import FORBIDDEN_GRADE_TERMS  # noqa: PLC0415

    return FORBIDDEN_GRADE_TERMS


def extract_title(text: str) -> str | None:
    """본문에서 제목을 꺼낸다. 없으면 None.

    조건 셋을 모두 만족해야 제목으로 본다 — 하나라도 어기면 '제목 없음'이다.
    느슨하게 잡으면 본문 첫 문장이 제목으로 세어져 결합도가 부풀려진다.
    """
    if not text:
        return None
    lines = [ln.strip() for ln in str(text).splitlines()]
    lines = [ln for ln in lines if ln]
    if len(lines) < 2:                       # 뒤에 본문이 있어야 제목이다
        return None
    head = lines[0]
    if len(head) > _TITLE_MAX_CHARS:         # 너무 길면 본문 문단이다
        return None
    if head.endswith((".", "다.", "니다.")):  # 문장으로 끝나면 제목이 아니다
        return None
    return head


def _cramers_v(table: dict[str, Counter], n: int) -> float:
    """범주 x 등급 분할표의 Cramer's V. measure_shortcut_bias 와 같은 정의."""
    if n == 0 or not table:
        return 0.0
    col: Counter = Counter()
    for row in table.values():
        col.update(row)
    chi2 = 0.0
    for row in table.values():
        total = sum(row.values())
        for grade in GRADES:
            expected = total * col[grade] / n
            if expected > 0:
                chi2 += (row[grade] - expected) ** 2 / expected
    k = min(len(table), len(GRADES))
    if k < 2:
        return 0.0
    return (chi2 / (n * (k - 1))) ** 0.5


def _axis_report(name: str, pairs: list[tuple[str, str]]) -> dict | None:
    """(범주값, 등급) 쌍에서 결합도와 최빈등급 적중률. measure_shortcut_bias 와 같은 정의."""
    pairs = [(str(v), g) for v, g in pairs if g in GRADES and v]
    n = len(pairs)
    if n == 0:
        return None
    table: dict[str, Counter] = defaultdict(Counter)
    for value, grade in pairs:
        table[value][grade] += 1
    col = Counter(g for _, g in pairs)
    hit = sum(row.most_common(1)[0][1] for row in table.values())
    baseline = col.most_common(1)[0][1]
    # 한 등급으로만 나타나는 범주 — 이 축이 등급을 '지시'하는 정도
    pure = sum(1 for row in table.values() if len(row) == 1)
    return {
        "axis": name, "n": n, "categories": len(table),
        "cramers_v": round(_cramers_v(table, n), 3),
        "hit": hit, "hit_rate": round(hit / n, 4),
        "baseline_rate": round(baseline / n, 4),
        "gain_pp": round((hit - baseline) / n * 100, 1),
        "pure_categories": pure,
        # 범주가 표본만큼 많으면 "그 범주의 최빈등급"이 사실상 자기 자신이라 적중률이
        # 100% 에 가깝게 부풀려진다. 지표가 아니라 과적합의 그림자다 — 비율을 함께 낸다.
        "categories_per_row": round(len(table) / n, 3),
        "inflated": len(table) / n > 0.3,
    }


def _doc_type(title: str) -> str:
    """제목의 **마지막 어절**을 문서유형으로 본다.

    유형 목록을 손으로 적지 않는 이유: 목록을 고르는 순간 그 목록 밖 유형이 통째로
    안 세어진다([[counting-tools-hand-picked-scope]] 와 같은 함정). 데이터가 말하게 둔다.
    괄호 태그('[특급기밀]' 등)는 유형이 아니므로 떼고 본다.
    """
    t = re.sub(r"[\[(（【][^\])）】]*[\])）】]", " ", title).strip()
    parts = [p for p in t.split() if p]
    return parts[-1] if parts else ""


def _grade_terms_in(text: str, terms: tuple[str, ...]) -> list[str]:
    low = text or ""
    return [t for t in terms if t in low]


# ── 자료 읽기 ────────────────────────────────────────────────────────────────
def load_dataset(root: Path) -> list[dict]:
    """학습셋(jsonl 3종)을 읽는다. label·text·source·document_origin 만 쓴다."""
    rows: list[dict] = []
    for name in ("train.jsonl", "val.jsonl", "test.jsonl"):
        p = root / name
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            rows.append({
                "label": r.get("label"),
                "text": r.get("text") or "",
                "source": r.get("source") or "",
                "origin": r.get("document_origin") or "",
                "split": name.split(".")[0],
            })
    return rows


def load_pool(root: Path) -> list[dict]:
    """골든 후보 풀을 읽는다. 콘솔과 같은 우선순위로 본문을 고른다."""
    rows: list[dict] = []
    for meta_path in sorted(glob.glob(str(root / "*.metadata.json"))):
        try:
            meta = json.loads(Path(meta_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        doc_id = str(meta.get("doc_id") or "")
        label = meta.get("intended_label")
        rev = str(meta.get("content_revision_path") or "").strip()
        body = root / rev if rev else None
        if body is None or not body.is_file():
            cands = [p for p in glob.glob(str(root / f"{doc_id}*.md"))]
            if len(cands) != 1:
                continue
            body = Path(cands[0])
        try:
            text = body.read_text(encoding="utf-8")
        except OSError:
            continue
        rows.append({
            "label": label, "text": text,
            "source": "candidate",
            "origin": meta.get("document_origin") or "",
            "split": "pool",
        })
    return rows


# ── 보고 ────────────────────────────────────────────────────────────────────
def analyse(rows: list[dict], name: str) -> dict:
    terms = _canonical_grade_terms()
    titled = [(r, extract_title(r["text"])) for r in rows]
    with_title = [(r, t) for r, t in titled if t]

    out: dict = {
        "name": name,
        "rows": len(rows),
        "with_title": len(with_title),
        "title_rate": round(len(with_title) / len(rows), 4) if rows else 0.0,
        "by_source_title_rate": {},
        "axes": [],
        "grade_terms_in_title": {},
    }
    # 출처별 제목 추출률 — 낮은 출처가 있으면 아래 축이 그 출처를 대표하지 못한다
    by_src: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for r, t in titled:
        s = r["source"] or "(미기록)"
        by_src[s][1] += 1
        if t:
            by_src[s][0] += 1
    out["by_source_title_rate"] = {
        s: {"with_title": a, "rows": b, "rate": round(a / b, 4)}
        for s, (a, b) in sorted(by_src.items(), key=lambda kv: -kv[1][1])
    }

    for axis_name, key in (("제목 x 등급", lambda t: t), ("문서유형(제목 말미) x 등급", _doc_type)):
        rep = _axis_report(axis_name, [(key(t), r["label"]) for r, t in with_title])
        if rep:
            out["axes"].append(rep)

    # 등급어가 제목에 직접 나오는가 — 출처를 갈라서
    for r, t in with_title:
        hits = _grade_terms_in(t, terms)
        if not hits:
            continue
        s = r["source"] or "(미기록)"
        slot = out["grade_terms_in_title"].setdefault(s, {"count": 0, "terms": {}})
        slot["count"] += 1
        for h in hits:
            slot["terms"][h] = slot["terms"].get(h, 0) + 1
    return out


def render(rep: dict) -> None:
    print("=" * 72)
    print(f"{rep['name']}")
    print("=" * 72)
    print(f"  행 {rep['rows']:,} · 제목을 뽑은 행 {rep['with_title']:,} "
          f"({rep['title_rate'] * 100:.1f}%)")
    if rep["title_rate"] < 1.0:
        print("  ⚠ 제목이 없는 행은 아래 축의 분모에서 빠진다 — 부분표본이다.")
    print()
    print("  출처별 제목 추출률")
    for s, d in rep["by_source_title_rate"].items():
        print(f"    {s:<22} {d['with_title']:>6,}/{d['rows']:<6,} = {d['rate'] * 100:5.1f}%")
    print()
    for a in rep["axes"]:
        print(f"  [{a['axis']}] 분모 {a['n']:,} · 범주 {a['categories']:,}개")
        print(f"    Cramer's V                = {a['cramers_v']}")
        print(f"    이 축만 보고 최빈등급 찍기   = {a['hit']:,}/{a['n']:,} = {a['hit_rate'] * 100:.1f}%")
        print(f"    전체 최빈등급 하나로 찍기    = {a['baseline_rate'] * 100:.1f}%"
              f"   (차이 {a['gain_pp']}%p)")
        print(f"    한 등급으로만 나타난 범주    = {a['pure_categories']:,}/{a['categories']:,}")
        if a.get("inflated"):
            print(f"    ⚠ 범주/행 = {a['categories_per_row']} — 범주가 거의 유일하다.")
            print("      이 적중률은 **부풀려진 값**이라 인용하면 안 된다(그 범주의 최빈등급이")
            print("      사실상 자기 자신이다). 결합의 근거로는 아래 등급어 노출을 쓸 것.")
        print()
    if rep["grade_terms_in_title"]:
        print("  [제목에 등급어가 직접 나온 행] — 생성물에서는 금지, 실문서에서는 M 의 근거")
        for s, d in sorted(rep["grade_terms_in_title"].items(), key=lambda kv: -kv[1]["count"]):
            top = ", ".join(f"{k}×{v}" for k, v in sorted(d["terms"].items(), key=lambda kv: -kv[1])[:5])
            print(f"    {s:<22} {d['count']:>6,}건   {top}")
    else:
        print("  [제목에 등급어가 직접 나온 행] 0건")
    print()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dataset", help="학습셋 디렉터리(train/val/test.jsonl)")
    ap.add_argument("--pool", help="골든 후보 풀 디렉터리")
    ap.add_argument("--json", help="결과를 이 경로에 JSON 으로 저장")
    args = ap.parse_args()
    if not args.dataset and not args.pool:
        ap.error("--dataset 또는 --pool 중 하나는 필요하다")

    reports = []
    if args.dataset:
        root = Path(args.dataset)
        rows = load_dataset(root if root.is_absolute() else _POC / root)
        reports.append(analyse(rows, f"학습셋: {root}"))
    if args.pool:
        root = Path(args.pool)
        rows = load_pool(root if root.is_absolute() else _POC / root)
        reports.append(analyse(rows, f"골든 후보 풀: {root}"))

    for rep in reports:
        render(rep)

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  JSON 저장: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
