"""Validate the Proxy Gold pilot before it can be expanded to 1,000 candidates.

This is intentionally a content-readiness gate, not an accuracy claim.  It
checks traceable mechanics that must hold before human review: composition,
minimum document depth, grade rationale, source provenance, encoding health,
and repeated long sentences as an advisory signal.
"""
from __future__ import annotations

import html
import json
import re
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / "datasets" / "proxy_gold" / "single_document_candidates"
JSON_OUT = DIR / "pilot_100_quality_gate.v1.json"
HTML_OUT = DIR / "pilot_100_quality_gate.v1.html"
GRADES = ("TS", "S1", "S2", "S3")
EXPECTED_BY_TOTAL = {
    100: {"TS": 20, "S1": 25, "S2": 25, "S3": 30},
    400: {"TS": 80, "S1": 100, "S2": 100, "S3": 120},
    700: {"TS": 140, "S1": 175, "S2": 175, "S3": 210},
    1000: {"TS": 200, "S1": 250, "S2": 250, "S3": 300},
}


def read_document(meta: dict[str, object]) -> str:
    doc_id = str(meta["doc_id"])
    revision = meta.get("content_revision_path")
    if revision:
        path = DIR / str(revision)
        if path.exists():
            return path.read_text(encoding="utf-8")
    matches = sorted(DIR.glob(f"{doc_id}*.md"))
    if not matches:
        raise FileNotFoundError(doc_id)
    return matches[0].read_text(encoding="utf-8")


def grade_of(meta: dict[str, object]) -> str | None:
    grade = meta.get("intended_label")
    if grade in GRADES:
        return str(grade)
    if meta.get("document_origin") == "public_real":
        return "S3"
    return None


def repeated_sentence_share(documents: list[str]) -> float:
    chunks: Counter[str] = Counter()
    total = 0
    for document in documents:
        for raw in re.split(r"[\n.!?]+", document):
            chunk = re.sub(r"\s+", " ", raw).strip()
            if len(chunk) >= 50:
                chunks[chunk] += 1
                total += 1
    if not total:
        return 0.0
    return round(sum(count for count in chunks.values() if count >= 2) / total, 4)


def has_mojibake(text: str) -> bool:
    # U+00B7 is a normal Korean middle dot; the broken forms we need to catch
    # occupy the upper Latin-1 range instead.
    return "\ufffd" in text or any(0xC0 <= ord(char) <= 0xFF for char in text)


def build_report() -> dict[str, object]:
    metas = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(DIR.glob("*.metadata.json"))]
    documents: dict[str, str] = {}
    missing_documents: list[str] = []
    for meta in metas:
        try:
            documents[str(meta["doc_id"])] = read_document(meta)
        except FileNotFoundError:
            missing_documents.append(str(meta["doc_id"]))

    composition = Counter(grade_of(meta) for meta in metas)
    generated = [
        meta for meta in metas
        if str(meta["doc_id"]).startswith("GOLD-PILOT-") or str(meta["doc_id"]).startswith("GOLD-B")
    ]
    generated_by_grade: dict[str, list[dict[str, object]]] = {
        grade: [meta for meta in generated if grade_of(meta) == grade] for grade in GRADES
    }
    short = []
    rationale_missing = []
    encoding_failures = []
    repetition = {}
    for grade, entries in generated_by_grade.items():
        docs = [documents[str(meta["doc_id"])] for meta in entries if str(meta["doc_id"]) in documents]
        minimum = 3200
        for meta in entries:
            doc_id = str(meta["doc_id"])
            content = documents.get(doc_id, "")
            if len(content) < minimum:
                short.append({"doc_id": doc_id, "characters": len(content), "minimum": minimum})
            if f"\ub4f1\uae09 \uc81c\uc548 \uc0ac\uc720: {grade}" not in content:
                rationale_missing.append(doc_id)
            if has_mojibake(content):
                encoding_failures.append(doc_id)
        repetition[grade] = repeated_sentence_share(docs)

    actual = [meta for meta in metas if meta.get("document_origin") == "public_real"]
    provenance_missing = [
        str(meta["doc_id"])
        for meta in actual
        if not (meta.get("provenance") or {}).get("source_reference") or not meta.get("source_file_sha256")
    ]
    expected = EXPECTED_BY_TOTAL.get(len(metas))
    status = {
        "count": expected is not None,
        "composition": expected is not None and {grade: composition[grade] for grade in GRADES} == expected,
        "documents": not missing_documents,
        "minimum_depth": not short,
        "grade_rationale": not rationale_missing,
        "encoding": not encoding_failures,
        "actual_provenance": not provenance_missing,
    }
    hard_pass = all(status.values())
    warnings = []
    for grade, value in repetition.items():
        if value > 0.35:
            warnings.append({"grade": grade, "repeated_long_sentence_share": value, "message": "revise before large-scale expansion"})
    return {
        "artifact": "proxy_gold_pilot_100_quality_gate_v1",
        "scope": "Proxy Gold candidate readiness only; not Locked Gold or operational accuracy evidence",
        "hard_pass": hard_pass,
        "expand_to_1000_allowed": hard_pass and not warnings,
        "status": status,
        "total_candidates": len(metas),
        "composition": {grade: composition[grade] for grade in GRADES},
        "synthetic_candidates": sum(meta.get("document_origin") == "synthetic" for meta in metas),
        "actual_public_candidates": len(actual),
        "generated_candidates": len(generated),
        "short_documents": short,
        "missing_documents": missing_documents,
        "missing_rationale": rationale_missing,
        "encoding_failures": encoding_failures,
        "provenance_missing": provenance_missing,
        "repetition_advisories": warnings,
    }


def render_html(report: dict[str, object]) -> str:
    status = report["status"]
    status_rows = "".join(
        f"<tr><th>{html.escape(name)}</th><td class='{ 'pass' if ok else 'fail'}'>{'PASS' if ok else 'FAIL'}</td></tr>"
        for name, ok in status.items()
    )
    warnings = report["repetition_advisories"]
    warning_rows = "".join(
        f"<tr><th>{item['grade']}</th><td>{item['repeated_long_sentence_share']:.1%}</td><td>{html.escape(item['message'])}</td></tr>"
        for item in warnings
    ) or "<tr><td colspan='3'>No advisory</td></tr>"
    decision = "EXPANSION READY" if report["expand_to_1000_allowed"] else "HOLD EXPANSION"
    cls = "pass" if report["expand_to_1000_allowed"] else "fail"
    return f"""<!doctype html><html lang='ko'><meta charset='utf-8'>
<title>Proxy Gold Pilot Quality Gate</title><style>
body{{margin:0;background:#f5f5f3;color:#151515;font-family:Arial,'Malgun Gothic',sans-serif}}main{{max-width:1080px;margin:48px auto;padding:0 28px}}.eyebrow{{font:700 12px monospace;letter-spacing:.12em;color:#777}}h1{{font-size:42px;margin:12px 0}}.result{{border-left:6px solid #e52b3d;background:#fff;padding:24px;margin:28px 0;font-weight:800;font-size:24px}}.pass{{color:#137a4a;font-weight:800}}.fail{{color:#d62839;font-weight:800}}section{{background:#fff;border:1px solid #dededb;padding:22px;margin-top:18px}}table{{border-collapse:collapse;width:100%}}th,td{{padding:12px;border-bottom:1px solid #e7e7e4;text-align:left}}th{{width:42%;font-weight:600}}.note{{color:#666;line-height:1.65}}</style>
<main><div class='eyebrow'>PROXY GOLD / QUALITY GATE / PILOT 100</div><h1>1,000건 확장 전 품질 게이트</h1>
<p class='note'>이 결과는 후보 문서의 준비 상태만 말합니다. 사람 검수·실문서 출처·Locked Gold·실운영 정확도를 보증하지 않습니다.</p>
<div class='result {cls}'>{decision}</div>
<section><h2>기본 현황</h2><table><tr><th>후보 수</th><td>{report['total_candidates']}</td></tr><tr><th>등급 분포</th><td>{html.escape(json.dumps(report['composition'], ensure_ascii=False))}</td></tr><tr><th>합성 / 실제 공개문서</th><td>{report['synthetic_candidates']} / {report['actual_public_candidates']}</td></tr></table></section>
<section><h2>필수 통과 조건</h2><table>{status_rows}</table></section>
<section><h2>확장 전 개선 신호</h2><table><tr><th>등급</th><th>반복 긴 문장 비율</th><th>조치</th></tr>{warning_rows}</table></section>
</main></html>"""


def main() -> int:
    report = build_report()
    JSON_OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    HTML_OUT.write_text(render_html(report), encoding="utf-8")
    print(JSON_OUT)
    print(HTML_OUT)
    print(json.dumps({"hard_pass": report["hard_pass"], "expand_to_1000_allowed": report["expand_to_1000_allowed"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
