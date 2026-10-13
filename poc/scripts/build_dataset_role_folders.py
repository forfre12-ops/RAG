#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""학습셋·골든셋·평가셋을 역할별 폴더로 나눠 저장하고, 세트 사이 겹침을 센다.

만드는 것 (기본 출력 = datasets/세트구분/):

    1_학습셋/   A_… 배포본이 가중치를 학습한 문서 · B_… 학습 중 체크포인트 선택에 쓴 문서
    2_골든셋/   학습에 쓰지 않기로 한 문서 (golden100 v3.0 · KL 검수 전달본 ff5a822c)
    3_평가셋/   학습에 안 쓴 채 성능을 재는 문서 (holdout109 · v5_clean test)

세트 폴더마다:
    문서.jsonl    원본 행 그대로(라벨·출처 포함). 프로그램용.
    본문/         문서 1건 = 텍스트 1개(0001.txt …, 번호는 섞인 순서). **파일명에 정답이 없다** — 검수자에게 이 폴더만 준다.
                  (본문 안에 등급 표기가 적힌 문서는 정답표.csv `본문에_등급표기` 로 표시한다)
    정답표.csv    doc_id·정답·라벨 출처·플래그. 검수자에게 주지 않는다(정답 노출 방지).
    README.md     원본 경로·건수·등급 분포·라벨 근거

최상위: 문서색인.csv · 세트별_겹침_점검.json · README.md

왜 이 도구가 있나(2026-09-20): "학습셋에 들어간 문서 / 골든셋 / 평가셋을 나눠 보관하고 싶다"는
요청. 이름만으로는 갈리지 않았다 — `gold_real/` 은 이름이 골든이지만 그 행 255건이 배포본 학습셋
(train.jsonl `origin_dataset=gold_real`, val 23·test 19 포함하면 297)에 들어갔다. 그래서 폴더가 아니라 **문서
단위로** 본문 지문(dataset_usage.body_fingerprint)과 글자 TF-IDF 코사인으로 세트 간 겹침을 잰다.

읽기 전용: 원본 파일은 건드리지 않는다. 출력 폴더만 새로 쓴다(재실행하면 그 폴더를 덮어쓴다).
사용:
    poc/.venv/Scripts/python.exe scripts/build_dataset_role_folders.py
    poc/.venv/Scripts/python.exe scripts/build_dataset_role_folders.py --out datasets/세트구분 --no-bodies
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from koipa.dataset_usage import body_fingerprint  # noqa: E402

GRADES = ("TS", "S1", "S2", "S3")
NEAR_DUP_HI = 0.95   # 1차 통합테스트(run_phase1_cv.py)가 근접중복 묶음에 쓴 문턱과 같다
NEAR_DUP_LO = 0.90


@dataclass
class SetSpec:
    role: str            # 1_학습셋 / 2_골든셋 / 3_평가셋
    folder: str
    key: str             # 겹침 표에서 쓰는 짧은 이름
    src: str             # _ROOT 기준 원본 경로
    label_key: str = "label"
    note: str = ""       # 세트 README 에 그대로 들어가는 자격 설명
    rows: list[dict] = field(default_factory=list)


SETS = [
    SetSpec(
        "1_학습셋", "A_배포본_가중치학습", "학습", "datasets/labeled_p1_v5_clean/train.jsonl",
        note=("배포본 v-fe4b386b 의 가중치를 직접 학습시킨 문서. 이 문서들로 다시 재면 암기 성능이 나온다"
              "(같은 문서 정확도 94.9% — _deployed_model_train_docs_2042/README.md, 2026-09-19)."),
    ),
    SetSpec(
        "1_학습셋", "B_체크포인트선택용_val", "검증", "datasets/labeled_p1_v5_clean/val.jsonl",
        note=("가중치 갱신에는 안 쓰였지만 에폭별 성능을 보고 최종 체크포인트를 고르는 데 쓰였다"
              "(trainer.py `metric_for_best_model=early_stop_metric`, `eval_dataset=ds_val`). "
              "그래서 최종 평가 정답으로 쓰지 않는다."),
    ),
    SetSpec(
        "2_골든셋", "A_golden100_v3.0_합성", "골든100", "datasets/gold/golden100_labeled_v3.jsonl",
        label_key="target",
        note=("LLM 합성 200건(등급당 50). 정답은 생성 시 의도 등급이고 블라인드 LLM 재판정과 91.0% 일치. "
              "사람 서명 0건. ⚠ 글자 n-gram 만으로 5겹 교차검증 100.0%(라벨 섞기 기준선 28.0%)인 지름길 셋 — "
              "같은 셋 위의 모델 간 상대 비교에만 쓴다(datasets/gold/READONLY.md)."),
    ),
    SetSpec(
        "2_골든셋", "B_KL검수전달본_ff5a822c", "KL검수120", "datasets/golden_review/ff5a822c/candidates.jsonl",
        note=("사람 검수를 받으려고 2026-08-09 에 정제한 120건(등급당 30, 등급 길이 중앙값을 맞춤, 정답 노출 세척). "
              "review_status 는 전부 pending — 사람 확정 0건. 33건은 공개 실문서, 나머지는 합성."),
    ),
    SetSpec(
        "3_평가셋", "A_holdout109_라벨정정후", "홀드아웃109", "datasets/gold_real/holdout_eval.jsonl",
        note=("109건. 2026-09-20 에 규칙=S3 인데 LLM 판정이 덮어쓴 22건을 S3 로 정정한 판(label_before_correction_2026_09_20 "
              "필드에 원 라벨 보존). hardened42·clean42 는 이 안의 같은 42문서라 별도 세트가 아니다. "
              "길이가 등급을 알려 줘 모델 간 비교 불가(Theil's U 0.372)."),
    ),
    SetSpec(
        "3_평가셋", "B_v5_test", "v5test", "datasets/labeled_p1_v5_clean/test.jsonl",
        note=("v5_clean 을 근접중복 없이 나눈 내부 홀드아웃 256건 — 학습과 같은 분포라 낙관적이다. "
              "정정 전 라벨 판이며 65행 정정본(s3fix)에서는 3행이 바뀐다."),
    ),
]


def _read_jsonl(path: Path) -> list[dict]:
    out = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _safe(name: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", str(name))[:80]


def _eid(r: dict) -> str:
    """유효 doc_id. 원본에 doc_id 가 없으면(학습셋 2,042행 중 694행 = bilingual_en·rag_corpus_v2 전건)
    본문 지문 앞 16자로 대신한다 — 같은 본문이면 같은 id 가 나온다."""
    return r.get("doc_id") or "nodoc-" + body_fingerprint(r["text"])[:16]


def _quantiles(vals: list[int]) -> dict:
    if not vals:
        return {}
    s = sorted(vals)
    q = lambda p: s[min(len(s) - 1, int(p * (len(s) - 1)))]  # noqa: E731
    return {"min": s[0], "p25": q(0.25), "median": q(0.5), "p75": q(0.75), "max": s[-1],
            "over_3000": sum(1 for v in s if v > 3000)}


def _s3fix_labels() -> dict[str, str]:
    """65행 정정 후보(datasets/labeled_p1_v5_clean_s3fix)에서 라벨이 다른 본문 지문 → 정정 라벨.
    doc_id 가 없는 행이 있어 본문 지문으로 짝짓는다."""
    base = _ROOT / "datasets" / "labeled_p1_v5_clean"
    fix = _ROOT / "datasets" / "labeled_p1_v5_clean_s3fix"
    out: dict[str, str] = {}
    for name in ("train.jsonl", "val.jsonl", "test.jsonl"):
        if not (base / name).exists() or not (fix / name).exists():
            continue
        a = {body_fingerprint(r["text"]): r["label"] for r in _read_jsonl(base / name)}
        for r in _read_jsonl(fix / name):
            fp = body_fingerprint(r["text"])
            if a.get(fp) not in (None, r["label"]):
                out[fp] = r["label"]
    return out


def _load_all() -> None:
    for sp in SETS:
        path = _ROOT / sp.src
        rows = _read_jsonl(path)
        ids = [_eid(r) for r in rows]
        if len(ids) != len(set(ids)):
            raise SystemExit(f"[중단] {sp.src}: doc_id 중복 {len(ids) - len(set(ids))}건 (본문 지문 대체 id 포함)")
        sp.rows = rows


def _overlap(specs: list[SetSpec]) -> dict:
    """세트 쌍마다: doc_id 일치 · 본문 지문 일치 · 근접중복(코사인) 문서 수. A 의 문서 중 B 와 겹치는 수."""
    import numpy as np
    from sklearn.feature_extraction.text import TfidfVectorizer

    texts, owner = [], []
    for si, sp in enumerate(specs):
        for r in sp.rows:
            texts.append(r["text"])
            owner.append(si)
    owner = np.array(owner)
    vec = TfidfVectorizer(analyzer="char", ngram_range=(3, 4), sublinear_tf=True, min_df=2,
                          max_features=300_000, dtype=np.float32)
    X = vec.fit_transform(texts)
    sim = (X @ X.T).toarray()
    np.fill_diagonal(sim, 0.0)

    fps = [[body_fingerprint(r["text"]) for r in sp.rows] for sp in specs]
    ids = [[r["doc_id"] for r in sp.rows if r.get("doc_id")] for sp in specs]
    starts = np.cumsum([0] + [len(sp.rows) for sp in specs])

    pairs = {}
    per_doc: dict[tuple[int, int], dict] = {}
    for a, sa in enumerate(specs):
        for b, sb in enumerate(specs):
            if a == b:
                continue
            block = sim[starts[a]:starts[a + 1], starts[b]:starts[b + 1]]
            mx = block.max(axis=1) if block.size else np.zeros(len(sa.rows))
            fp_b = set(fps[b])
            id_b = set(ids[b])
            exact = [i for i, f in enumerate(fps[a]) if f in fp_b]
            idm = [i for i, r in enumerate(sa.rows) if r.get("doc_id") and r["doc_id"] in id_b]
            hi = [i for i, v in enumerate(mx) if v >= NEAR_DUP_HI]
            lo = [i for i, v in enumerate(mx) if v >= NEAR_DUP_LO]
            pairs[f"{sa.key}→{sb.key}"] = {
                "A_문서수": len(sa.rows), "본문지문_일치": len(exact), "doc_id_일치": len(idm),
                f"근접중복_코사인≥{NEAR_DUP_HI}": len(hi), f"근접중복_코사인≥{NEAR_DUP_LO}": len(lo),
                "최대코사인_중앙값": round(float(np.median(mx)), 4) if len(mx) else None,
            }
            per_doc[(a, b)] = {"exact": set(exact), "hi": set(hi), "mx": mx}
    # 학습셋(0) 대비 문서별 정보를 색인에 싣기 위해 돌려준다
    return {"pairs": pairs, "per_doc": per_doc}


def _write_set(sp: SetSpec, out_root: Path, *, bodies: bool, s3fix: dict[str, str],
               ov: dict, idx: int, all_specs: list[SetSpec]) -> dict:
    from koipa.services.synth_quality import _exposes_grade_token

    d = out_root / sp.role / sp.folder
    if d.exists():
        shutil.rmtree(d)
    (d / "본문").mkdir(parents=True)

    train_idx = next(i for i, s in enumerate(all_specs) if s.key == "학습")
    labels = Counter()
    lens = []
    src_counter = Counter()
    exposed = 0
    index_rows = []
    hardened_ids = set()
    if sp.key == "홀드아웃109":
        hp = _ROOT / "datasets/gold_real/holdout_eval.hardened.jsonl"
        if hp.exists():
            hardened_ids = {body_fingerprint(r["text"]) for r in _read_jsonl(hp)}

    with (d / "문서.jsonl").open("w", encoding="utf-8", newline="\n") as jf:
        for r in sp.rows:
            jf.write(json.dumps(r, ensure_ascii=False) + "\n")

    # 검수용 번호: 원본 순서(등급별로 정렬돼 있는 세트가 있다)를 그대로 쓰면 번호만으로 등급이 새므로
    # 세트 키 + 유효 doc_id 의 sha256 순으로 섞는다. 결정적이라 다시 돌려도 같다.
    order = sorted(range(len(sp.rows)),
                   key=lambda i: hashlib.sha256(f"{sp.key}:{_eid(sp.rows[i])}".encode()).hexdigest())
    for n, pos in enumerate(order, 1):
        r = sp.rows[pos]
        label = r.get(sp.label_key)
        labels[label] += 1
        lens.append(len(r["text"]))
        src_counter[r.get("label_source") or r.get("source") or "?"] += 1
        exp = _exposes_grade_token(r["text"])
        exposed += exp
        fname = f"{n:04d}.txt"   # doc_id 자체에 등급이 든 세트가 있어(golden100 v3) 파일명에 쓰지 않는다
        if bodies:
            with (d / "본문" / fname).open("w", encoding="utf-8", newline="") as bf:
                bf.write(r["text"])
        in_train_exact = in_train_near = ""
        if idx != train_idx:
            pd = ov["per_doc"][(idx, train_idx)]
            in_train_exact = int(pos in pd["exact"])
            in_train_near = int(pos in pd["hi"])
        index_rows.append({
            "역할": sp.role, "세트": sp.folder, "번호(본문파일)": n, "doc_id": _eid(r),
            "doc_id_원본없음": int(not r.get("doc_id")),
            "정답": label, "라벨출처": r.get("label_source", ""),
            "원천": r.get("source", ""), "글자수": len(r["text"]),
            "본문에_등급표기": int(exp),
            "학습셋과_본문일치": in_train_exact, f"학습셋과_근접중복(≥{NEAR_DUP_HI})": in_train_near,
            "s3fix_정정라벨(AI정독·미서명)": s3fix.get(body_fingerprint(r["text"]), ""),
            "hardened42_포함": int(body_fingerprint(r["text"]) in hardened_ids) if hardened_ids else "",
            "holdout_정정전라벨": r.get("label_before_correction_2026_09_20", ""),
            "review_status": r.get("review_status", ""),
            "사람서명": int(r.get("label_source") == "human_review"),
            "본문파일": f"본문/{fname}" if bodies else "",
        })

    if index_rows:
        with (d / "정답표.csv").open("w", encoding="utf-8-sig", newline="") as cf:
            w = csv.DictWriter(cf, fieldnames=list(index_rows[0].keys()))
            w.writeheader()
            w.writerows(index_rows)

    src_path = _ROOT / sp.src
    info = {
        "역할": sp.role, "세트": sp.folder, "키": sp.key, "원본": sp.src,
        "원본_sha256": hashlib.sha256(src_path.read_bytes()).hexdigest(),
        "건수": len(sp.rows),
        "정답분포": {g: labels.get(g, 0) for g in GRADES},
        "라벨출처_또는_원천": dict(src_counter.most_common()),
        "본문에_등급표기_문서수": exposed,
        "글자수": _quantiles(lens), "글자수_합": sum(lens),
        "사람서명_건수": sum(r["사람서명"] for r in index_rows),
        "doc_id_원본없음_건수": sum(r["doc_id_원본없음"] for r in index_rows),
        "s3fix_정정대상_건수": sum(1 for r in index_rows if r["s3fix_정정라벨(AI정독·미서명)"]),
    }
    md = [
        f"# {sp.role} / {sp.folder}", "",
        f"- 원본: `poc/{sp.src}` (sha256 `{info['원본_sha256'][:16]}…`)",
        f"- 건수: **{info['건수']:,}** · 정답 분포 " + " · ".join(f"{g} {info['정답분포'][g]}" for g in GRADES),
        f"- 글자수 중앙값 {info['글자수'].get('median')} · 최대 {info['글자수'].get('max')} · 3,000자 초과 {info['글자수'].get('over_3000')}건",
        f"- 본문에 등급 표기(특급·1급 비밀·대외비·TS/S1… 등)가 적힌 문서: **{exposed}건** "
        f"({exposed / max(1, len(sp.rows)) * 100:.1f}%) — 검수자가 답을 보고 읽게 되는 문서다",
        f"- 사람 서명 정답: **{info['사람서명_건수']}건**",
        f"- 원본에 doc_id 가 없는 행: **{info['doc_id_원본없음_건수']}건** (정답표의 doc_id 는 본문 지문 앞 16자로 만든 `nodoc-…` 대체값)",
        "", "## 자격", "", sp.note, "",
        "## 폴더 구성", "",
        "- `문서.jsonl` 원본 행(정답·라벨출처 포함)  ·  `본문/` 정답 없는 텍스트 (검수자에게 줄 것)  ·  `정답표.csv` (검수자에게 주지 말 것)",
        "", "## 라벨 출처 (또는 원천) 분포", "",
    ]
    md += [f"- {k}: {v:,}" for k, v in info["라벨출처_또는_원천"].items()]
    (d / "README.md").write_text("\n".join(md) + "\n", encoding="utf-8", newline="\n")
    info["_index_rows"] = index_rows
    return info


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="datasets/세트구분", help="출력 폴더(_ROOT 기준)")
    ap.add_argument("--no-bodies", action="store_true", help="본문/*.txt 를 만들지 않는다")
    args = ap.parse_args()

    out_root = (_ROOT / args.out).resolve()
    if _ROOT.resolve() not in out_root.parents:
        raise SystemExit(f"[중단] 출력은 {_ROOT} 아래여야 한다: {out_root}")

    _load_all()
    s3fix = _s3fix_labels()
    ov = _overlap(SETS)

    if out_root.exists():
        for sub in ("1_학습셋", "2_골든셋", "3_평가셋"):
            p = out_root / sub
            if p.exists():
                shutil.rmtree(p)
    out_root.mkdir(parents=True, exist_ok=True)

    infos = [
        _write_set(sp, out_root, bodies=not args.no_bodies, s3fix=s3fix, ov=ov, idx=i, all_specs=SETS)
        for i, sp in enumerate(SETS)
    ]

    # 검증: 파일 수 == 행 수, 본문 파일에 정답 파일명 없음
    for sp, info in zip(SETS, infos):
        d = out_root / sp.role / sp.folder
        if not args.no_bodies:
            n_files = len(list((d / "본문").glob("*.txt")))
            assert n_files == len(sp.rows), (sp.folder, n_files, len(sp.rows))
            assert all(re.fullmatch(r"\d{4}\.txt", p.name) for p in (d / "본문").glob("*.txt")), "본문 파일명은 번호뿐이어야 한다"

    all_rows = [r for info in infos for r in info.pop("_index_rows")]
    with (out_root / "문서색인.csv").open("w", encoding="utf-8-sig", newline="") as cf:
        w = csv.DictWriter(cf, fieldnames=list(all_rows[0].keys()))
        w.writeheader()
        w.writerows(all_rows)

    report = {"생성": "scripts/build_dataset_role_folders.py", "세트": infos, "겹침": ov["pairs"],
              "문턱": {"근접중복_높음": NEAR_DUP_HI, "근접중복_낮음": NEAR_DUP_LO,
                      "방법": "글자 3~4gram TF-IDF(sublinear, min_df=2) 코사인 · 본문 지문=dataset_usage.body_fingerprint(공백 제거 sha256)"}}
    (out_root / "세트별_겹침_점검.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

    print(f"[완료] {out_root}")
    for info in infos:
        print(f"  {info['역할']}/{info['세트']}: {info['건수']:,}건 {info['정답분포']} 등급표기 {info['본문에_등급표기_문서수']}")
    print("\n[겹침: A 의 문서 중 B 와 겹치는 수]")
    for k, v in ov["pairs"].items():
        print(f"  {k}: 본문일치 {v['본문지문_일치']} · id일치 {v['doc_id_일치']} · "
              f"근접≥{NEAR_DUP_HI} {v[f'근접중복_코사인≥{NEAR_DUP_HI}']} · ≥{NEAR_DUP_LO} {v[f'근접중복_코사인≥{NEAR_DUP_LO}']} / {v['A_문서수']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
