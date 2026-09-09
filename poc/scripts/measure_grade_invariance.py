"""등급 불변성을 잰다 — 내용을 바꾸지 않은 변형에도 등급이 그대로인가.

왜 필요한가. 지금까지 우리가 잰 것은 **데이터 쪽 결합**이었다 — 길이·제목·부서군이
라벨과 얼마나 붙어 있는가(scripts/measure_shortcut_bias.py, holdout 4종의 길이 누설).
그것은 "라벨이 부수 정보와 상관 있다"는 관찰이지, **모델이 실제로 그 부수 정보를 보고
등급을 정하는가**에 대한 답이 아니다. 상관은 개입으로만 인과가 된다.

이 도구는 개입한다. 같은 문서에 **내용을 바꾸지 않는 변형**을 걸고 배포본을 다시 태워
등급이 움직이는지 본다. 라벨을 쓰지 않으므로 평가셋 봉인과 무관하다 — 성능을 재는 것이
아니라 **같은 것을 같게 판단하는가**를 재기 때문이다.

축 — 앞의 셋은 등급이 **변하면 안 된다**. 넷째는 통제군으로, 변하는 것이 정상이다.

    pad_short/pad_long   중립 문장을 덧붙인다(길이 ↑, 내용 불변)
    whitespace           앞뒤 공백·줄바꿈만 넣는다(완전 무의미)
    reorder              문장 순서를 뒤집는다(문장 집합 동일)
    [통제] source_public  source_type=public → source-prior cap 이 발동해야 정상

읽는 법. 변화율만 보면 안 된다. **방향**을 함께 본다.

    하향(등급이 낮아짐)  미탐 방향이다. 이것이 이 도구가 찾는 주 결함이다.
    상향(등급이 높아짐)  과대 방향이다. 덜 위험하지만 불안정성의 증거다.

실행:
    CLASSIFIER_MODEL_DIR=artifacts/classifier_p1_v5_clean/v-fe4b386b \
    METADATA_FLOOR_ENABLED=true TESTING=1 python scripts/measure_grade_invariance.py

    --sets hardened42,v3_final800   특정 셋만
    --limit 100                     셋당 문서 수 상한(빠른 확인용)
"""
from __future__ import annotations

try:  # 콘솔 출구 고정 — cp949 에서 em dash 하나에 죽지 않게
    from _cli_io import force_utf8_stdio  # noqa: E402
except ImportError:  # 패키지로 import 될 때(릴리스 번들의 import 폐쇄 검사)
    from scripts._cli_io import force_utf8_stdio  # noqa: E402

force_utf8_stdio()

import argparse
import json
import os
import sys
import uuid
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

# 배포본 거동을 재려면 프로필 플래그까지 맞춰야 한다. 기본 OFF 인 게이트를 안 켜고 재면
# 배포본에 있는 경로가 통째로 빠진 채 측정되고, 그 결과가 오류가 아니라 **그럴듯한 표**로
# 나온다(실측 2026-09-10: 여섯 조건 전부 같음 → "M 은 효과가 없다"로 읽혔다).
os.environ.setdefault("METADATA_FLOOR_ENABLED", "true")
os.environ.setdefault("TESTING", "1")

# 평가면 — regression_gate.py 와 같은 파일을 본다(두 도구가 다른 것을 재면 대조가 안 된다).
EVAL_SETS: dict[str, str] = {
    "hardened42": "datasets/gold_real/holdout_eval.hardened.jsonl",
    "holdout109": "datasets/gold_real/_rejudge_claude/holdout109_provenance_corrected.jsonl",
    "v3_final800": "datasets/proxy_eval/direct_authored_proxy_eval_split.v3/final_800.locked.jsonl",
}

# 덧붙이는 문장 — 어느 등급도 가리키지 않는 사무 문구여야 한다. 등급 어휘(비밀·대외비·
# 기밀)나 공개 어휘(공고·공표)가 섞이면 그것 자체가 신호가 되어 축이 오염된다.
_FILLER = [
    "본 문서의 문의는 담당 부서로 주시기 바랍니다.",
    "작성 서식은 사내 표준 양식을 따릅니다.",
    "본 자료의 쪽 번호는 하단에 표기되어 있습니다.",
    "오탈자는 발견 즉시 담당자에게 알려 주시기 바랍니다.",
    "본 문서는 국문으로 작성되었습니다.",
]

_GRADE_ORDER = {"TS": 0, "S1": 1, "S2": 2, "S3": 3}


def _rows(path: Path, limit: int | None) -> list[str]:
    """본문만 뽑는다 — 라벨은 쓰지 않는다(불변성은 정답이 필요 없다)."""
    out: list[str] = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        text = str(row.get("text") or row.get("content") or "").strip()
        if len(text) >= 40:          # 너무 짧으면 빈본문 게이트가 먼저 걸려 축이 안 보인다
            out.append(text)
        if limit and len(out) >= limit:
            break
    return out


def _split_sentences(text: str) -> list[str]:
    parts = [p.strip() for p in text.replace("\n", " ").split(".") if p.strip()]
    return [p + "." for p in parts]


def variants(text: str) -> dict[str, tuple[str, dict | None]]:
    """(변형 이름) → (본문, 메타데이터). 메타데이터가 None 이면 안 보낸다."""
    sentences = _split_sentences(text)
    return {
        "pad_short": (text + " " + _FILLER[0], None),
        "pad_long": (text + " " + " ".join(_FILLER), None),
        "whitespace": ("\n\n  " + text + "  \n\n", None),
        "reorder": (" ".join(reversed(sentences)) if len(sentences) > 1 else text, None),
        "source_public": (text, {"source_type": "public"}),      # 통제군
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sets", default=",".join(EVAL_SETS))
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--out", default="reports/grade_invariance.json")
    args = ap.parse_args()

    from koipa.schemas.classify import ClassifyRequest      # noqa: PLC0415
    from koipa.services.classify_service import ClassifyService  # noqa: PLC0415

    svc = ClassifyService()
    model = getattr(svc.inference, "model_dir", None)
    model_name = model.name if model else "rule-fallback"
    if getattr(svc.inference, "_model", None) is None:
        # 분류기 없이 재면 룰 단독 경로를 재고 배포본을 잰 것처럼 보고하게 된다.
        print("분류기가 로드되지 않았다 — CLASSIFIER_MODEL_DIR 를 주고 다시 돌릴 것.", file=sys.stderr)
        return 2

    def grade_of(text: str, metadata: dict | None) -> str:
        res = svc.classify(
            ClassifyRequest(doc_id=str(uuid.uuid4()), content=text, metadata=metadata)
        )
        return res.label.value if hasattr(res.label, "value") else str(res.label)

    report: dict = {"model": model_name, "sets": {}}
    for name in [s.strip() for s in args.sets.split(",") if s.strip()]:
        path = _ROOT / EVAL_SETS.get(name, name)
        texts = _rows(path, args.limit)
        if not texts:
            print(f"  [건너뜀] {name} — 읽을 본문이 없다: {path}")
            continue
        axes: dict[str, Counter] = {}
        for text in texts:
            base = grade_of(text, None)
            for axis, (mutated, meta) in variants(text).items():
                got = grade_of(mutated, meta)
                c = axes.setdefault(axis, Counter())
                c["n"] += 1
                if got == base:
                    c["same"] += 1
                elif _GRADE_ORDER.get(got, 9) > _GRADE_ORDER.get(base, 9):
                    c["down"] += 1        # 등급이 낮아졌다 = 미탐 방향
                else:
                    c["up"] += 1
        report["sets"][name] = {a: dict(c) for a, c in axes.items()}
        print(f"\n== {name} — 문서 {len(texts)}건 · 모델 {model_name}")
        print(f"   {'축':<14}{'n':>5}{'불변':>7}{'불변율':>9}{'하향(미탐)':>11}{'상향':>7}")
        for axis, c in axes.items():
            n = c["n"] or 1
            tag = "  [통제군]" if axis == "source_public" else ""
            print(
                f"   {axis:<14}{c['n']:>5}{c['same']:>7}{c['same']/n:>8.1%}"
                f"{c['down']:>11}{c['up']:>7}{tag}"
            )

    out = _ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n기록: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
