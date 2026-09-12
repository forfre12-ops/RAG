"""요소 모델의 예측을 '등급 확률 분포'로 변환해 덤프한다 - 서빙 게이트 시뮬레이션 입력.

왜 필요한가. 서빙 게이트(신뢰도 임계 0.70 · 합의 게이트)는 **등급 하나와 그 신뢰도**를
받도록 돼 있다. 요소 모델은 헤드가 셋이라 그대로는 못 넣는다. 헤드별 최대확률의 최소값
같은 임시방편을 쓰면 배포본 신뢰도와 축이 달라져 비교가 무의미해진다.

정합적인 정의는 하나뿐이다. 세 헤드를 독립으로 보고 27개 조합의 결합확률을 구한 뒤
grade_from_svm() 으로 등급별로 합산한다:

    P(등급 g) = sum over (s,v,m) where grade_from_svm(s,v,m)==g of P(s)P(v)P(m)

이러면 배포본 분류기가 내는 등급 확률과 같은 의미가 되어 임계 0.70 을 그대로 적용할 수
있다. 독립 가정은 근사다(헤드가 백본을 공유하므로 실제로는 상관이 있다) - 그 한계는
리포트에 남긴다.

GPU venv 로 돌린다. 게이트 시뮬레이션은 별도 스크립트가 생산 venv 로 이어받는다
(룰 엔진이 koipa 전체 의존을 끌어오므로 환경을 섞지 않는다).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

FACTORS = ("secrecy", "value", "management")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="요소 모델 -> 등급 확률 덤프")
    parser.add_argument("--model", required=True)
    parser.add_argument("--eval", required=True)
    parser.add_argument("--chunk-chars", type=int, default=1600)
    parser.add_argument("--chunk-overlap", type=int, default=300)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    sys.stdout.reconfigure(encoding="utf-8")

    import torch
    import torch.nn.functional as F
    from transformers import AutoTokenizer

    from koipa.modules.m3_labeling.rule_engine import grade_from_svm
    from koipa.modules.m5_inference.factor_model import cls_to_worst, head_classes

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from train_factor_model import _build_model, _load  # noqa: PLC0415

    mdir = Path(args.model)
    meta = json.loads((mdir / "meta.json").read_text("utf-8"))
    tok = AutoTokenizer.from_pretrained(str(mdir))
    # 헤드 폭은 체크포인트가 갖고 있다. 종전엔 _build_model 의 기본값(3)으로 만들어
    # 4-class 체크포인트(artifacts/factor_model 의 20개 중 18개)를 못 실었다.
    state = torch.load(mdir / "model.pt", map_location="cuda", weights_only=True)
    n_classes = int(meta.get("classes") or head_classes(state))
    model = _build_model(meta["base_model"], torch, n_classes)
    model.load_state_dict(state)
    model.cuda().eval()

    # 조합 -> 등급 대응표를 미리 만든다(매 문서 재계산 방지). 3-class 면 27, 4-class 면 64.
    # 판정식은 3-레벨이므로 unknown 은 cls_to_worst 로 접어 넣는다 — 여러 조합이 같은
    # 3-레벨 삼항으로 접히지만 등급별 확률 합산이라 질량은 그대로 보존된다.
    fold = cls_to_worst if n_classes == 4 else int
    combos = [(s, v, m)
              for s in range(n_classes) for v in range(n_classes) for m in range(n_classes)]
    combo_grade = {c: grade_from_svm(*(fold(x) for x in c)) for c in combos}

    texts, truth, grades = _load(Path(args.eval))
    step = max(1, args.chunk_chars - args.chunk_overlap)
    rows = []
    with torch.no_grad():
        for idx, text in enumerate(texts):
            pieces = [text[i:i + args.chunk_chars]
                      for i in range(0, max(1, len(text)), step)] or [text]
            probs = []
            for i in range(0, len(pieces), args.batch):
                enc = tok(pieces[i:i + args.batch], truncation=True,
                          max_length=meta["max_len"], padding="max_length",
                          return_tensors="pt").to("cuda")
                lg = model(enc["input_ids"], enc["attention_mask"])
                probs.append(torch.stack([F.softmax(x, -1) for x in lg]))
            p = torch.cat(probs, dim=1).mean(1)  # [3, n_classes] 요소 x 수준
            pf = p.tolist()
            gp: dict[str, float] = {}
            for (s, v, m), g in combo_grade.items():
                gp[g] = gp.get(g, 0.0) + pf[0][s] * pf[1][v] * pf[2][m]
            rows.append({
                "idx": idx,
                "truth_grade": grades[idx],
                "truth_factors": dict(zip(FACTORS, truth[idx])),
                "pred_factors": {f: int(max(range(n_classes), key=lambda k: pf[i][k]))
                                 for i, f in enumerate(FACTORS)},
                "grade_probs": {g: round(v, 6) for g, v in sorted(gp.items())},
                "n_chunks": len(pieces),
            })
            if (idx + 1) % 100 == 0:
                print(f"  {idx + 1}/{len(texts)}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                   encoding="utf-8")
    print(f"[dump] {out}  ({len(rows)}건)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
