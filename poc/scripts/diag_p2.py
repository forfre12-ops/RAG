import json, sys
from pathlib import Path

sys.path.insert(0, 'src')
sys.path.insert(0, 'scripts')

synth_dir = Path('datasets/synthetic_5k')

# load_corpus 방식
docs = []
for f in sorted(synth_dir.glob('*.json'))[:5]:
    d = json.loads(f.read_text(encoding='utf-8'))
    docs.append({'id': d['synth_id'], 'grade': d['target_grade']})

from p2_doc_derived_queries import build_doc_derived_queries
queries = build_doc_derived_queries(synth_dir, n_per_grade=2)

print('=== corpus 첫 5 ===')
for doc in docs:
    print(f'  {doc[\"id\"]!r}  {type(doc[\"id\"]).__name__}  grade={doc[\"grade\"]}')

print()
print('=== query 첫 4 ===')
for q in queries[:4]:
    print(f'  {q[\"expected_doc_id\"]!r}  {type(q[\"expected_doc_id\"]).__name__}  grade={q[\"expected_grade\"]}')

corpus_ids = {d['id'] for d in docs}
query_ids = {q['expected_doc_id'] for q in queries}
overlap = corpus_ids & query_ids
print(f'overlap count: {len(overlap)} / corpus_sample=5, queries=8')
