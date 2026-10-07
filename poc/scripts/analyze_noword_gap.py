#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""등급명 낱말이 없는 문서에서 재현율이 낮은 이유를 층화(stratification)로 좁히는 읽기 전용 분석.

배경(2026-09-20 기록): 1차 통합테스트(run_phase1_cv.py) 보류 예측 2,554건 중 본문에 등급명 낱말이 든 문서는
재현율 96.7%, 없는 문서는 74.6%. 낱말을 지워도 예측이 안 바뀌므로(커밋 fd01dcf8) 낱말이 원인이 아니다.
이 도구는 "무엇이 격차와 함께 움직이는가"를 축별로 재고, 축을 통제한 뒤 격차가 얼마 남는지만 보고한다.
**원인을 단정하지 않는다.** 층화는 관찰 데이터의 비교이며 겹치는 층이 없으면 추정 자체가 불가능하다.

입력(전부 읽기 전용, CPU 전용 — GPU·모델 학습·임베딩 모델 로딩 없음)
  reports/phase1_cv/fold{0..4}/test.jsonl   보류 문서 본문·메타(preds.json 과 같은 순서)
  reports/phase1_cv/fold{0..4}/preds.json   서빙 경로 예측(τ=0.30) · 평가 라벨(s3fix 정정본)
  reports/phase1_cv/fold{0..4}/train.jsonl  분할별 학습 문서(글자 TF-IDF 이웃·출처 비중 계산용)
  reports/phase1_cv/prereg.json · aggregate_2026-09-20.txt
  낱말 판정 = koipa.services.synth_quality._grade_term_pattern (NFKC 정규화 후, 직접 만든 정규식 없음)

사용:  poc/.venv/Scripts/python.exe scripts/analyze_noword_gap.py            (결과를 표준출력으로, 약 3~5분)
       poc/.venv/Scripts/python.exe scripts/analyze_noword_gap.py --out FILE  (같은 내용을 파일에도 저장)
       --force   9/20 수치를 재현하지 못해도 분석을 진행한다(기본은 멈춘다)
       --boot N  부트스트랩·순열 반복 수(기본 1000)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import unicodedata
from collections import Counter
from math import sqrt
from pathlib import Path

os.environ.setdefault("TESTING", "1")
import numpy as np  # noqa: E402

POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(POC / "src"))
CV = POC / "reports" / "phase1_cv"
K = 5
G = ["TS", "S1", "S2", "S3"]
GI = {g: i for i, g in enumerate(G)}
RANK = {"S3": 0, "S2": 1, "S1": 2, "TS": 3}
SEED = 20260922
MIN_CELL = 30          # 셀 크기 n<30 은 표에 † 로 표시하고 해석하지 않는다
MIN_REF = 10           # 참고용(해석 안 함) 층화에서 쓰는 더 느슨한 하한

# 2026-09-20 기록(메모리 target-feasibility-review-curve): 재현 대상
EXPECT = {"n_w": 1264, "n_n": 1290, "macro_w": 0.967, "macro_n": 0.746,
          "rec_w": {"TS": 0.997, "S1": 0.990, "S2": 0.995, "S3": 0.885},
          "rec_n": {"TS": 0.837, "S1": 0.625, "S2": 0.714, "S3": 0.806},
          "n_n_grade": {"TS": 129, "S1": 40, "S2": 126, "S3": 995},
          "hi_w": (1, 775), "hi_n": (31, 169)}

# 출력은 절 단위로 모았다가 마지막에 정해진 순서로 내보낸다(자동 요약을 앞에 두려고).
SECTIONS: dict[str, list[str]] = {}
_CUR = ["head"]
ORDER = ["head", "repro", "summary", "gap", "axes", "table", "conc", "strat", "overlap", "base", "err", "conf", "limits"]


def sec(name: str) -> None:
    _CUR[0] = name
    SECTIONS.setdefault(name, [])


def P(*a) -> None:
    SECTIONS.setdefault(_CUR[0], []).append(" ".join(str(x) for x in a))


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def pc(x: float, w: int = 5, d: int = 1) -> str:
    return "-".rjust(w + 1) if x is None or x != x else f"{x * 100:{w}.{d}f}%"


def pp(x: float, w: int = 5) -> str:
    return "-".rjust(w + 2) if x is None or x != x else f"{x * 100:+{w}.1f}p"


# ───────────────────────────── 자료 읽기 ─────────────────────────────
def load_jsonl(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def load_records() -> list[dict]:
    from koipa.services.synth_quality import _grade_term_pattern  # noqa: PLC0415

    pat = _grade_term_pattern()
    recs: list[dict] = []
    for k in range(K):
        rows = load_jsonl(CV / f"fold{k}" / "test.jsonl")
        preds = json.loads((CV / f"fold{k}" / "preds.json").read_text(encoding="utf-8"))
        assert len(rows) == len(preds), f"fold{k}: test {len(rows)} != preds {len(preds)}"
        for r, p in zip(rows, preds):
            assert r.get("label_source") == p["source"], f"fold{k}: test.jsonl 과 preds.json 순서 불일치"
            r = dict(r)
            r["fold"] = k
            r["truth"] = p["label"]                      # 평가 라벨(s3fix)
            r["pred"] = p["pred"]                        # 서빙 경로 τ=0.30 예측
            r["scores"] = p["scores"]
            r["maxp"] = max(p["scores"].values())
            r["w"] = bool(pat.search(unicodedata.normalize("NFKC", r["text"])))
            recs.append(r)
    return recs


def mask_terms(text: str) -> str:
    """run_phase1_cv.mask_terms 와 같다: NFKC 후 등급명 낱말 삭제."""
    from koipa.services.synth_quality import _grade_term_pattern  # noqa: PLC0415

    return _grade_term_pattern().sub("", unicodedata.normalize("NFKC", text or ""))


def near_dup_groups(texts: list[str]) -> tuple[np.ndarray, int]:
    """run_phase1_cv.prepare() 와 같은 묶음(글자 TF-IDF 코사인 ≥ 0.95 연결요소) — 부트스트랩을 묶음 단위로 하기 위해."""
    from scipy.sparse import csr_matrix  # noqa: PLC0415
    from scipy.sparse.csgraph import connected_components  # noqa: PLC0415
    from sklearn.feature_extraction.text import TfidfVectorizer  # noqa: PLC0415

    x = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=2, sublinear_tf=True, max_features=200000).fit_transform(texts)
    rows, cols = [], []
    for i in range(0, x.shape[0], 400):
        sim = (x[i:i + 400] @ x.T).toarray()
        for a, b in zip(*np.where(sim >= 0.95)):
            rows.append(i + a)
            cols.append(b)
    n, lab = connected_components(csr_matrix((np.ones(len(rows)), (rows, cols)), shape=(len(texts),) * 2), directed=False)
    return lab, int(n)


def _neighbors(train_texts: list[str], ytrain: np.ndarray, held_texts: list[str], truths: list[str]) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """글자 TF-IDF(분할별 train 에만 맞춘 벡터라이저 — 비지도) 최근접 이웃. 모델 학습이 아니라 조회다."""
    from sklearn.feature_extraction.text import TfidfVectorizer  # noqa: PLC0415

    vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=2, sublinear_tf=True, max_features=200000)
    xt = vec.fit_transform(train_texts)
    sim = (vec.transform(held_texts) @ xt.T).toarray()
    top = np.argsort(-sim, axis=1)[:, :10]
    nn = sim[np.arange(len(held_texts)), top[:, 0]]
    agree = np.array([np.mean(ytrain[top[i]] == truths[i]) for i in range(len(held_texts))])     # 최근접 10개 중 정답 등급과 같은 라벨 비율
    pred = []
    for i in range(len(held_texts)):                                                              # 최근접 5개 유사도 가중 다수결
        sc = {g: 0.0 for g in G}
        for j in top[i, :5]:
            sc[ytrain[j]] += float(sim[i, j])
        pred.append(max(sc, key=sc.get))
    return nn, agree, pred


def add_train_relative_features(recs: list[dict]) -> None:
    """분할별 학습 문서(train.jsonl)에 대한 상대 특성 — 같은 출처·도메인 비중, 출처·도메인 안 등급 비율, 글자 TF-IDF 이웃.

    이웃 특성은 두 벌: 원문(raw)과 낱말을 지운 본문(mask). 축으로는 mask 판을 쓴다 — 낱말 자체가 이웃을 정하는 것을 막기 위해.
    """
    for k in range(K):
        tr = load_jsonl(CV / f"fold{k}" / "train.jsonl")
        idx = [i for i, r in enumerate(recs) if r["fold"] == k]
        n_tr = len(tr)
        by_src, by_dom = Counter(t["label_source"] for t in tr), Counter(t["domain"] for t in tr)
        by_cell = Counter((t["label_source"], t["label"]) for t in tr)
        by_dcell = Counter((t["domain"], t["label"]) for t in tr)
        ylab = np.array([t["label"] for t in tr])
        truths = [recs[i]["truth"] for i in idx]
        raw = _neighbors([t["text"] for t in tr], ylab, [recs[i]["text"] for i in idx], truths)
        msk = _neighbors([mask_terms(t["text"]) for t in tr], ylab, [mask_terms(recs[i]["text"]) for i in idx], truths)
        for row, i in enumerate(idx):
            r = recs[i]
            ls, dm, g = r["label_source"], r["domain"], r["truth"]
            r["src_share"] = by_src.get(ls, 0) / n_tr
            r["p_lab_src"] = by_cell.get((ls, g), 0) / by_src[ls] if by_src.get(ls) else 0.0
            r["p_lab_dom"] = by_dcell.get((dm, g), 0) / by_dom[dm] if by_dom.get(dm) else 0.0
            r["nn_sim"], r["knn_agree"], r["knn_pred"] = float(msk[0][row]), float(msk[1][row]), msk[2][row]
            r["knn_pred_raw"] = raw[2][row]
        log(f"  분할 {k} 이웃 특성 완료")


def cramers_v(a: list, b: list) -> float:
    la, lb = sorted(set(a), key=str), sorted(set(b), key=str)
    if len(la) < 2 or len(lb) < 2:
        return 0.0
    ia, ib = {v: i for i, v in enumerate(la)}, {v: i for i, v in enumerate(lb)}
    t = np.zeros((len(la), len(lb)))
    for x, y in zip(a, b):
        t[ia[x], ib[y]] += 1
    n = t.sum()
    e = t.sum(1, keepdims=True) * t.sum(0, keepdims=True) / n
    chi = float(((t - e) ** 2 / np.where(e > 0, e, 1)).sum())
    return sqrt(chi / (n * (min(len(la), len(lb)) - 1)))


# ───────────────────────────── 축 정의 ─────────────────────────────
def qbins(x: np.ndarray, k: int, fmt: str = "{:.0f}") -> tuple[np.ndarray, list[str]]:
    """전체 문서 기준 분위수 구간. 동점으로 경계가 겹치면 합친다."""
    edges = np.unique(np.quantile(x, np.linspace(0, 1, k + 1)[1:-1]))
    lv = np.searchsorted(edges, x, side="left")
    names = []
    for i in range(len(edges) + 1):
        if i == 0:
            names.append(f"≤{fmt.format(edges[0])}")
        elif i == len(edges):
            names.append(f">{fmt.format(edges[-1])}")
        else:
            names.append(f"{fmt.format(edges[i - 1])}<x≤{fmt.format(edges[i])}")
    return lv, names


def build_axes(recs: list[dict]) -> dict[str, dict]:
    """축 목록. 각 축 = {key, title, group, lv(문서별 수준 번호), names(수준 이름), table(4절 표 출력 여부), note}."""
    ax: dict[str, dict] = {}

    def cat(key, title, group, values, table=True, note="", order=None):
        vals = [str(v) for v in values]
        cnt = Counter(vals)
        names = order or sorted(cnt, key=lambda s: (-cnt[s], s))
        m = {v: i for i, v in enumerate(names)}
        ax[key] = {"key": key, "title": title, "group": group, "lv": np.array([m[v] for v in vals]), "names": names, "table": table, "note": note}

    def num(key, title, group, arr, k, fmt="{:.0f}", table=True, note=""):
        lv, names = qbins(np.asarray(arr, dtype=float), k, fmt)
        ax[key] = {"key": key, "title": title, "group": group, "lv": lv, "names": names, "table": table, "note": note}

    g = lambda f: [r.get(f) for r in recs]  # noqa: E731
    # ① 저장된 메타데이터 — 출처·유형
    cat("label_source", "라벨 출처(label_source)", "메타", g("label_source"))
    cat("source", "문서 출처(source)", "메타", g("source"))
    cat("origin_dataset", "원천 데이터셋(origin_dataset)", "메타", g("origin_dataset"))
    cat("domain", "도메인(domain — 문서 유형에 가장 가까운 저장 필드)", "메타", g("domain"))
    cat("dom_vocab", "도메인 표기 방식(영문 코드=business·tech 등 ASCII / 한글 도메인명) — 표를 본 뒤 만든 사후 축, 기술용", "메타(사후)",
        ["영문코드" if str(r.get("domain")).isascii() else "한글명" for r in recs], note="사후 축")
    cat("is_court", "판결문 여부(저장 필드 is_court)", "메타", g("is_court"))
    cat("relabel", "판결문 정책 재라벨(relabel_basis 있음 = 원 라벨 TS/S1/S2 → S3)", "메타", ["재라벨" if r.get("relabel_basis") else "해당없음" for r in recs])
    cat("long_doc", "긴 문서 표지(long_doc, head_tail 절단)", "메타", ["긴문서" if r.get("long_doc") else "보통" for r in recs])
    cat("collision", "라벨 충돌 해소 문서(label_collision 있음)", "메타", ["충돌해소" if r.get("label_collision") else "해당없음" for r in recs])
    cat("dup_count", "중복 수(dup_count)", "메타", g("dup_count"), table=False, note="판결문 여부와 겹침")
    # ② 본문에서 계산
    ln = np.array([len(r["text"]) for r in recs])
    num("len_q5", "글자 수(분위)", "본문", ln, 5)
    num("len_q10", "글자 수(10분위 — 구간 수 민감도 확인)", "본문", ln, 10, table=False)
    para = np.array([len([x for x in r["text"].splitlines() if x.strip()]) for r in recs])
    num("para_q", "문단(비어 있지 않은 줄) 수(분위)", "본문", para, 4)

    def hangul_share(t: str) -> float:
        al = [c for c in t if c.isalpha()]
        return sum("가" <= c <= "힣" for c in al) / len(al) if al else 0.0

    hs = np.array([hangul_share(r["text"]) for r in recs])
    cat("lang", "언어(한글 비율: 영어<0.1 · 혼합 · 한국어≥0.9)", "본문", ["영어" if h < 0.1 else ("한국어" if h >= 0.9 else "혼합") for h in hs], order=["한국어", "혼합", "영어"])
    # ③ 학습 분할 대비 상대 특성
    num("src_share", "학습셋 안 같은 label_source 비중(분위)", "학습 대비", [r["src_share"] for r in recs], 5, "{:.3f}")
    num("p_lab_src", "학습셋 안 같은 label_source 에서 그 등급이 차지하는 비율(분위)", "학습 대비", [r["p_lab_src"] for r in recs], 5, "{:.3f}")
    num("p_lab_dom", "학습셋 안 같은 도메인에서 그 등급이 차지하는 비율(분위)", "학습 대비", [r["p_lab_dom"] for r in recs], 5, "{:.3f}")
    num("nn_sim", "학습 문서 최근접 글자 TF-IDF 코사인(분위, 낱말 지운 본문)", "학습 대비", [r["nn_sim"] for r in recs], 5, "{:.2f}")
    ag = np.array([r["knn_agree"] for r in recs])
    lv = np.digitize(ag, [0.55, 0.85, 0.95])
    ax["knn_agree"] = {"key": "knn_agree", "title": "최근접 10개 학습 문서 중 정답 등급 라벨 비율(낱말 지운 본문)", "group": "학습 대비", "lv": lv,
                       "names": ["≤0.5", "0.6~0.8", "0.9", "1.0"], "table": True, "note": ""}
    # ④ 모델 출력 — 통제가 아니라 기술(descriptive): 결과에서 나온 값이므로 원인 통제로 쓰지 않는다
    num("maxp", "모델 최대 확률(확신도, 분위) — 모델 출력이라 통제 아님", "모델 출력", [r["maxp"] for r in recs], 5, "{:.3f}", note="결과 변수의 일부")
    cat("fold", "교차검증 분할 번호", "실행", g("fold"), order=[str(i) for i in range(K)])
    # ⑤ 저장 필드 중 다른 축과 겹치는 것(표는 생략, 층화만)
    for f, t in (("tier", "tier"), ("document_origin", "document_origin"), ("rule_grade", "rule_grade"), ("review_status", "review_status"),
                 ("sample_weight", "sample_weight(학습 가중치)")):
        cat(f, t, "메타(중복)", g(f), table=False)
    return ax


def combine(axes: dict[str, dict], keys: list[str], label: str) -> dict:
    """여러 축의 교차 층. 수준 이름은 '이름 / 이름 …'."""
    n = len(next(iter(axes.values()))["lv"])
    tup = [tuple(axes[k]["names"][axes[k]["lv"][i]] for k in keys) for i in range(n)]
    uniq = sorted(set(tup))
    m = {t: i for i, t in enumerate(uniq)}
    return {"key": label, "title": " × ".join(keys), "lv": np.array([m[t] for t in tup]), "names": [" / ".join(t) for t in uniq], "table": False, "note": "", "group": "교차"}


# ───────────────────────────── 지표 ─────────────────────────────
def rec_table(g: np.ndarray, ok: np.ndarray, mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    return np.bincount(g[mask], minlength=4), np.bincount(g[mask & ok], minlength=4)


def macro(n: np.ndarray, c: np.ndarray) -> float:
    have = n > 0
    return float(np.mean(c[have] / n[have])) if have.any() else float("nan")


class Strat:
    """등급 × 축 수준 층. 낱말 있음(W)·없음(N) 양쪽에 문서가 하한 m 이상인 층만 '지지'된다."""

    def __init__(self, g: np.ndarray, w: np.ndarray, lv: np.ndarray, m: int):
        self.S = int(lv.max()) + 1
        self.cid = g * self.S + lv
        self.g, self.w, self.m, self.lv = g, w, m, lv
        self.nw0 = np.bincount(self.cid[w], minlength=4 * self.S).reshape(4, self.S)
        self.nn0 = np.bincount(self.cid[~w], minlength=4 * self.S).reshape(4, self.S)
        self.supp = (self.nw0 >= m) & (self.nn0 >= m)

    def stats(self, y: np.ndarray, w: np.ndarray | None = None, wt: np.ndarray | None = None) -> dict:
        """y: 문서별 성공(재현) 여부. 등급별 (같은집합 crude 격차, 층화 격차) — 지지된 층만, N 문서 수로 가중."""
        w = self.w if w is None else w
        S4 = 4 * self.S
        bc = lambda mask: np.bincount(self.cid[mask], weights=None if wt is None else wt[mask], minlength=S4).reshape(4, self.S)  # noqa: E731
        nw, cw, nn, cn = bc(w), bc(w & y), bc(~w), bc(~w & y)
        crude, std = np.full(4, np.nan), np.full(4, np.nan)
        for gi in range(4):
            s = self.supp[gi] & (nw[gi] > 0) & (nn[gi] > 0)
            if not s.any():
                continue
            a, b, c_, d = nw[gi][s], cw[gi][s], nn[gi][s], cn[gi][s]
            crude[gi] = b.sum() / a.sum() - d.sum() / c_.sum()
            std[gi] = (c_ * (b / a - d / c_)).sum() / c_.sum()
        return {"crude": crude, "std": std}

    def cover(self, grades: slice = slice(0, 4)) -> float | np.ndarray:
        """N 문서 중 지지된 층에 든 비율(등급별 배열 또는 grades 합산)."""
        if grades == slice(0, 4):
            return np.array([self.nn0[gi][self.supp[gi]].sum() / self.nn0[gi].sum() if self.nn0[gi].sum() else float("nan") for gi in range(4)])
        return float(self.nn0[grades][self.supp[grades]].sum() / self.nn0[grades].sum())


def macro_gap(v: np.ndarray) -> float:
    return float(np.nanmean(v)) if np.any(~np.isnan(v)) else float("nan")


def high_miss_gap(st: Strat, down: np.ndarray) -> tuple[float, float]:
    """고등급(TS·S1) 미탐률 격차 = N 미탐률 − W 미탐률. (crude, 층화) — 지지된 층만, TS·S1 합쳐 N 문서 수로 가중."""
    S4 = 4 * st.S
    bc = lambda mask: np.bincount(st.cid[mask], minlength=S4).reshape(4, st.S)  # noqa: E731
    nw, cw, nn, cn = bc(st.w), bc(st.w & down), bc(~st.w), bc(~st.w & down)
    sel = np.zeros((4, st.S), dtype=bool)
    sel[:2] = st.supp[:2]
    if not sel.any():
        return float("nan"), float("nan")
    a, b, c_, d = nw[sel], cw[sel], nn[sel], cn[sel]
    return float(d.sum() / c_.sum() - b.sum() / a.sum()), float((c_ * (d / c_ - b / a)).sum() / c_.sum())


def boot_and_perm(st: Strat, ok: np.ndarray, gid: np.ndarray, ngrp: int, B: int, rng: np.random.Generator) -> dict:
    """층화 격차의 (a) 묶음 단위 부트스트랩 95% 구간, (b) 층 안에서 낱말 표지를 섞은 순열 기준선."""
    obs = macro_gap(st.stats(ok)["std"])
    bs = np.empty(B)
    for b in range(B):
        cnt = np.bincount(rng.integers(0, ngrp, ngrp), minlength=ngrp)
        bs[b] = macro_gap(st.stats(ok, wt=cnt[gid].astype(float))["std"])
    order = np.argsort(st.cid, kind="stable")
    perm = np.empty(B)
    n = len(ok)
    for b in range(B):
        o1 = np.lexsort((rng.random(n), st.cid))
        wp = np.empty(n, dtype=bool)
        wp[o1] = st.w[order]
        perm[b] = macro_gap(st.stats(ok, w=wp)["std"])
    bs, perm = bs[~np.isnan(bs)], perm[~np.isnan(perm)]
    nan2 = (float("nan"), float("nan"))
    return {"obs": obs, "ci": (float(np.quantile(bs, 0.025)), float(np.quantile(bs, 0.975))) if len(bs) else nan2,
            "null_mean": float(perm.mean()) if len(perm) else float("nan"),
            "null_rng": (float(np.quantile(perm, 0.025)), float(np.quantile(perm, 0.975))) if len(perm) else nan2,
            "p": float((np.abs(perm) >= abs(obs) - 1e-12).mean()) if len(perm) else float("nan")}


def cell(n: int, c: int) -> str:
    if n == 0:
        return "-".center(13)
    return (f"{c / n * 100:5.1f}%({n})" + ("†" if n < MIN_CELL else " ")).rjust(13)


# ───────────────────────────── 절별 출력 ─────────────────────────────
def qualification(recs: list[dict]) -> None:
    sec("head")
    ls = Counter(r["label_source"] for r in recs)
    P("등급명 낱말 유무 재현율 격차 층화 분석 (읽기 전용 · CPU 전용) — 도구 scripts/analyze_noword_gap.py")
    P("=" * 100)
    P("0. 이 셋의 자격 (수치보다 먼저)")
    P("=" * 100)
    P("· 셋 = 1차 통합테스트(scripts/run_phase1_cv.py) 보류 예측 %d건. v5_clean 을 글자 TF-IDF 코사인 ≥0.95 근접중복 묶음 단위로 5분할한 교차검증," % len(recs))
    P("  분할마다 학습한 모델이 자기 보류분만 예측했다. 검수 없음. 학습 분포 안이다 — 독립 생성 문서의 성능이 아니다.")
    P("· 평가 라벨 = s3fix 정정본(LLM 이 규칙 S3 를 덮어쓴 65행을 S3 로 바로잡음; 정정 주체는 AI 정독이며 사람 서명이 아니다).")
    P("  학습 라벨 = 원본. 예측 = 서빙 경로 pipe.run, escalation τ=0.30 고정(사전 등록, prereg.json).")
    P("· 순수 합성이 아니다. label_source 구성: " + " · ".join(f"{k} {v}" for k, v in ls.most_common()))
    P("  locked_eval(사람 서명 실문서)이 없으므로 이 표의 어떤 값도 절대 성능 근거가 못 된다. 아래는 셋 안의 상대 비교(층화)뿐이다.")
    P("· '낱말 있음/없음' 은 관찰된 문서 표지이지 실험으로 조작한 처치가 아니다. 낱말을 지워도 예측이 안 바뀐다는 반증(커밋 fd01dcf8)이 이미 있다.")
    P("  따라서 아래 층화 결과는 '낱말 효과'를 뜻하지 않는다. 층화 후 남은 격차 = 통제한 축으로 설명되지 않은 부분이며,")
    P("  그 남은 부분이 무엇 때문인지는 이 분석으로 말할 수 없다(측정하지 않은 문서 특성이 남아 있을 수 있다).")
    P("· 셀 크기 n<30 은 † 로 표시하고 해석하지 않는다. 층화의 '지지 층' = 낱말 있음·없음 양쪽 모두 n≥30 인 (등급×축 수준) 층.")
    P("")
    P("[입력 지문 — reports/ 는 git 이 추적하지 않으므로 산출물에 남긴다]")
    from koipa.services.synth_quality import _grade_term_pattern  # noqa: PLC0415

    try:
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=str(POC), capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception as exc:  # noqa: BLE001
        head = f"확인 실패({exc})"
    P(f"  git HEAD {head} · 낱말 정규식 sha256[:16] {hashlib.sha256(_grade_term_pattern().pattern.encode('utf-8')).hexdigest()[:16]}")
    P(f"  prereg.json {sha(CV / 'prereg.json')} · aggregate_2026-09-20.txt {sha(CV / 'aggregate_2026-09-20.txt')}")
    for k in range(K):
        d = CV / f"fold{k}"
        P(f"  fold{k}: test.jsonl {sha(d / 'test.jsonl')} · preds.json {sha(d / 'preds.json')} · train.jsonl {sha(d / 'train.jsonl')}")


def reproduce(recs: list[dict]) -> bool:
    sec("repro")
    g = np.array([GI[r["truth"]] for r in recs])
    ok = np.array([r["pred"] == r["truth"] for r in recs])
    w = np.array([r["w"] for r in recs])
    P("")
    P("=" * 100)
    P("1. 재현 확인 — 9/20 기록(메모리 target-feasibility-review-curve-2026-09-20)과 같은가")
    P("=" * 100)
    n_all, c_all = rec_table(g, ok, np.ones(len(g), bool))
    P(f"  전체 {len(recs)}건: 4등급 평균 재현율 {pc(macro(n_all, c_all))} | " + " ".join(f"{G[i]} {pc(c_all[i] / n_all[i])}({n_all[i]})" for i in range(4)))
    res = {}
    for name, m in (("있음", w), ("없음", ~w)):
        n, c = rec_table(g, ok, m)
        res[name] = (n, c)
        P(f"  낱말 {name} {int(m.sum()):4d}건: 4등급 평균 재현율 {pc(macro(n, c))} | " + " ".join(f"{G[i]} {pc(c[i] / n[i])}({n[i]})" for i in range(4)))
    down = np.array([RANK[r["pred"]] < RANK[r["truth"]] for r in recs])
    hi = g <= 1
    hw, hn_ = (int((down & hi & w).sum()), int((hi & w).sum())), (int((down & hi & ~w).sum()), int((hi & ~w).sum()))
    P(f"  고등급(TS·S1) 미탐: 낱말 있음 {hw[0]}/{hw[1]} · 낱말 없음 {hn_[0]}/{hn_[1]} · 전체 {int((down & hi).sum())}/{int(hi.sum())}")
    checks = [("낱말 있음 건수 1,264", int(w.sum()) == EXPECT["n_w"]), ("낱말 없음 건수 1,290", int((~w).sum()) == EXPECT["n_n"]),
              ("낱말 있음 재현율 96.7%", abs(macro(*res["있음"]) - EXPECT["macro_w"]) < 0.0006),
              ("낱말 없음 재현율 74.6%", abs(macro(*res["없음"]) - EXPECT["macro_n"]) < 0.0006),
              ("낱말 없음 등급별 n(129·40·126·995)", {G[i]: int(res["없음"][0][i]) for i in range(4)} == EXPECT["n_n_grade"]),
              ("낱말 있음 등급별 재현율(99.7·99.0·99.5·88.5)", all(abs(res["있음"][1][i] / res["있음"][0][i] - EXPECT["rec_w"][G[i]]) < 0.0006 for i in range(4))),
              ("낱말 없음 등급별 재현율(83.7·62.5·71.4·80.6)", all(abs(res["없음"][1][i] / res["없음"][0][i] - EXPECT["rec_n"][G[i]]) < 0.0006 for i in range(4))),
              ("고등급 미탐 1/775", hw == EXPECT["hi_w"]), ("고등급 미탐 31/169", hn_ == EXPECT["hi_n"])]
    for name, ok_ in checks:
        P(f"  [{'일치' if ok_ else '불일치'}] {name}")
    good = all(c for _, c in checks)
    P("  → " + ("9/20 수치를 그대로 재현한다. 분석을 진행한다." if good else "재현 실패 — 분석을 진행하지 않는다(--force 로 강제 가능)."))
    return good


def gap_overview(recs, g, ok, w, down, gid, ngrp, rng, B, grp_note) -> dict:
    sec("gap")
    P("")
    P("=" * 100)
    P("3. 격차의 크기와 구성 — 통제 없음(crude)")
    P("=" * 100)
    P("  " + grp_note)
    nW, cW = rec_table(g, ok, w)
    nN, cN = rec_table(g, ok, ~w)
    P("  등급별 재현율: 낱말 있음(W) 대 없음(N)")
    P("        W 재현율(n)      N 재현율(n)     격차(W−N)   N 의 비중(그 등급 안에서)")
    for i in range(4):
        P(f"   {G[i]} {cell(nW[i], cW[i])} {cell(nN[i], cN[i])}  {pp(cW[i] / nW[i] - cN[i] / nN[i])}     {nN[i] / (nW[i] + nN[i]) * 100:5.1f}%")
    crude = macro(nW, cW) - macro(nN, cN)
    P(f"  4등급 평균 격차 {pp(crude)}  (= 등급별 격차의 단순 평균)")
    bs = []
    for _ in range(B):
        cnt = np.bincount(rng.integers(0, ngrp, ngrp), minlength=ngrp)[gid].astype(float)
        a = [np.bincount(g[m], weights=cnt[m], minlength=4) for m in (w, w & ok, ~w, ~w & ok)]
        with np.errstate(all="ignore"):
            bs.append(np.nanmean(a[1] / a[0] - a[3] / a[2]))
    lo, hi_ = float(np.nanquantile(bs, 0.025)), float(np.nanquantile(bs, 0.975))
    P(f"  묶음 단위 부트스트랩 95% 구간(B={B}): {pp(lo)} ~ {pp(hi_)}")
    P("  ※ 4등급 평균은 등급별 재현율의 평균이라 등급 구성 차이는 이미 제거돼 있다. 등급 구성이 다른 것은 격차가 아니라 '어느 등급 문서가 낱말을 갖는가' 의 문제다:")
    P("    낱말 있음 = " + " · ".join(f"{G[i]} {int(nW[i])}" for i in range(4)) + " / 낱말 없음 = " + " · ".join(f"{G[i]} {int(nN[i])}" for i in range(4)))
    P("    낱말 없음 문서 중 S3 비율 %.1f%%, 낱말 있음 문서 중 S3 비율 %.1f%%." % (nN[3] / nN.sum() * 100, nW[3] / nW.sum() * 100))
    hi = g <= 1
    P(f"  고등급 미탐: W {int((down & hi & w).sum())}/{int((hi & w).sum())} = {pc((down & hi & w).sum() / (hi & w).sum())} · "
      f"N {int((down & hi & ~w).sum())}/{int((hi & ~w).sum())} = {pc((down & hi & ~w).sum() / (hi & ~w).sum())}")
    P("")
    P("  낱말 없음(N) 안에서 등급 TS·S1·S2 의 재현 실패 문서 — 그 문서들은 어디서 왔나")
    for i in range(3):
        m = (~w) & (g == i) & (~ok)
        P(f"   {G[i]}: 실패 {int(m.sum())}/{int(nN[i])} — " + " · ".join(f"{k} {v}" for k, v in Counter(recs[j]["label_source"] for j in np.where(m)[0]).most_common()))
    P("  낱말 없음(N) 의 TS·S1·S2 문서 전체(%d건)의 label_source 구성: " % int(nN[:3].sum())
      + " · ".join(f"{k} {v}" for k, v in Counter(r["label_source"] for r, gg, ww in zip(recs, g, w) if not ww and gg < 3).most_common()))
    P("  낱말 있음(W) 의 TS·S1·S2 문서 전체(%d건)의 label_source 구성: " % int(nW[:3].sum())
      + " · ".join(f"{k} {v}" for k, v in Counter(r["label_source"] for r, gg, ww in zip(recs, g, w) if ww and gg < 3).most_common()))
    return {"crude": crude, "ci": (lo, hi_), "per": np.array([cW[i] / nW[i] - cN[i] / nN[i] for i in range(4)]), "nN": nN, "nW": nW}


def discarded_section(recs, axes, w, ok, g) -> None:
    sec("axes")
    P("")
    P("=" * 100)
    P("4. 사용한 축과 버린(표를 생략한) 축")
    P("=" * 100)
    P("  사용 = 5절 표 + 7절 층화 모두에 넣음 / 층화만 = 7절에만 넣고 5절 표는 생략(다른 축과 겹치거나 값이 거의 하나뿐) / 쓰지 않음 = 아예 뺐다. 이유를 함께 적는다.")
    P("  V = Cramér V(0~1, 연관 세기). 수준 수가 많은 축은 V 가 부풀 수 있다. 'N 실패 V' = 낱말 없는 TS·S1·S2 문서(재현 성공/실패)와의 연관.")
    ls = [r["label_source"] for r in recs]
    nn3 = (~w) & (g < 3)
    P(f"  {'구분':6s} {'축':15s} {'그룹':9s} {'수준':>4s} {'V(낱말유무)':>10s} {'V(label_source)':>15s} {'V(N 실패)':>9s}  설명")
    for a in axes.values():
        v_w = cramers_v(list(a["lv"]), list(w))
        v_l = cramers_v(list(a["lv"]), ls)
        v_f = cramers_v(list(a["lv"][nn3]), list(ok[nn3]))
        used = "사용" if a["table"] else "층화만"
        P(f"  {used:6s} {a['key']:15s} {a['group']:9s} {len(a['names']):4d} {v_w:10.2f} {v_l:15.2f} {v_f:9.2f}  {a['title']}" + (f"  ({a['note']})" if a["note"] else ""))
    P("  · [쓰지 않음] doc_id — 2,554건 전부 null 이라 정보 없음(문서 식별은 파일 순서로 함)")
    P("  · [쓰지 않음] origin_datasets(리스트 문자열) — origin_dataset 과 같은 정보")
    P("  · [쓰지 않음] label_original·relabel_basis·collision_resolved_by 원문 — 'relabel'·'collision' 축으로 요약")
    P("  · [쓰지 않음] scores(전체 확률) — 모델 출력. 확신도는 maxp 축으로만(기술용)")
    P("  · [쓰지 않음] 문서 주제·문체 같은 의미 축 — 이 분석에서는 계산하지 않았다(확인 안 함). 저장 필드와 길이·언어·이웃 구성으로만 갈랐다")
    P("  · [쓰지 않음] 이웃 특성의 원문(낱말 포함) 판 — 낱말 자체가 이웃을 정할 수 있어 축에는 낱말 지운 본문 판만 썼다(9절 기준선에서는 두 판을 모두 냈다)")
    P("  · label_source·source·origin_dataset·domain 등은 서로 강하게 겹친다(V 참조) — 한 축을 통제하면 겹치는 축도 함께 통제된다")


def axis_tables(recs, axes, g, ok, w) -> None:
    sec("table")
    P("")
    P("=" * 100)
    P("5. 축별 재현율 표 — 수준마다 낱말 있음(W)·없음(N) 두 줄, 등급별 재현율(n).  † = n<30 (해석하지 않음)")
    P("=" * 100)
    for a in axes.values():
        if not a["table"]:
            continue
        P("")
        P(f"[{a['key']}] {a['title']}" + (f"  · 주의: {a['note']}" if a["note"] else ""))
        P(f"   {'수준':26s} {'낱말':4s} {'n':>5s}  " + "  ".join(f"{gg:^13s}" for gg in G))
        for li, name in enumerate(a["names"]):
            sel = a["lv"] == li
            if not sel.any():
                continue
            for wl, wn in ((True, "있음"), (False, "없음")):
                m = sel & (w == wl)
                if not m.any():
                    P(f"   {(name[:26] if wl else ''):26s} {wn:4s} {0:5d}")
                    continue
                n, c = rec_table(g, ok, m)
                P(f"   {(name[:26] if wl else ''):26s} {wn:4s} {int(m.sum()):5d}  " + "  ".join(cell(int(n[i]), int(c[i])) for i in range(4)))


def concentration(recs, axes, g, ok, w) -> dict:
    """낱말 없는 TS·S1·S2 문서(등급 합산)의 실패가 어느 수준에 몰렸나 — 같은 수준의 낱말 있는 문서와 나란히."""
    sec("conc")
    P("")
    P("=" * 100)
    P("6. 낱말 없는 TS·S1·S2 문서의 실패 집중 — 등급을 합쳐(마이크로) 셀을 키운 요약. † = n<30")
    P("=" * 100)
    P("  N = 낱말 없는 TS·S1·S2 문서, W = 낱말 있는 TS·S1·S2 문서(같은 수준 안). 실패율 = 정답 등급으로 예측하지 못한 비율.")
    nn3, ww3 = (~w) & (g < 3), w & (g < 3)
    tot_fail = int((nn3 & ~ok).sum())
    P(f"  N 전체 {int(nn3.sum())}건 중 실패 {tot_fail}건({tot_fail / nn3.sum() * 100:.1f}%) · W 전체 {int(ww3.sum())}건 중 실패 {int((ww3 & ~ok).sum())}건({(ww3 & ~ok).sum() / ww3.sum() * 100:.1f}%)")
    out = {}
    for key in ("label_source", "long_doc", "len_q5", "domain", "knn_agree", "p_lab_dom", "relabel"):
        a = axes[key]
        P(f"\n  [{key}] {a['title']}")
        P(f"   {'수준':26s} {'N 문서':>7s} {'N 실패':>7s} {'N 실패율':>9s} {'실패 비중':>9s} | {'W 문서':>7s} {'W 실패율':>9s}")
        rows = []
        for li, name in enumerate(a["names"]):
            mn, mw = (a["lv"] == li) & nn3, (a["lv"] == li) & ww3
            if not mn.any():
                continue
            nf = int((mn & ~ok).sum())
            rows.append((name, int(mn.sum()), nf, nf / mn.sum(), nf / tot_fail if tot_fail else float("nan"), int(mw.sum()), (mw & ~ok).sum() / mw.sum() if mw.any() else float("nan")))
        out[key] = rows
        for name, n_n, nf, rate, share, n_w, rate_w in sorted(rows, key=lambda r: -r[2]):
            P(f"   {name[:26]:26s} {n_n:6d}{'†' if n_n < MIN_CELL else ' '} {nf:7d} {pc(rate, 8)} {pc(share, 8)} | {n_w:6d}{'†' if n_w < MIN_CELL else ' '} {pc(rate_w, 8)}")
    return out


def strat_summary(axes_list, g, ok, w, down, gid, ngrp, rng, B, m, title, ci: bool) -> list[dict]:
    P("")
    P(f"  [{title}] 지지 층 = 낱말 있음·없음 양쪽 n≥{m}.  격차 = 재현율(W) − 재현율(N), N 문서 수로 가중한 층화 평균.")
    P(f"   {'통제한 축':30s} {'지표':10s} {'TS':>7s} {'S1':>7s} {'S2':>7s} {'S3':>7s} | {'평균':>7s}  {'지지 등급':8s} 미탐격차(N−W, TS·S1)")
    out = []
    for a in axes_list:
        st = Strat(g, w, a["lv"], m)
        s = st.stats(ok)
        cov = st.cover()
        used = ~np.isnan(s["std"])
        mc, ms = macro_gap(s["crude"]), macro_gap(s["std"])
        hm_c, hm_s = high_miss_gap(st, down)
        name = a["key"] if len(a["key"]) <= 30 else a["key"][:29] + "…"
        P(f"   {name:30s} {'같은집합 crude':10s} " + " ".join(pp(x) for x in s["crude"]) + f" | {pp(mc)}  {int(used.sum())}/4      {pp(hm_c)}")
        P(f"   {'':30s} {'층화':10s} " + " ".join(pp(x) for x in s["std"]) + f" | {pp(ms)}            {pp(hm_s)}")
        P(f"   {'':30s} {'N 커버율':10s} " + " ".join(pc(x, 6, 0) for x in cov) + f" | 지지 층 {int(st.supp.sum())}개 · TS·S1·S2 합산 커버율 {pc(st.cover(slice(0, 3)), 4, 0)}")
        bp = None
        if ci and used.any():
            bp = boot_and_perm(st, ok, gid, ngrp, B, rng)
            P(f"   {'':30s} {'구간':10s} 층화 평균 {pp(bp['obs'])} · 부트스트랩 95% {pp(bp['ci'][0])}~{pp(bp['ci'][1])}"
              f" · 낱말 표지를 층 안에서 섞은 값 평균 {pp(bp['null_mean'])} (95% {pp(bp['null_rng'][0])}~{pp(bp['null_rng'][1])}) · 순열 p={bp['p']:.3f}")
        out.append({"key": a["key"], "crude": s["crude"], "std": s["std"], "cover": cov, "cover3": st.cover(slice(0, 3)), "used": used, "mc": mc, "ms": ms,
                    "supp": int(st.supp.sum()), "hm": (hm_c, hm_s), "bp": bp, "st": st, "names": a["names"]})
    return out


def supported_cells(a: dict, g, ok, w, m) -> None:
    st = Strat(g, w, a["lv"], m)
    rows = []
    for gi in range(4):
        for s in np.where(st.supp[gi])[0]:
            mk = (a["lv"] == s) & (g == gi)
            nw_, nn_ = int((mk & w).sum()), int((mk & ~w).sum())
            rw, rn = (mk & w & ok).sum() / nw_, (mk & ~w & ok).sum() / nn_
            rows.append(f"      {G[gi]} · {a['names'][s][:60]:60s} W {rw * 100:5.1f}%({nw_}) · N {rn * 100:5.1f}%({nn_}) · 격차 {pp(rw - rn)}")
    P(f"    [{a['key']}] 지지 층 명세" + ("" if rows else " — 없음"))
    for r in rows:
        P(r)


def stratified_section(recs, axes, g, ok, w, down, gid, ngrp, rng, B) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    sec("strat")
    P("")
    P("=" * 100)
    P("7. 축을 통제한 뒤 남는 격차 — 층화(등급 × 축 수준)")
    P("=" * 100)
    P("  읽는 법: '같은집합 crude' = 지지 층에 든 문서만으로 낸 통제 없는 격차, '층화' = 같은 문서를 층 안에서 비교해 N 비중으로 평균낸 격차.")
    P("  낱말 있는 문서의 재현율이 거의 모든 층에서 100% 근처라서 두 값은 거의 같다 — 이 표의 정보는 '어느 문서가 지지 층에 드는가(커버율)' 와 '지지 층 안의 격차' 다.")
    P("  'N 커버율' = 그 등급의 낱말 없는 문서 중 낱말 있는 같은 등급 문서와 같은 층에 든(n≥하한) 비율. 낮으면 대부분의 문서가 비교할 상대 없이 남는다.")
    P("  '-' = 지지 층이 없어 격차를 낼 수 없음(그 등급에 대해 그 축으로는 통제 비교가 불가능).")
    P("  '모델 출력' 축(maxp)은 예측 결과에서 나온 값이라 통제가 아니라 기술이다(층화 값을 원인 통제로 읽지 않는다).")
    ax_map = axes
    single = list(axes.values())
    main = strat_summary(single, g, ok, w, down, gid, ngrp, rng, B, MIN_CELL, f"단일 축, 셀 하한 n≥{MIN_CELL} (해석 대상)", ci=True)
    ref = strat_summary(single, g, ok, w, down, gid, ngrp, rng, B, MIN_REF, f"단일 축, 셀 하한 n≥{MIN_REF} (참고 — n<30 셀 포함, 해석하지 않음)", ci=False)
    P("")
    P("  [누적 통제] 축을 하나씩 더해 가며 같은 층화를 반복한다(셀 하한 n≥%d). 더할수록 층이 잘게 갈려 지지 층이 줄어든다." % MIN_CELL)
    chains = [
        ("① 라벨 출처", ["label_source"]),
        ("② +판결문 재라벨", ["label_source", "relabel"]),
        ("③ +긴 문서 표지", ["label_source", "relabel", "long_doc"]),
        ("④ +글자 수 분위", ["label_source", "relabel", "long_doc", "len_q5"]),
        ("⑤ +도메인", ["label_source", "relabel", "long_doc", "len_q5", "domain"]),
        ("⑥ +학습 내 도메인 안 등급 비율", ["label_source", "relabel", "long_doc", "len_q5", "domain", "p_lab_dom"]),
        ("⑦ +이웃 라벨 일치", ["label_source", "relabel", "long_doc", "len_q5", "domain", "p_lab_dom", "knn_agree"]),
    ]
    comb = [combine(ax_map, keys, title) for title, keys in chains]
    chain_res = strat_summary(comb, g, ok, w, down, gid, ngrp, rng, B, MIN_CELL, "누적 통제", ci=True)
    for t, c in zip(chains, chain_res):
        c["keys"] = t[1]
    P("")
    P("  [출처 안에서 다른 축 통제] label_source 를 고정한 채 축 하나를 더 통제한다(셀 하한 n≥%d). 같은 출처 안에서 길이·도메인·이웃 구성이 겹치는가를 본다." % MIN_CELL)
    pair_keys = ["domain", "dom_vocab", "is_court", "relabel", "long_doc", "collision", "len_q5", "para_q", "lang", "p_lab_dom", "nn_sim", "knn_agree"]
    pair = [combine(ax_map, ["label_source", k], f"출처 × {k}") for k in pair_keys]
    pair_res = strat_summary(pair, g, ok, w, down, gid, ngrp, rng, B, MIN_CELL, "출처 안에서 축 하나 추가", ci=False)
    P("")
    P("  [지지 층 명세] 위 층화가 실제로 비교한 층(셀 하한 n≥%d, 등급별). 이 목록이 통제 후 격차의 전부다." % MIN_CELL)
    for key in ("label_source", "long_doc", "knn_agree"):
        supported_cells(axes[key], g, ok, w, MIN_CELL)
    for c, (title, _keys) in zip(comb, chains):
        if title[0] in "②③":
            c["key"] = title
            supported_cells(c, g, ok, w, MIN_CELL)
    return main, chain_res, ref, pair_res


def overlap_section(recs, axes, g, ok, w) -> None:
    sec("overlap")
    P("")
    P("=" * 100)
    P("8. 겹침(overlap) 점검 — 낱말 없는 TS·S1·S2 문서가 놓인 층에 낱말 있는 문서가 있는가")
    P("=" * 100)
    P("  층화 비교는 같은 층에 W·N 이 함께 있어야 성립한다. 함께 없으면 그 문서들은 어떤 통제로도 비교할 수 없다(격차를 추정할 수 없음).")
    for key in ("label_source", "domain", "long_doc", "len_q5"):
        a = axes[key]
        P(f"\n  [{key}] 수준별 낱말 없음(N) 의 TS·S1·S2 문서 수 / 그 수준에 있는 낱말 있음(W) 의 같은 등급 문서 수")
        for li, name in enumerate(a["names"]):
            line, tot = [], 0
            for gi in range(3):
                nN = int(((a["lv"] == li) & ~w & (g == gi)).sum())
                nW = int(((a["lv"] == li) & w & (g == gi)).sum())
                tot += nN
                line.append(f"{G[gi]} N{nN:3d}/W{nW:3d}")
            if tot:
                P(f"    {name[:22]:22s} " + " · ".join(line))
    P("\n  synthetic_llm(가장 큰 출처) 안에서 낱말 있음·없음 문서의 길이·도메인 대조 — 같은 label_source 인데도 서로 다른 무리에서 왔는가")
    syn = [i for i, r in enumerate(recs) if r["label_source"] == "synthetic_llm"]
    for wl, wn in ((True, "낱말 있음"), (False, "낱말 없음")):
        for gi in range(4):
            idx = [i for i in syn if w[i] == wl and g[i] == gi]
            if not idx:
                continue
            ln = np.array([len(recs[i]["text"]) for i in idx])
            asc = sum(str(recs[i]["domain"]).isascii() for i in idx) / len(idx)
            P(f"    {wn} {G[gi]} n={len(idx):3d} · 글자 수 최소 {ln.min():5d} 중앙 {int(np.median(ln)):5d} 최대 {ln.max():5d} · 영문 코드 도메인 비율 {asc * 100:5.1f}%"
              f" · 도메인 상위 " + ",".join(f"{k}:{v}" for k, v in Counter(recs[i]["domain"] for i in idx).most_common(3)))
    P("  (영문 코드 도메인 = business·tech·finance·hr·legal·mixed·security 처럼 ASCII 이름, 그 밖은 한글 도메인명. 저장된 메타일 뿐 생성 경로의 확인된 사실이 아니다.)")


def baseline_section(recs, g, w, gid, ngrp, rng, B) -> dict:
    sec("base")
    P("")
    P("=" * 100)
    P("9. 모델과 무관한 기준선 — 글자 TF-IDF 최근접 5개 다수결(학습 없음, 분할별 train.jsonl 이웃)")
    P("=" * 100)
    P("  같은 보류 문서·같은 정답 라벨로 단순 이웃 조회의 재현율 격차를 잰다. 이 기준선에서도 격차가 크면 격차는 문서 집단의 성질(이웃 구성)과 관계가 있다는 방증이다")
    P("  (BERT 분류기가 그 성질을 어떻게 쓰는지는 이 표로 알 수 없다). 원문 판은 낱말 자체가 이웃을 정할 수 있어, 낱말을 지운 본문 판을 함께 낸다.")
    okb = np.array([r["pred"] == r["truth"] for r in recs])
    oka = np.array([r["knn_pred_raw"] == r["truth"] for r in recs])
    okm = np.array([r["knn_pred"] == r["truth"] for r in recs])
    out = {}
    for name, ok_ in (("BERT(서빙 경로 τ=0.30)", okb), ("TF-IDF 이웃(원문)", oka), ("TF-IDF 이웃(낱말 지운 본문)", okm)):
        nW, cW = rec_table(g, ok_, w)
        nN, cN = rec_table(g, ok_, ~w)
        out[name] = macro(nW, cW) - macro(nN, cN)
        P(f"  {name:26s} W {pc(macro(nW, cW))} · N {pc(macro(nN, cN))} · 격차 {pp(macro(nW, cW) - macro(nN, cN))} | 등급별 격차 "
          + " ".join(f"{G[i]} {pp(cW[i] / nW[i] - cN[i] / nN[i])}" for i in range(4)) + " | 전체 재현율 %s" % pc(macro(*rec_table(g, ok_, np.ones(len(g), bool)))))
    both = ((okb & okm).sum(), (okb & ~okm).sum(), (~okb & okm).sum(), (~okb & ~okm).sum())
    P(f"  BERT·이웃(낱말 지운 판) 맞음/틀림 교차(전체 {len(g)}건): 둘 다 맞음 {both[0]} · BERT만 맞음 {both[1]} · 이웃만 맞음 {both[2]} · 둘 다 틀림 {both[3]}")
    m = (~w) & (g < 3)
    P(f"  낱말 없음 TS·S1·S2 {int(m.sum())}건 중: BERT 맞음 {int((okb & m).sum())} · 이웃(낱말 지운 판) 맞음 {int((okm & m).sum())} · 둘 다 틀림 {int((~okb & ~okm & m).sum())}")
    return out


def error_direction(recs, g, w) -> None:
    sec("err")
    P("")
    P("=" * 100)
    P("10. 오류 방향 — 정답 등급별로 낱말 있음(W)·없음(N) 문서가 어느 등급으로 예측됐나 (행=정답, 열=예측)")
    P("=" * 100)
    for wl, wn in ((True, "낱말 있음"), (False, "낱말 없음")):
        P(f"  [{wn}]          " + "".join(f"{x:>7s}" for x in G))
        for gi in range(4):
            idx = [i for i, r in enumerate(recs) if w[i] == wl and g[i] == gi]
            cnt = Counter(recs[i]["pred"] for i in idx)
            P(f"   정답 {G[gi]}({len(idx):4d}) " + "".join(f"{cnt.get(x, 0):7d}" for x in G))


def confidence_section(recs, g, ok, w, down) -> None:
    sec("conf")
    P("")
    P("=" * 100)
    P("11. 확신도 구간(모델 최대 확률) — 통제가 아니라 기술. 낱말 없는 문서의 실패가 낮은 확신도에 몰려 있는가")
    P("=" * 100)
    mp = np.array([r["maxp"] for r in recs])
    edges = [0.0, 0.5, 0.7, 0.9, 0.99, 1.0001]
    P(f"   {'구간':12s} {'낱말':4s} {'문서':>5s} {'정확도':>7s} {'실패':>5s} {'고등급 미탐/고등급':>16s}")
    for lo, hi_ in zip(edges[:-1], edges[1:]):
        for wl, wn in ((True, "있음"), (False, "없음")):
            m = (mp >= lo) & (mp < hi_) & (w == wl)
            if not m.any():
                continue
            hm = int((down & m & (g <= 1)).sum())
            P(f"   [{lo:.2f},{min(hi_, 1.0):.2f}) {wn:4s} {int(m.sum()):5d} {pc(ok[m].mean(), 6)} {int((~ok & m).sum()):5d} {hm:>7d}/{int((m & (g <= 1)).sum()):<7d}")
    m = (~w) & (g <= 1) & down
    P(f"  낱말 없음 고등급 미탐 {int(m.sum())}건의 확신도: " + " ".join(f"{x:.2f}" for x in sorted(mp[m])))
    P("  (확신도 < 0.5 이면 배포 프로필의 사람 검수 라우팅에 걸린다고 볼 수 있으나 이 표는 검수 경로를 재현한 것이 아니다 — 검수는 미포함이다)")
    P("")
    P("  고등급 미탐 %d건(낱말 없음 %d · 낱말 있음 %d)의 label_source·정답 등급→예측 등급" % (int((down & (g <= 1)).sum()), int((~w & down & (g <= 1)).sum()), int((w & down & (g <= 1)).sum())))
    cnt = Counter((recs[i]["label_source"], recs[i]["truth"], recs[i]["pred"], "W" if w[i] else "N") for i in np.where(down & (g <= 1))[0])
    for (ls, t, p_, ww), v in sorted(cnt.items(), key=lambda kv: -kv[1]):
        P(f"    {ls:20s} 정답 {t} → 예측 {p_}  낱말 {ww}: {v}건")


def limits_section() -> None:
    sec("limits")
    P("")
    P("=" * 100)
    P("12. 한계·주의")
    P("=" * 100)
    P("  · 층화는 관찰된 축만 통제한다. 통제하지 않은 문서 특성이 남는다. 층화 후 격차가 0 에 가까워도 '낱말 무관' 이 아니라 '그 축들로 설명되는 만큼' 이다.")
    P("  · 낱말 있는 문서와 없는 문서가 같은 층에 함께 있지 않으면 통제 비교 자체가 불가능하다(8절). 그 부분의 격차는 이 데이터로 추정할 수 없다 —")
    P("    격차의 원인이 낱말 유무인지, 낱말 유무와 함께 갈리는 생성 무리(도메인·길이·출처)인지를 이 셋 안에서는 가를 수 없다.")
    P("  · 서로 겹치는 축을 함께 통제하면 지지 층이 줄어 결과가 커버율만큼의 부분집합에 대한 말이 된다. 커버율이 낮은 행은 전체 격차를 대표하지 않는다.")
    P("  · 부트스트랩 구간은 지지 층을 관찰 자료로 고정한 채 근접중복 묶음 단위로 다시 뽑은 값이다(층 선택의 불확실성은 미반영).")
    P("  · 순열 기준선은 층 안에서 낱말 표지를 섞어 '층이 같으면 격차가 얼마나 우연히 나오나' 를 본 값이다(라벨 섞기 기준선과 같은 취지).")
    P("  · 검수 라우팅·서빙 경로의 합의게이트 등은 preds.json 에 이미 반영된 것 외에는 재현하지 않았다.")
    P("  · 분할별 모델이 다르다(5개, 시드 42 하나). 학습 시드 변동은 반영하지 않았다.")
    P("  · 낱말 판정은 NFKC 정규화 후 프로젝트 정규식(생성기 금지어 20개)이다. 그 밖의 등급 암시 표현(예: '기밀 수준', 문서 머리말 형식)은 '낱말 없음' 으로 센다.")
    P("  · 이 도구는 문서를 바꾸거나 모델을 다시 돌리지 않는다. 낱말을 지운 재평가는 하지 않았고 커밋 fd01dcf8 의 결과만 인용한다.")


def summary_section(overview, main, chain, conc, base, pair, okarr) -> None:
    sec("summary")
    P("")
    P("=" * 100)
    P("2. 핵심 결과 (자동 요약 — 아래 절의 표에서 계산한 값만 옮김, 원인 단정 아님)")
    P("=" * 100)
    P(f"  (가) 통제 없는 4등급 평균 재현율 격차(W−N) {pp(overview['crude'])}  (부트스트랩 95% {pp(overview['ci'][0])}~{pp(overview['ci'][1])}) · 등급별 "
      + " ".join(f"{G[i]} {pp(overview['per'][i])}" for i in range(4)))
    # (나) label_source 별 격차가 어디에 있나
    lsm = next(r for r in main if r["key"] == "label_source")
    P("  (나) 격차는 label_source 안에서 균일하지 않다 — 낱말 있음·없음이 함께 n≥30 인 층의 격차(등급 · 출처):")
    st = lsm["st"]
    for gi in range(4):
        for s in np.where(st.supp[gi])[0]:
            mk = (st.lv == s) & (st.g == gi)
            rw = (mk & st.w & okarr).sum() / (mk & st.w).sum()
            rn = (mk & ~st.w & okarr).sum() / (mk & ~st.w).sum()
            P(f"        {G[gi]} · {lsm['names'][s]:16s} W {pc(rw)}({int((mk & st.w).sum())}) · N {pc(rn)}({int((mk & ~st.w).sum())}) · 격차 {pp(rw - rn)}")
    # (다) 커버율
    P("  (다) 겹침: 낱말 없는 TS·S1·S2 문서 %d건 중 같은 등급 낱말 있는 문서와 같은 층에(n≥30) 든 비율 — 단일 축별:" % int(overview["nN"][:3].sum()))
    P("        " + " · ".join(f"{r['key']} {pc(r['cover3'], 4, 0)}" for r in main if r["key"] in ("label_source", "domain", "long_doc", "len_q5", "p_lab_dom", "knn_agree", "is_court", "lang")))
    P("        누적 통제: " + " → ".join(f"{r['key'].split(' ')[0]} {pc(r['cover3'], 4, 0)}" for r in chain))
    # (라) 누적 통제 결과
    P("  (라) 누적 통제 후 층화 격차(지지 등급 평균, 등급별, TS·S1·S2 합산 커버율):")
    for r in chain:
        P(f"        {r['key']:28s} {pp(r['ms'])} · " + " ".join(f"{G[i]} {pp(r['std'][i])}" for i in range(4)) + f" · 커버율 {pc(r['cover3'], 4, 0)}")
    # (마) 실패 집중
    ls_rows = sorted(conc["label_source"], key=lambda r: -r[2])
    P("  (마) 낱말 없는 TS·S1·S2 실패 %d건이 몰린 label_source: " % sum(r[2] for r in ls_rows) + " · ".join(f"{r[0]} {r[2]}건({r[4] * 100:.0f}%)" for r in ls_rows[:4]))
    ld_rows = {r[0]: r for r in conc["long_doc"]}
    if "긴문서" in ld_rows and "보통" in ld_rows:
        a, b = ld_rows["긴문서"], ld_rows["보통"]
        P(f"        긴 문서 표지: 긴문서 N {a[1]}건 실패율 {pc(a[3])} (W {a[5]}건 {pc(a[6])}) · 보통 N {b[1]}건 실패율 {pc(b[3])} (W {b[5]}건 {pc(b[6])})")
    kr = {r[0]: r for r in conc["knn_agree"]}
    if kr:
        P("        낱말 지운 본문 기준 이웃 라벨 일치: " + " · ".join(f"{k} N {v[1]}건 실패율 {pc(v[3])}(W {v[5]}건 {pc(v[6])})" for k, v in kr.items()))
    P("  (아) 출처(label_source)를 고정하고 축 하나를 더 통제했을 때 TS·S1·S2 합산 커버율과 층화 격차(지지 등급 평균):")
    P("        " + " · ".join(f"{r['key'].replace('출처 × ', '')} {pc(r['cover3'], 4, 0)}/{pp(r['ms'])}" for r in pair))
    # (바) 모델 무관 기준선
    P("  (바) 모델 무관 기준선 격차: " + " · ".join(f"{k} {pp(v)}" for k, v in base.items()))
    # (자) 등급별로 층화가 가능했던 축 수
    cand = [r for r in main if r["key"] not in ("maxp",)]
    P("  (자) 등급별로 지지 층이 하나라도 있어 층화가 가능했던 단일 축 수(모델 출력 축 제외 %d개 중, 셀 n≥30): " % len(cand)
      + " · ".join(f"{G[i]} {sum(bool(r['used'][i]) for r in cand)}개" for i in range(4)))
    # (사) 통제 후 격차가 낮게 나온 축과 그 커버율
    small = sorted([r for r in main if not np.isnan(r["ms"]) and r["key"] != "maxp"], key=lambda r: abs(r["ms"]))[:4]
    P("  (사) 단일 축 통제(셀 n≥30)에서 층화 격차가 가장 작은 축(지지 등급 평균 · 지지 등급 수 · N 커버율): "
      + " · ".join(f"{r['key']} {pp(r['ms'])}({int(r['used'].sum())}/4·{pc(np.nanmean(r['cover'][r['used']]), 4, 0)})" for r in small))
    P("       커버율이 낮은 축의 작은 격차는 '그 축으로 설명된다' 가 아니라 '비교 가능한 층이 그 문서들뿐이다' 를 뜻한다(7절 지지 층 명세 참조).")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", help="결과를 이 파일에도 저장한다")
    ap.add_argument("--force", action="store_true", help="9/20 수치 재현에 실패해도 진행")
    ap.add_argument("--boot", type=int, default=1000)
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    rng = np.random.default_rng(SEED)
    log("입력 읽는 중")
    recs = load_records()
    qualification(recs)
    good = reproduce(recs)

    def emit() -> int:
        text = "\n".join("\n".join(SECTIONS.get(k, [])) for k in ORDER) + "\n"
        print(text)
        if a.out:
            Path(a.out).parent.mkdir(parents=True, exist_ok=True)
            Path(a.out).write_text(text, encoding="utf-8", newline="\n")
            log(f"(저장: {a.out})")
        return 0 if good else 2

    if not good and not a.force:
        sec("summary")
        P("")
        P("재현 실패 — 여기서 멈춘다. 어긋난 항목은 1절에 있다(--force 로 강제 진행 가능).")
        return emit()
    g = np.array([GI[r["truth"]] for r in recs])
    w = np.array([r["w"] for r in recs])
    ok = np.array([r["pred"] == r["truth"] for r in recs])
    down = np.array([RANK[r["pred"]] < RANK[r["truth"]] for r in recs])
    log("근접중복 묶음 계산")
    gid, ngrp = near_dup_groups([r["text"] for r in recs])
    pre = json.loads((CV / "prereg.json").read_text(encoding="utf-8"))
    grp_note = f"[부트스트랩 묶음] 근접중복 묶음 {ngrp}개 (prereg.json 기록 {pre['split']['n_groups']}개 — {'일치' if ngrp == pre['split']['n_groups'] else '불일치'})"
    log("학습 문서 대비 이웃 특성 계산")
    add_train_relative_features(recs)
    axes = build_axes(recs)
    log("격차 개요")
    overview = gap_overview(recs, g, ok, w, down, gid, ngrp, rng, a.boot, grp_note)
    discarded_section(recs, axes, w, ok, g)
    axis_tables(recs, axes, g, ok, w)
    conc = concentration(recs, axes, g, ok, w)
    log("층화·부트스트랩·순열")
    main_res, chain_res, ref, pair_res = stratified_section(recs, axes, g, ok, w, down, gid, ngrp, rng, a.boot)
    overlap_section(recs, axes, g, ok, w)
    base = baseline_section(recs, g, w, gid, ngrp, rng, a.boot)
    error_direction(recs, g, w)
    confidence_section(recs, g, ok, w, down)
    limits_section()
    summary_section(overview, main_res, chain_res, conc, base, pair_res, ok)
    return emit()


if __name__ == "__main__":
    sys.exit(main())
