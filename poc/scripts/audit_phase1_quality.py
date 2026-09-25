"""1차 통합테스트 데이터(v5_clean 2,554건)의 결함 유형별 건수와 등급별 정밀도·재현율·F1 을 센다.

보호원 요청(2026-09-20): ① 모의문서 1차 품질 제외 기준 ② 등급별 Precision/Recall/F1.
전에는 이 숫자를 임시 스크립트(리포 밖)로 셌다 — 같은 질문이 또 오면 다시 **돌리도록** 남긴다.

    poc/.venv/Scripts/python.exe -X utf8 scripts/audit_phase1_quality.py

읽는 법
    [A] 결함 유형별 건수 — 서로 겹친다(한 문서가 여러 결함). 합산하지 말 것.
        '정형 키워드 3종' 은 표본을 읽고 고른 7개 낱말이다 → 하한선이지 총계가 아니다.
        '단어·문장 단순 조합' 은 낱말 통계(문서 내 문장 중복·타 문서와 같은 문장)로는 거의 안 잡힌다.
    [B] 제외 후 잔량 — 결함을 통째로 제외하면 등급별로 몇 건이 남는가.
    [C] 정밀도·재현율·F1 — reports/phase1_cv/fold*/preds.json(τ=0.30, 사전 등록 값) 전량과
        본문 등급명 낱말 유무 층화. 학습 분포 내 5분할 교차검증 값이다(독립 셋의 절대 성능이 아니다).
"""

from __future__ import annotations

import collections
import json
import re
import sys
from pathlib import Path

POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(POC / "src"))
sys.stdout.reconfigure(encoding="utf-8")

G = ["TS", "S1", "S2", "S3"]
RANK = {g: i for i, g in enumerate(G)}
STOCK_KEYWORDS = ["공정 노하우", "원가 구조", "고객 데이터베이스", "마케팅 전략", "분기 매출 데이터", "거래처 명단", "사업 계획"]
COURT = re.compile(r"【(주\s*문|이\s*유|원\s*고|청구취지|상\s*고\s*인)】")
DATA = POC / "datasets" / "labeled_p1_v5_clean_s3fix"
CV = POC / "reports" / "phase1_cv"


def load_docs() -> list[dict]:
    rows = []
    for sp in ("train", "val", "test"):
        for line in (DATA / f"{sp}.jsonl").open(encoding="utf-8"):
            rows.append(json.loads(line))
    return rows


def sentences(text: str) -> list[str]:
    t = re.sub(r"\s+", " ", text)
    return [s.strip() for s in re.split(r"(?<=[.。!?…])\s+|(?<=다\.)|\n", t) if len(s.strip()) >= 8]


def title_echo(text: str) -> bool:
    """첫 줄이 잘린 조각이고 바로 뒤 본문이 그것을 되풀이한다(제목 추출 결함)."""
    parts = [p for p in re.split(r"\n\s*\n|\n", text) if p.strip()]
    if len(parts) < 2:
        return False
    a, b = parts[0].strip(), parts[1].strip()
    return len(a) >= 6 and b.startswith(a[: max(6, len(a) - 1)]) and a != b and len(a) < len(b)


def defects() -> None:
    import numpy as np  # noqa: PLC0415
    from sklearn.feature_extraction.text import TfidfVectorizer  # noqa: PLC0415

    from koipa.proxy_corpus import SYNTHETIC, _quality_errors  # noqa: PLC0415
    from koipa.services.synth_quality import _exposes_grade_token  # noqa: PLC0415

    rows = load_docs()
    n = len(rows)
    print(f"[A] 결함 유형별 건수 (분모 {n}건 = v5_clean 전량, 등급 {dict(collections.Counter(r['label'] for r in rows))})")
    tfidf = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=2, sublinear_tf=True, max_features=200000)
    sim = (lambda x: (x @ x.T).tocsr())(tfidf.fit_transform([r["text"] for r in rows]))
    sim.setdiag(0)
    nn = np.asarray(sim.max(axis=1).todense()).ravel()

    floor_fail = collections.Counter()
    floor_any = 0
    for r, m in zip(rows, nn):
        t = r["text"]
        errs = _quality_errors(t, origin=SYNTHETIC)
        floor_any += bool(errs)
        for e in errs:
            floor_fail[e.split(":")[1]] += 1
        ss = sentences(t)
        r["f"] = {
            "exposed": _exposes_grade_token(t),
            "court": bool(COURT.search(t)),
            "title_echo": title_echo(t),
            "near_dup": bool(m >= 0.9),
            "short": len(t) < 150 or len(ss) < 2,
            "stock3": sum(k in t for k in STOCK_KEYWORDS) >= 3,
            "in_doc_dup": bool(ss) and (len(ss) - len(set(ss))) / len(ss) >= 0.2,
        }
    print(f"  기존 품질 하한(proxy_corpus.SYNTHETIC_QUALITY_POLICY) 탈락 {floor_any}/{n} = {floor_any / n:.1%}  지표별 {floor_fail.most_common(4)}")
    names = {
        "exposed": "본문에 등급명 낱말(정답 노출)",
        "court": "판결문 서식(【주문】 등)",
        "title_echo": "제목줄이 잘려 본문에 되풀이",
        "stock3": "정형 키워드 3종 이상 동시 등장(하한선)",
        "near_dup": "근접 복제(최근접 유사도≥0.9)",
        "short": "150자 미만 또는 문장 2개 미만",
        "in_doc_dup": "문서 안 문장 중복≥20%",
    }
    for k, label in names.items():
        sel = [r for r in rows if r["f"][k]]
        c = collections.Counter(r["label"] for r in sel)
        print(f"  {label}: {len(sel)} ({len(sel) / n:.1%})  " + " ".join(f"{g}={c[g]}" for g in G))

    print("\n[B] 제외 후 잔량")

    def left(title: str, keys: tuple[str, ...]) -> None:
        keep = [r for r in rows if not any(r["f"][k] for k in keys)]
        c = collections.Counter(r["label"] for r in keep)
        print(f"  {title}: {len(keep)}/{n} = {len(keep) / n:.1%}  " + " ".join(f"{g}={c[g]}" for g in G))

    form = ("title_echo", "short", "near_dup")
    left("형태·중복만 제외", form)
    left("+ 판결문 서식 제외", form + ("court",))
    left("+ 등급명 노출 제외(생성 게이트 기준)", form + ("court", "exposed"))
    left("[참고] 등급명 노출만 제외", ("exposed",))


def prf(recs: list[dict], name: str) -> None:
    conf = collections.Counter((r["label"], r["pred"]) for r in recs)
    print(f"\n  [{name}] n={len(recs)}  정답 " + " ".join(f"{g}={sum(1 for r in recs if r['label'] == g)}" for g in G))
    ps, rs, fs = [], [], []
    for g in G:
        tp = conf[(g, g)]
        fp = sum(conf[(a, g)] for a in G if a != g)
        fn = sum(conf[(g, b)] for b in G if b != g)
        p = tp / (tp + fp) if tp + fp else 0.0
        r_ = tp / (tp + fn) if tp + fn else 0.0
        f = 2 * p * r_ / (p + r_) if p + r_ else 0.0
        ps.append(p), rs.append(r_), fs.append(f)
        print(f"    {g}: 정밀도 {p:.1%} 재현율 {r_:.1%} F1 {f:.1%} (정답 {tp + fn}건)")
    hi = [r for r in recs if r["label"] in ("TS", "S1")]
    miss = sum(1 for r in hi if RANK[r["pred"]] > RANK[r["label"]])
    acc = sum(conf[(g, g)] for g in G) / len(recs)
    print(f"    macro 정밀도 {sum(ps) / 4:.1%} 재현율 {sum(rs) / 4:.1%} F1 {sum(fs) / 4:.1%} | 정확도 {acc:.1%} | 고등급 미탐 {miss}/{len(hi)} = {miss / len(hi):.1%}")


def scores() -> None:
    from koipa.services.synth_quality import _exposes_grade_token  # noqa: PLC0415

    preds, texts = [], []
    for k in range(5):
        preds += json.loads((CV / f"fold{k}" / "preds.json").read_text(encoding="utf-8"))
        texts += [json.loads(line)["text"] for line in (CV / f"fold{k}" / "test.jsonl").open(encoding="utf-8")]
    assert len(preds) == len(texts)
    for r, t in zip(preds, texts):
        r["exp"] = _exposes_grade_token(t)
    print("\n[C] 등급별 정밀도·재현율·F1 (τ=0.30, 5분할 교차검증 보류 예측 전량 — 학습 분포 내 값)")
    prf(preds, "전체")
    prf([r for r in preds if r["exp"]], "본문에 등급명 낱말이 있는 문서")
    prf([r for r in preds if not r["exp"]], "본문에 등급명 낱말이 없는 문서")


if __name__ == "__main__":
    defects()
    scores()
