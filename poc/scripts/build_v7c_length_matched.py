#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""v7c — v7a 학습셋에 재무·인사 합성 TS 를 **학습셋 TS 의 길이 분포에 맞춰 잘라** 더한다. 길이 한 축만 바꾼 개입.

왜 이 도구가 있는가(2026-09-11). v7b(v7a + 합성 TS 19건 원문)는 공개문서 300건 중 TS 판정을
23~32건(v7a 세 번 학습)에서 142건으로 옮겼다. 합성 19건은 학습셋 TS 와 길이가 다르다 —
중앙 994자 대 440자로, 오히려 학습셋 S3(1,080자)에 가깝다. 번호 제목 비율도 84% 대 12% 다.
길이가 원인인지 가르려고 합성 본문을 앞에서부터 문장 경계로 잘라, 학습셋 TS 길이 분포에서
뽑은 길이에 맞춘다. v7a 를 다시 뽑지 않고 커밋된 v7a 학습셋을 그대로 바탕으로 쓴다 — 풀을 다시
훑으면 그사이 생긴 데이터가 섞여 바탕이 달라진다.

⚠ 자르면 뒷부분 내용도 빠진다 — 길이와 내용이 완전히 갈리지는 않는다. 번호 제목은 앞머리에도 있어 남는다.
⚠ 산출 학습셋은 커밋하지 않는다(비교 실험용). 이 스크립트 + 커밋된 입력 + 시드로 다시 만든다.

사용:
    python scripts/build_v7c_length_matched.py --out datasets/labeled_p1_v7c_revision_tsfh_len
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import shutil
import statistics
import sys
from pathlib import Path

_POC = Path(__file__).resolve().parents[1]
_END = re.compile(r"[.!?]\s|\n")


def _jsonl(p: Path) -> list[dict]:
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def _file_sha256(p: Path) -> str:
    """줄바꿈을 LF 로 맞춘 바이트 기준(= git 저장본) — 명세 해시 규칙(build_nis_checklist_doc M10)과 같다."""
    return hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def trim_to(text: str, target: int, *, keep_ratio: float = 0.6) -> str:
    """앞에서부터 target 글자 안의 마지막 문장 경계에서 자른다. 경계가 너무 앞(60% 미만)이면 target 에서 자른다."""
    if len(text) <= target:
        return text
    cut = text[:target]
    ends = [m.end() for m in _END.finditer(cut)]
    if ends and ends[-1] >= int(target * keep_ratio):
        cut = cut[:ends[-1]]
    return cut.strip()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="v7c 학습셋(길이 맞춘 합성 TS)")
    ap.add_argument("--base", default="datasets/labeled_p1_v7a_revision")
    ap.add_argument("--extra", default="datasets/synth_ts_fin_hr_20260911/samples.jsonl")
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args(argv)

    base, out = _POC / a.base, _POC / a.out
    train = _jsonl(base / "train.jsonl")
    ts_lens = sorted(len(r["text"]) for r in train if r["label"] == "TS")
    rng = random.Random(a.seed)
    added = []
    for r in _jsonl(_POC / a.extra):
        target = rng.choice(ts_lens)
        text = trim_to(r["text"], target)
        added.append({"text": text, "label": r["label"], "augment_source": a.extra,
                      "augment_reason": "extra_length_matched", "orig_chars": len(r["text"]),
                      "target_chars": target})

    out.mkdir(parents=True, exist_ok=True)
    (out / "train.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in train + added),
                                     encoding="utf-8", newline="\n")
    for s in ("val", "test"):
        shutil.copyfile(base / f"{s}.jsonl", out / f"{s}.jsonl")
    manifest = {
        "base": a.base, "base_rows": len(train), "extra": a.extra, "extra_rows": len(added), "seed": a.seed,
        "extra_chars_before": {"median": statistics.median(x["orig_chars"] for x in added)},
        "extra_chars_after": {"median": statistics.median(len(x["text"]) for x in added)},
        "base_ts_chars": {"median": statistics.median(ts_lens)},
        "not_committed": "비교 실험용 — 스크립트와 커밋된 입력으로 다시 만든다",
        "files": {n: {"rows": sum(1 for line in (out / n).read_text(encoding="utf-8").splitlines() if line.strip()),
                      "sha256": _file_sha256(out / n)} for n in ("train.jsonl", "val.jsonl", "test.jsonl")},
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps({k: v for k, v in manifest.items() if k != "files"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
