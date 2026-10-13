#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""받침과 어긋난 조사를 **후보별로 세고 형태를 보여 준다** — 고치기 전에 무엇을 고칠지 먼저 본다.

왜(2026-09-12). 골든 후보 생성기가 틀에 조사를 한 형태로 박아 둬서 본문에
"…남아 있는지 확인가 확정되지 않은" 같은 문장이 실려 나갔다. 생성기는 고쳤지만
([[label-conflict-signal-is-template-siblings-2026-09-12]]) **이미 만들어진 후보 1,055건은
그대로**다. 그걸 고치려면 먼저 "어떤 낱말 뒤에서 어긋났는가"를 알아야 한다.

⚠ 받침 규칙만으로 일괄 교정하면 안 된다. `불가`·`추가`·`증가` 처럼 **조사가 아닌 글자**가
   끝에 오는 낱말이 있고, 그것까지 바꾸면 "불이"가 된다. 그래서 이 도구는 고치지 않는다 —
   **형태별로 세어서 보여 주기만** 한다. 무엇을 고칠지는 사람이 목록을 보고 정한다.

사용:
    python scripts/scan_particle_mismatch.py                       # 기본 후보 풀
    python scripts/scan_particle_mismatch.py --pool <디렉터리> --top 40
    python scripts/scan_particle_mismatch.py --json reports/PARTICLE_MISMATCH.json
"""
from __future__ import annotations

import argparse
import collections
import io
import json
import re
import sys
from pathlib import Path

_POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_POC / "scripts"))

# 받침 있음 → 앞, 받침 없음 → 뒤
PAIRS = (("이", "가"), ("은", "는"), ("을", "를"), ("과", "와"))
# 쓰인 조사가 "받침 없는 말에 붙는 형태"일 때, 받침 있는 말에 맞는 형태로 바꿀 표
_TO_BATCHIM = {vowel: batchim for batchim, vowel in PAIRS}
# 그 반대
_TO_VOWEL = {batchim: vowel for batchim, vowel in PAIRS}

# 낱말 + 조사 + 공백. 낱말은 한글 2자 이상이어야 본다(한 글자면 조사인지 낱말인지 못 가른다).
_TOKEN = re.compile(r"([가-힣]{2,12})([이가은는을를과와])(?=\s)")


def has_batchim(ch: str) -> bool | None:
    code = ord(ch) - 0xAC00
    if not 0 <= code <= 11171:
        return None
    return code % 28 != 0


def mismatches(text: str) -> collections.Counter:
    """어긋난 (낱말, 쓰인조사, 맞는조사) 를 센다."""
    out: collections.Counter = collections.Counter()
    for m in _TOKEN.finditer(text):
        stem, particle = m.group(1), m.group(2)
        batchim = has_batchim(stem[-1])
        if batchim is None:
            continue
        # 받침이 있는데 '받침 없는 말용' 조사가 붙었으면 바꿔야 한다(반대도 같다).
        # 이미 맞는 형태면 표에 없어서 None 이 나온다.
        want = _TO_BATCHIM.get(particle) if batchim else _TO_VOWEL.get(particle)
        if want is None:
            continue
        out[(stem, particle, want)] += 1
    return out


def _load(pool: Path) -> list[tuple[str, str]]:
    from eval_on_clean_candidates import load_candidates
    import eval_on_clean_candidates as _src

    _src.ROOT = pool
    return [(c["doc_id"], c["text"]) for c in load_candidates() if (c.get("text") or "").strip()]


def main(argv=None) -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="받침과 어긋난 조사 계수")
    ap.add_argument("--pool", default=str(_POC / "datasets/proxy_gold/single_document_candidates"))
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--json", default="")
    a = ap.parse_args(argv)

    docs = _load(Path(a.pool).resolve())
    total: collections.Counter = collections.Counter()
    docs_hit = 0
    for _doc_id, text in docs:
        found = mismatches(text)
        if found:
            docs_hit += 1
        total.update(found)

    n_occ = sum(total.values())
    print("분모: 후보 %d건 · 어긋난 조사가 있는 후보 %d건 · 총 출현 %d회 · 형태 %d개"
          % (len(docs), docs_hit, n_occ, len(total)))
    print("\n형태별(많은 순 %d개) — '쓰인 것 → 맞는 것':" % a.top)
    for (stem, got, want), cnt in total.most_common(a.top):
        print("  %6d회  %s%s → %s%s" % (cnt, stem, got, stem, want))

    if a.json:
        out = _POC / a.json
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "n_candidates": len(docs), "n_docs_with_mismatch": docs_hit,
            "n_occurrences": n_occ,
            "forms": [{"stem": s, "got": g, "want": w, "count": c}
                      for (s, g, w), c in total.most_common()],
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        print("\n기록: %s" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
