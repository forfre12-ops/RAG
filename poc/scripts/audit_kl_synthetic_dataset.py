# -*- coding: utf-8 -*-
"""KL 제공 「가상 영업비밀 문서 합성 데이터셋」 품질 점검 — 한 번에 다시 돌리는 도구.

만든 이유(2026-09-21): 이 데이터셋 점검 수치를 세션 임시 폴더의 스크립트로만 냈다.
KL 에 보내는 글에 "재현할 수 있다"고 쓰려면 도구가 리포에 있어야 한다.

무엇을 재나 (전부 dataset.csv 를 DOC_UUID 별로 이어 붙인 문서 단위):
  1 구성      문서 수·등급 분포·문서당 등급 수
  2 길이      등급별 길이, 길이만으로 등급 맞히기(라벨 섞기 기준선 포함)
  3 지름길    TF-IDF(1~2gram)+로지스틱 회귀 5분할×시드3 — 4등급·6개 등급쌍, 문서유형·업무분야 코드만
  4 등급낱말  등급명·'기밀'·'비밀' 등장 문서, 분류·지정·간주 서술 문서
  5 이상문서  가나·키릴·아랍·태국 문자, 한자, U+FFFD, 같은 문장 5회 이상 반복, 화살표
  6 룰 대조   LabelRuleEngine 판정과 대조 — 라벨 섞기 1,000회 기준선, 키워드 무매치(=기본값 S3) 분해, 매핑 24가지
  7 근접중복  BGE-M3 코사인, 입력 조건 3가지(--embed, GPU 필요)
  8 혼입 대조 poc/datasets 아래 jsonl 의 모든 긴 문자열 필드와 본문 지문 대조(--overlap)

측정 기준(CLAUDE.md §4): 결과에 분모와 방법을 함께 적는다. 정확도는 항상 '라벨 섞기' 값과 나란히 낸다.

사용:
    cd poc
    .venv/Scripts/python.exe scripts/audit_kl_synthetic_dataset.py --embed --overlap
결과: poc/reports/CLAUDE_KL_DATASET_AUDIT_<날짜>/summary.json (+ 문서 단위 목록 CSV)
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import itertools
import json
import os
import re
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(POC / "src"))

DEFAULT_CSV = r"C:\Users\tio\Downloads\가상 영업비밀 문서 합성 데이터셋\dataset.csv"
NAMES = {0: "TS0", 1: "TS1", 2: "TS2", 3: "TS3"}
OUR = ["TS", "S1", "S2", "S3"]
ASSUMED = {0: "TS", 1: "S1", 2: "S2", 3: "S3"}  # 순서 대응(특급기밀→TS … 3급공개→S3)


def load_docs(csv: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    df = pd.read_csv(csv, encoding="utf-8-sig")
    df["CHUNK_TEXT"] = df["CHUNK_TEXT"].fillna("")
    df = df.sort_values(["DOC_UUID", "CHUNK_ORDER"])
    docs = (df.groupby("DOC_UUID")
            .agg(text=("CHUNK_TEXT", lambda s: "\n".join(s)), label=("LABEL", "first"),
                 dtype=("DOC_TYPE_CODE", "first"), domain=("DOMAIN_CODE", "first"),
                 nchunk=("CHUNK_ORDER", "size"), nlabel=("LABEL", "nunique"))
            .reset_index())
    return df, docs


# ---------------------------------------------------------------- 1~3
def cv_acc(make, X, y, seed):
    from sklearn.model_selection import StratifiedKFold, cross_val_predict
    pred = cross_val_predict(make(), X, y, cv=StratifiedKFold(5, shuffle=True, random_state=seed))
    return float((pred == y).mean())


def with_shuffle(make, X, y, seeds=(0, 1, 2), n_shuf=3):
    real = [cv_acc(make, X, y, s) for s in seeds]
    rng = np.random.RandomState(123)
    shuf = [cv_acc(make, X, rng.permutation(y), seeds[i % len(seeds)]) for i in range(n_shuf)]
    return dict(real_mean=round(float(np.mean(real)), 4), real_min=round(min(real), 4), real_max=round(max(real), 4),
                shuffled_mean=round(float(np.mean(shuf)), 4), shuffled_max=round(max(shuf), 4))


def measure_structure_length_shortcut(docs: pd.DataFrame) -> dict:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import OneHotEncoder
    from sklearn.tree import DecisionTreeClassifier

    y = docs.label.values
    out: dict = {}
    out["structure"] = dict(n_docs=len(docs), n_chunk_rows=int(docs.nchunk.sum()),
                            docs_with_multiple_labels=int((docs.nlabel > 1).sum()),
                            label_counts={NAMES[k]: int(v) for k, v in docs.label.value_counts().sort_index().items()})
    docs = docs.assign(chars=docs.text.str.len())
    out["length"] = dict(
        overall=dict(median=int(docs.chars.median()), min=int(docs.chars.min())),
        by_grade={NAMES[k]: dict(median=int(docs.chars[docs.label == k].median())) for k in range(4)},
        length_only_4class=with_shuffle(lambda: DecisionTreeClassifier(max_depth=3, random_state=0),
                                        np.log1p(docs.chars.values).reshape(-1, 1), y))

    def tfidf_lr():
        return make_pipeline(TfidfVectorizer(analyzer="word", ngram_range=(1, 2), min_df=2, sublinear_tf=True,
                                             token_pattern=r"(?u)\b\w+\b"),
                             LogisticRegression(max_iter=2000, C=10))

    T = docs.text.values
    sc = {"4class": with_shuffle(tfidf_lr, T, y)}
    for a, b in itertools.combinations(range(4), 2):
        m = np.isin(y, [a, b])
        sc[f"{NAMES[a]}_vs_{NAMES[b]}"] = with_shuffle(tfidf_lr, T[m], y[m])
    out["tfidf_shortcut"] = sc

    def onehot_lr():
        return make_pipeline(OneHotEncoder(handle_unknown="ignore"), LogisticRegression(max_iter=2000))

    M2 = docs[["dtype", "domain"]].values
    m01 = np.isin(y, [0, 1])
    out["code_only"] = dict(
        dtype_domain_4class=with_shuffle(onehot_lr, M2, y),
        domain_only_4class=with_shuffle(onehot_lr, docs[["domain"]].values, y),
        dtype_only_4class=with_shuffle(onehot_lr, docs[["dtype"]].values, y),
        dtype_domain_TS0_vs_TS1=with_shuffle(onehot_lr, M2[m01], y[m01]))

    vec = TfidfVectorizer(analyzer="word", ngram_range=(1, 1), min_df=5, sublinear_tf=True, token_pattern=r"(?u)\b\w+\b")
    lr = LogisticRegression(max_iter=2000, C=10).fit(vec.fit_transform(T[m01]), y[m01])
    fn = np.array(vec.get_feature_names_out()); o = np.argsort(lr.coef_[0])
    out["top_tokens_TS0_vs_TS1"] = dict(TS0=fn[o[:20]].tolist(), TS1=fn[o[::-1][:20]].tolist())
    return out


# ---------------------------------------------------------------- 4
GRADE_WORD_PATTERNS = {
    "대외비": r"대외비", "극비": r"극비", "특급": r"특급", "1급": r"1급", "2급": r"2급", "3급": r"3급",
    "등급명 결합": r"(특급\s*기밀|1급\s*비밀|2급\s*대외비|3급\s*공개)",
    "보안등급/비밀등급/등급:": r"(보안\s*등급|비밀\s*등급|등급\s*[:：])",
    "TS0~TS3 표기": r"TS\s?[0-3]",
    "기밀": r"기밀", "비밀(비밀번호 제외)": r"비밀(?!번호)",
}
# 자료를 '기밀'·'비밀'로 분류·지정·간주·처리한다는 서술
CLASSIFY_RX = re.compile(
    r"(기밀|비밀)(?:\s*문서|\s*정보)?['’]?(?:로|으로)\s*(?:분류|지정|간주|처리)"
    r"|비밀['’]\s*또는\s*['‘]내부['’]\s*등급")


def measure_grade_words(docs: pd.DataFrame) -> dict:
    out: dict = {"presence": {}}
    for name, p in GRADE_WORD_PATTERNS.items():
        rx = re.compile(p)
        hit = docs.text.apply(lambda t: bool(rx.search(t)))
        row = {NAMES[k]: int(hit[docs.label == k].sum()) for k in range(4)}
        row["전체"] = int(hit.sum())
        out["presence"][name] = row
    ts = docs[docs.text.str.contains(r"TS\s?[0-3]", regex=True)]
    out["ts_digit_contexts"] = [
        (r.DOC_UUID, NAMES[r.label], re.search(r".{0,20}TS\s?[0-3].{0,20}", r.text, re.S).group(0).replace("\n", " "))
        for r in ts.itertuples()]
    cl = docs[docs.text.apply(lambda t: bool(CLASSIFY_RX.search(t)))]
    out["classify_statement_docs"] = [(r.DOC_UUID, NAMES[r.label], CLASSIFY_RX.search(r.text).group(0)) for r in cl.itertuples()]
    out["classify_statement_n"] = len(cl)
    return out


# ---------------------------------------------------------------- 5
SCRIPTS = {"일본어 가나": r"[\u3040-\u30ff]", "키릴 문자": r"[\u0400-\u04ff]", "아랍 문자": r"[\u0600-\u06ff]",
           "태국 문자": r"[\u0e00-\u0e7f]", "한자": r"[\u4e00-\u9fff]", "깨진 글자 U+FFFD": "\ufffd"}


def _max_repeat(t: str) -> tuple[int, str]:
    lines = [l.strip() for l in re.split(r"[\n。]|(?<=[.다함음])\s", t) if len(l.strip()) >= 8]
    if not lines:
        return 0, ""
    s, c = collections.Counter(lines).most_common(1)[0]
    return c, s


def measure_anomalies(docs: pd.DataFrame) -> dict:
    out: dict = {"foreign_script": {}}
    foreign_ids: set = set()
    for name, p in SCRIPTS.items():
        rx = re.compile(p)
        rows = []
        for r in docs.itertuples():
            spans = [m.group(0) for m in re.finditer(r"\S*(?:%s)+\S*" % p, r.text)]
            if spans:
                rows.append((r.DOC_UUID, NAMES[r.label], spans[:3]))
        out["foreign_script"][name] = rows
        foreign_ids |= {x[0] for x in rows}
    rep = []
    for r in docs.itertuples():
        n, s = _max_repeat(r.text)
        if n >= 5:
            rep.append((r.DOC_UUID, NAMES[r.label], n, s[:50]))
    out["repeated_sentence_ge5"] = rep
    arrows = sorted(((r.DOC_UUID, NAMES[r.label], r.text.count("→")) for r in docs.itertuples()), key=lambda x: -x[2])
    out["arrows_top5"] = arrows[:5]
    out["docs_with_ge10_arrows"] = sum(1 for a in arrows if a[2] >= 10)
    arrow_ids = {a[0] for a in arrows if a[2] >= 40}
    repeat_ids = {x[0] for x in rep}
    out["foreign_script_docs"] = len(foreign_ids)
    out["union_docs"] = len(foreign_ids | repeat_ids | arrow_ids)
    out["union_ids"] = sorted(foreign_ids | repeat_ids | arrow_ids)
    return out


# ---------------------------------------------------------------- 6
def measure_rule(docs: pd.DataFrame, n_shuffle: int = 1000) -> dict:
    os.environ.setdefault("EMB_PROVIDER", "hash")  # 내장 시드는 전부 exact 라 임베더를 쓰지 않는다
    from koipa.modules.m3_labeling.rule_engine import KEYWORD_SEEDS, LabelRuleEngine

    eng = LabelRuleEngine()
    rows = []
    for r in docs.itertuples():
        o = eng.label(r.text)
        rows.append((r.DOC_UUID, int(r.label), o.grade, len(o.matched_keywords)))
    R = pd.DataFrame(rows, columns=["doc", "kl", "rule", "nkw"])
    rk = {g: i for i, g in enumerate(OUR)}
    R["mapped"] = R.kl.map(ASSUMED)
    a = R.mapped.map(rk).values; b = R.rule.map(rk).values
    out: dict = {"n": len(R), "seed_count": len(KEYWORD_SEEDS),
                 "semantic_seed_count": sum(1 for s in KEYWORD_SEEDS if s.get("pattern_type") == "semantic")}

    def stats(mask):
        aa, bb = a[mask], b[mask]
        return dict(n=int(mask.sum()), exact=int((aa == bb).sum()), exact_pct=round(float((aa == bb).mean() * 100), 1),
                    within1_pct=round(float((abs(aa - bb) <= 1).mean() * 100), 1))

    rng = np.random.RandomState(7)

    def shuffle_base(mask):
        aa, bb = a[mask], b[mask]
        ex, w1 = [], []
        for _ in range(n_shuffle):
            p = rng.permutation(aa)
            ex.append((p == bb).mean()); w1.append((abs(p - bb) <= 1).mean())
        return dict(exact_mean_pct=round(float(np.mean(ex) * 100), 1), exact_max_pct=round(float(np.max(ex) * 100), 1),
                    within1_mean_pct=round(float(np.mean(w1) * 100), 1), within1_max_pct=round(float(np.max(w1) * 100), 1))

    allm = np.ones(len(R), bool); kwm = (R.nkw > 0).values
    out["all_docs"] = {**stats(allm), "shuffled": shuffle_base(allm),
                       "spearman": round(float(pd.Series(a).corr(pd.Series(b), method="spearman")), 3)}
    out["keyword_matched_docs"] = {**stats(kwm), "shuffled": shuffle_base(kwm)}
    out["no_keyword_docs"] = dict(n=int((~kwm).sum()), pct=round(float((~kwm).mean() * 100), 1),
                                  all_S3=bool((R.rule[~kwm] == "S3").all()),
                                  exact_matches_among_them=int(((a == b) & ~kwm).sum()))
    out["rule_grade_dist"] = R.rule.value_counts().to_dict()
    ct = pd.crosstab(R.kl.map(lambda k: NAMES[k]), R.rule).reindex(columns=OUR, fill_value=0)
    out["confusion_rows_KL_cols_rule"] = ct.to_dict("index")
    out["S3_share_by_KL_grade_pct"] = {NAMES[k]: round(float((R.rule[R.kl == k] == "S3").mean() * 100), 1) for k in range(4)}
    out["S3_default_share_by_KL_grade_pct"] = {NAMES[k]: round(float(((R.nkw == 0) & (R.kl == k)).sum() / (R.kl == k).sum() * 100), 1) for k in range(4)}
    perms = sorted(((round(float((R.kl.map({k: p[k] for k in range(4)}) == R.rule).mean() * 100), 1), list(p))
                    for p in itertools.permutations(OUR)), reverse=True)
    out["mapping_24_top3"] = perms[:3]
    out["mapping_reverse_pct"] = next(acc for acc, p in perms if p == ["S3", "S2", "S1", "TS"])
    out["mapping_assumed_rank"] = 1 + [p for _, p in perms].index([ASSUMED[k] for k in range(4)])
    return out, R


# ---------------------------------------------------------------- 7
def measure_near_duplicates(docs: pd.DataFrame, df: pd.DataFrame) -> dict:
    import torch
    from sentence_transformers import SentenceTransformer

    m = SentenceTransformer("BAAI/bge-m3", device="cuda" if torch.cuda.is_available() else "cpu")
    out: dict = {}
    first = df.groupby("DOC_UUID").CHUNK_TEXT.first().reindex(docs.DOC_UUID).tolist()

    def run(name, texts, msl):
        m.max_seq_length = msl
        E = m.encode(texts, batch_size=8 if msl > 1024 else 32, normalize_embeddings=True,
                     show_progress_bar=False, convert_to_numpy=True).astype(np.float32)
        v = (E @ E.T)[np.triu_indices(len(texts), 1)]
        out[name] = {"max_cos": round(float(v.max()), 4), "ge_0.92": int((v >= .92).sum()),
                     "ge_0.90": int((v >= .90).sum()), "ge_0.85": int((v >= .85).sum()), "pairs": int(len(v))}
        print(name, out[name], flush=True)

    run("문서전체_최대8192토큰_fp32", docs.text.tolist(), 8192)
    run("512토큰_절단_fp32", docs.text.tolist(), 512)
    run("첫_청크만_512토큰", first, 512)
    return out


# ---------------------------------------------------------------- 8
def _norm(t: str) -> str:
    return re.sub(r"\s+", "", t or "")


def _fp(t: str) -> str:
    return hashlib.sha1(_norm(t)[:400].encode("utf-8")).hexdigest()


def measure_overlap(docs: pd.DataFrame, df: pd.DataFrame) -> dict:
    kl = {_fp(t) for t in docs.text}
    kl |= {_fp(t) for t in df.CHUNK_TEXT if len(_norm(t)) >= 100}
    kl_ids = set(docs.DOC_UUID)
    files = rows = 0
    hits: dict = {}
    no_str = []
    skipped_big = []
    for root, _, fs in os.walk(POC / "datasets"):
        if "kl_trade_secret_filtered" in root:  # KL 정제본 보관 폴더 — 일부러 제외
            continue
        for fn in fs:
            if not fn.endswith(".jsonl"):
                continue
            p = os.path.join(root, fn)
            if os.path.getsize(p) > 300_000_000:
                skipped_big.append(p); continue
            files += 1; n = h = longstr = 0
            try:
                for line in open(p, encoding="utf-8"):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                    except Exception:
                        continue
                    if not isinstance(r, dict):
                        continue
                    n += 1
                    if str(r.get("doc_id", "")) in kl_ids:
                        h += 1
                    for v in r.values():
                        if isinstance(v, str) and len(_norm(v)) >= 100:
                            longstr += 1
                            if _fp(v) in kl:
                                h += 1
                                break
            except Exception:
                continue
            rows += n
            if h:
                hits[p] = h
            if n and longstr == 0:
                no_str.append(p)
    return dict(jsonl_files=files, rows=rows, files_with_hits=len(hits), hits=hits,
                files_without_long_string_field=len(no_str), skipped_over_300MB=len(skipped_big),
                fingerprints=len(kl), not_checked="csv·parquet·json·모델 가중치")


# ---------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", default=DEFAULT_CSV)
    ap.add_argument("--out", default=None)
    ap.add_argument("--embed", action="store_true", help="BGE-M3 근접중복 측정(GPU 권장)")
    ap.add_argument("--overlap", action="store_true", help="poc/datasets jsonl 과 본문 지문 대조")
    ap.add_argument("--skip-rule", action="store_true")
    a = ap.parse_args()

    stamp = time.strftime("%Y%m%d")
    out_dir = Path(a.out) if a.out else POC / "reports" / f"CLAUDE_KL_DATASET_AUDIT_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    df, docs = load_docs(a.csv)
    res: dict = {"csv": a.csv, "measured_on": stamp}
    print("문서", len(docs), flush=True)
    res.update(measure_structure_length_shortcut(docs)); print("1~3 완료", flush=True)
    res["grade_words"] = measure_grade_words(docs); print("4 완료", flush=True)
    res["anomalies"] = measure_anomalies(docs); print("5 완료", flush=True)
    if not a.skip_rule:
        res["rule_crosscheck"], R = measure_rule(docs)
        R.to_csv(out_dir / "rule_rows.csv", index=False, encoding="utf-8-sig"); print("6 완료", flush=True)
    if a.embed:
        res["near_duplicates"] = measure_near_duplicates(docs, df); print("7 완료", flush=True)
    if a.overlap:
        res["overlap"] = measure_overlap(docs, df); print("8 완료", flush=True)
    (out_dir / "summary.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("저장", out_dir / "summary.json")


if __name__ == "__main__":
    main()
