"""p5 smoke samples JSON 생성 + p5_e2e_smoke.py 로더 방식으로 교체."""
import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

TEST_DIR    = Path("datasets/test_set_v2")
SAMPLES_JSON = Path("datasets/test_set_v2/smoke_samples.json")
P5_PATH     = Path("scripts/p5_e2e_smoke.py")

DOMAINS = ["tech", "business", "finance", "hr", "legal", "security"]
GRADES  = ["TS", "S1", "S2", "S3"]

# 1. smoke_samples.json 생성
samples = []
for domain in DOMAINS:
    for grade in GRADES:
        candidates = sorted(TEST_DIR.glob(f"{domain}_{grade}_*.json"))
        chosen = None
        for f in candidates:
            rec = json.loads(f.read_text("utf-8"))
            if rec.get("label_match"):
                chosen = rec
                break
        if not chosen and candidates:
            chosen = json.loads(candidates[0].read_text("utf-8"))
        if chosen:
            samples.append({
                "doc_id":         f"smoke-{domain}-{grade}-001",
                "tenant_id":      "poc",
                "title":          chosen.get("title", "")[:80],
                "content":        chosen.get("body", "")[:1000],
                "expected_grade": grade,
            })

SAMPLES_JSON.write_text(json.dumps(samples, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"smoke_samples.json 저장: {len(samples)}건")

# 2. p5_e2e_smoke.py SAMPLES 블록을 JSON 로더로 교체
loader_code = (
    "import json as _json\n"
    "SAMPLES = _json.loads(\n"
    "    (Path(__file__).parent.parent / 'datasets/test_set_v2/smoke_samples.json')\n"
    "    .read_text('utf-8')\n"
    ")\n"
)

src = P5_PATH.read_text("utf-8")
import re
new_src = re.sub(r"SAMPLES = \[.*?\]\n", loader_code, src, flags=re.DOTALL)

# Path import 확인
if "from pathlib import Path" not in new_src:
    new_src = "from pathlib import Path\n" + new_src

P5_PATH.write_text(new_src, encoding="utf-8")

# 문법 검증
import py_compile, tempfile, os
tmp = tempfile.mktemp(suffix=".py")
Path(tmp).write_text(new_src, encoding="utf-8")
try:
    py_compile.compile(tmp, doraise=True)
    print(f"p5_e2e_smoke.py 교체 완료 — 문법 OK")
except py_compile.PyCompileError as e:
    print(f"문법 오류: {e}")
finally:
    if os.path.exists(tmp):
        os.unlink(tmp)
