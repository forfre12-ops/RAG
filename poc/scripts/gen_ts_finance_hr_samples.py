#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""감리 별첨 185(나) — 재무·인사 문서의 TS(특급기밀) 샘플을 소량 만든다.

왜 이 도구가 있는가(2026-09-11). 감리가 "편향성 완화를 위해 재무/인사 문서의 TS 샘플 추가"를
권고했다(인쇄 185쪽 (나)). 학습셋의 TS 는 연구·기술 쪽에 몰려 있어 재무·인사 문서를 TS 로
배운 사례가 드물다.

⚠ 8/24 방침(합성 신규 생성 중단)의 **예외**다. 2026-09-11 사용자 지시("서버 배포 제외하고
  전부 진행")로 이 칸만 좁게 연다. 기존 학습셋과 섞지 않고 별도 폴더에 둔다. 학습에 넣는
  것은 비교 실험(v7b)으로만 하고, 넣었을 때와 뺐을 때를 같은 자로 잰다.

생성: 로컬 Ollama — 외부 전송 없음. 등급명이 본문에 드러나면 버린다(구버전 합성의 33.8% 가
등급명을 본문에 적어 등급을 자기 노출했다). 실패(빈 본문·자리표시·비JSON)도 버린다 — 채우지 않는다.

사용:
    python scripts/gen_ts_finance_hr_samples.py --per-domain 12 --model qwen3:14b
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import io
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

_POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_POC / "src"))

# (문서 유형, 상황) — 둘 다 등급명을 쓰지 않고 상황으로 유도한다(생성기 v2 프롬프트와 같은 원칙).
SCENARIOS = {
    "finance": [
        ("인수 대가 산정 검토서", "비상장 경쟁사 인수를 위한 최종 제시가격과 협상 하한선을 이사회 보고 전에 정리한다"),
        ("자금조달 실행계획서", "공시 전 대규모 유상증자 규모·시점·배정 대상을 확정하기 위한 내부 계획이다"),
        ("내부감사 결과 보고서", "매출 인식 시점 조작 의혹에 대한 감사 결과와 재무제표 영향 추정치를 담는다"),
        ("사업부 매각 협상 전략서", "적자 사업부 매각의 희망가·양보 가능 범위·잠재 인수자별 전략을 정리한다"),
        ("손상차손 사전 검토 메모", "분기 결산 발표 전 대규모 자산 손상 반영 여부와 금액 시나리오를 검토한다"),
        ("원가 구조 분석서", "주력 제품의 부품별 실제 원가와 거래처별 마진을 협상 대응용으로 정리한다"),
    ],
    "hr": [
        ("구조조정 실행안", "희망퇴직 대상 부서·인원·선정 기준과 발표 일정을 경영진만 공유하기 위해 정리한다"),
        ("임원 보상 산정서", "주요 임원별 성과급·주식보상 산정 근거와 금액을 보상위원회 심의용으로 작성한다"),
        ("핵심인력 유지 계약안", "경쟁사 이직 제안을 받은 핵심 연구인력별 잔류 보상 조건을 정리한다"),
        ("경영진 승계 계획서", "대표이사 유고 시 후보자 순위와 평가 결과, 전환 절차를 정리한다"),
        ("단체교섭 대응 전략서", "올해 임금 교섭의 사측 양보 한도와 단계별 제시안, 쟁의 대비책을 정리한다"),
        ("징계 검토 보고서", "내부 제보로 조사 중인 임원의 비위 사실관계와 징계 수위 검토 의견을 담는다"),
    ],
}
# 등급명 노출 — 본문이 등급을 스스로 말하면 분류기는 본문 대신 그 단어를 배운다.
# [2026-09-11] 첫 판은 완성된 등급명('특급기밀'·'1급비밀')만 봤다. 학습셋(v7a)을 세어 보니 낱말
# 자체가 등급을 알려준다 — '기밀'이 든 행이 TS 의 69.1%(다른 등급 1.6~6.0%), '비밀'이 S1 의 77.3%,
# '대외비'가 S2 의 66.4%. 그래서 낱말 단위로 막는다('영업비밀'도 여기에 걸린다).
_GRADE_WORDS = re.compile(r"기밀|비밀|극비|대외비|일반\s*공개|[1-3]\s*급|\b(TS|S1|S2|S3)\b")


def _file_sha256(p: Path) -> str:
    """파일별 SHA-256 — 줄바꿈을 LF 로 맞춘 바이트 기준(= git 저장본 · .gitattributes *.jsonl eol=lf).

    명세에 파일별 해시가 없으면 보안대책 체크리스트 M10 의 계수가 어긋난다(build_nis_checklist_doc.py).
    """
    return hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _refilter(out: Path) -> int:
    """생성하지 않고 이미 만든 samples.jsonl 에 지금의 등급명 필터를 다시 건다.

    첫 판 필터로 받은 24건 중 5건이 '기밀'·'비밀'을 가졌다. 나머지는 고친 필터를 그대로
    통과하므로 다시 만들지 않는다 — 다시 만들면 걸러낸 것이 아니라 표본이 바뀐다.
    """
    p = out / "samples.jsonl"
    rows = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
    keep = [r for r in rows if not _GRADE_WORDS.search(r["text"])]
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in keep), encoding="utf-8", newline="\n")
    mp = out / "manifest.json"
    manifest = json.loads(mp.read_text(encoding="utf-8")) if mp.exists() else {}
    manifest["refilter"] = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "pattern": _GRADE_WORDS.pattern,
                            "before": len(rows), "dropped_grade_words": len(rows) - len(keep)}
    manifest["accepted"] = len(keep)
    manifest["by_domain"] = dict(Counter(r["domain"] for r in keep))
    manifest["files"] = {"samples.jsonl": {"rows": len(keep), "sha256": _file_sha256(p)}}
    mp.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    print(json.dumps(manifest, ensure_ascii=False))
    return 0 if keep else 1


def main(argv=None) -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="재무·인사 TS 샘플 소량 생성(로컬 LLM)")
    ap.add_argument("--per-domain", type=int, default=12)
    ap.add_argument("--model", default="qwen3:14b")
    ap.add_argument("--base-url", default="http://localhost:11434/v1")
    ap.add_argument("--out", default="datasets/synth_ts_fin_hr_20260911")
    ap.add_argument("--refilter", action="store_true", help="생성하지 않고 기존 samples.jsonl 에 필터만 다시 건다")
    a = ap.parse_args(argv)
    if a.refilter:
        return _refilter(_POC / a.out)

    from koipa.adapters.llm.local_openai_provider import LocalOpenAIProvider  # noqa: PLC0415
    from koipa.modules.m1_synthesis.generator import SynthRequest, SyntheticDocGenerator  # noqa: PLC0415

    llm = LocalOpenAIProvider(base_url=a.base_url, api_key="ollama", model=a.model, provider_label="ollama")
    gen = SyntheticDocGenerator(llm=llm)
    out = _POC / a.out
    out.mkdir(parents=True, exist_ok=True)
    rows, reasons = [], Counter()
    t0 = time.time()
    for domain, scen in SCENARIOS.items():
        for i in range(a.per_domain):
            doctype, ctx = scen[i % len(scen)]
            req = SynthRequest(target_grade="TS", domain=domain, count=1, len_min=900, len_max=2000,
                               scenario_context=ctx, document_type_hint=doctype)
            try:
                doc = gen.generate_one(req)
            except Exception as exc:  # noqa: BLE001 — 한 건 실패로 전체를 멈추지 않는다(사유는 센다)
                reasons[f"예외:{type(exc).__name__}"] += 1
                continue
            d = dataclasses.asdict(doc) if dataclasses.is_dataclass(doc) else dict(vars(doc))
            text = (d.get("body") or d.get("text") or d.get("content") or "").strip()
            src = str(d.get("label_source") or "")
            if "fallback" in src or "nonjson" in src:
                reasons[f"생성 실패:{src}"] += 1
                continue
            if len(text) < 400:
                reasons["본문 400자 미만"] += 1
                continue
            if _GRADE_WORDS.search(text):
                reasons["등급명 노출"] += 1
                continue
            h = hashlib.sha256(text.encode("utf-8")).hexdigest()
            rows.append({
                "doc_id": f"synth-tsfh-{h[:10]}", "text": text, "label": "TS", "domain": domain,
                "document_type": doctype, "scenario": ctx,
                "document_origin": "synthetic", "source": "synthetic_ts_fin_hr_20260911",
                "label_source": "synth_intent", "llm_provider": "ollama", "llm_model": a.model,
                "prompt_version": d.get("prompt_version") or d.get("body_prompt_version"),
                "audit_ref": "설계단계 감리 별첨 185(나)", "text_sha256": h,
            })
            print(f"[{len(rows):2d}] {domain} · {doctype} · {len(text)}자", flush=True)
    (out / "samples.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                                       encoding="utf-8", newline="\n")
    manifest = {"created": time.strftime("%Y-%m-%dT%H:%M:%S"), "model": a.model, "requested": a.per_domain * len(SCENARIOS),
                "accepted": len(rows), "rejected": dict(reasons), "by_domain": dict(Counter(r["domain"] for r in rows)),
                "elapsed_sec": round(time.time() - t0), "policy": "8/24 합성 중단 방침의 예외 — 2026-09-11 사용자 지시",
                "not_merged_into_training": True,
                "files": {"samples.jsonl": {"rows": len(rows), "sha256": _file_sha256(out / "samples.jsonl")}}}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    print(json.dumps(manifest, ensure_ascii=False))
    return 0 if rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
