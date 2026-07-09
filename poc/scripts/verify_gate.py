"""Quick verification of the provenance (non-publicity) gate, Gate 1.

Runs the SAME deployed content model (.env CLASSIFIER_MODEL_DIR) on real published rulings two ways:
  - WITHOUT source metadata  -> raw content model (expected: over-classifies)
  - WITH source="판례"        -> gate caps public-source docs to S3

Proves the gate fixes over-classification on known-provenance docs WITHOUT
touching the content model's high-grade detection (unlike the data relabel).
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

from lloydk.modules.m5_inference.pipeline import InferencePipeline  # noqa: E402
from lloydk.config import settings as _settings  # noqa: E402

# 게이트는 '실제 배포 중인' 콘텐츠 모델로 검증해야 의미 있음 = .env CLASSIFIER_MODEL_DIR.
MODEL = _settings.classifier_model_dir or "artifacts/classifier_p1_retrain_v4_clean/v-dd3abab9"
PREC = {"판례", "판례(1000+)", "판례(2000+)", "판례(3000+)"}


def main() -> int:
    files = sorted(Path("datasets/oss_corpus").glob("*.json"))
    docs = []
    for f in files:
        d = json.loads(f.read_text(encoding="utf-8"))
        if d.get("source") in PREC and len(d.get("body") or "") >= 500:
            docs.append(f"{d.get('title','')}\n\n{d['body'][:3000]}")
        if len(docs) >= 60:
            break

    pipe = InferencePipeline(model_dir=Path(MODEL))
    no_meta, with_meta = Counter(), Counter()
    for t in docs:
        r1 = pipe.run(t, return_evidence=False, metadata=None)
        r2 = pipe.run(t, return_evidence=False, metadata={"source": "판례"})
        no_meta[r1.label.value if hasattr(r1.label, "value") else str(r1.label)] += 1
        with_meta[r2.label.value if hasattr(r2.label, "value") else str(r2.label)] += 1

    n = len(docs)
    over_no = sum(v for k, v in no_meta.items() if k != "S3")
    over_yes = sum(v for k, v in with_meta.items() if k != "S3")
    print(f"real public rulings: {n}")
    print(f"WITHOUT metadata (raw content model): {dict(no_meta)}  over-classified={over_no}/{n} ({over_no/n:.0%})")
    print(f"WITH source=판례 (gate ON):          {dict(with_meta)}  over-classified={over_yes}/{n} ({over_yes/n:.0%})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
