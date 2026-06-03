"""Step 2 (doc/32 §3): collect REAL patent technical content from KIPRIS as the
high-grade (TS/S1) proxy corpus.

Rationale
---------
A published patent is a trade secret its owner chose to disclose. Its abstract
(초록/astrtCont) + title is real, deep technical know-how — exactly the kind of
content that, as an undisclosed internal document, would be S1/TS. This is the
only publicly-obtainable REAL high-grade *content* (court rulings redact it;
the project's existing "secret" data is mislabeled public court text).

Provenance note (doc/22 §4.0): a PUBLISHED patent is public (S3) by provenance.
We use its CONTENT to teach the content model (Gate 2) what high-grade technical
material looks like. At inference, a real published patent carrying source=
"공개특허" is capped to S3 by the gate — no contradiction.

KIPRIS Plus Open API
--------------------
Register (free): https://plus.kipris.or.kr  →  마이페이지 → API 서비스 신청 →
  "특허실용신안 - 단어검색(getWordSearch)" service key.
Put the key in poc/.env as:   KIPRIS_SERVICE_KEY=...
getWordSearch returns 서지 + 초록(astrtCont) per item — one call yields content.

Usage
-----
  python scripts/kipris_collect.py --probe                 # check key + endpoint
  python scripts/kipris_collect.py --per-keyword 50        # collect
Output: datasets/patent_proxy/raw.jsonl  (text, label, grade_basis, app_no, field)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
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

# KIPRIS Plus — 단어검색(서지+초록). 응답 XML, item.astrtCont = 초록.
# 엔드포인트·키 파라미터(ServiceKey)는 2026-06-03 라이브 프로브로 확정
# (ServiceKey=DUMMY → resultCode 30 SERVICE_KEY_IS_NOT_REGISTERED = 파라미터 인식됨).
ENDPOINT = "http://plus.kipris.or.kr/kipo-api/kipi/patUtiliInfoSearchSevice/getWordSearch"

# 국가핵심기술 핵심분야 키워드 → TS (산업기술보호법 §9 지정분야 근사).
TS_KEYWORDS = [
    "EUV 노광", "반도체 식각 공정", "HBM 적층", "D램 셀", "낸드 플래시",
    "양극재 조성", "전고체 전지", "리튬 이차전지 전해질",
    "OLED 증착", "마이크로 LED", "자율주행 인지 알고리즘",
    "수소 연료전지 스택", "초고압 송전", "유도무기 제어", "위성 항법",
]
# 일반 고기술 특허 → S1 (영업비밀급 기술 노하우).
S1_KEYWORDS = [
    "제조 공정 최적화", "촉매 합성 방법", "공정 파라미터 제어",
    "정밀 가공 방법", "제어 알고리즘", "신약 후보물질 합성",
    "센서 신호 처리", "암호 키 관리", "추천 알고리즘 모델",
]


def _service_key() -> str:
    key = os.environ.get("KIPRIS_SERVICE_KEY")
    if not key:
        try:
            from dotenv import dotenv_values
            key = dotenv_values(_HERE.parent / ".env").get("KIPRIS_SERVICE_KEY")
        except Exception:
            key = None
    return key or ""


def _call(word: str, key: str, rows: int, page: int) -> bytes:
    qs = urllib.parse.urlencode(
        {"word": word, "ServiceKey": key, "numOfRows": rows, "pageNo": page,
         "patent": "true", "utility": "true"}
    )
    req = urllib.request.Request(f"{ENDPOINT}?{qs}")
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


class KiprisApiError(RuntimeError):
    """KIPRIS returned successYN=N (e.g. expired deadline, unregistered key)."""


def _check_response(xml_bytes: bytes) -> None:
    """Raise KiprisApiError if the API header reports failure (resultCode != 00)."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return
    hdr = root.find("header")
    if hdr is None:
        return
    success = (hdr.findtext("successYN") or "").strip().upper()
    code = (hdr.findtext("resultCode") or "").strip()
    msg = (hdr.findtext("resultMsg") or "").strip()
    if success == "N" or (code and code not in ("00", "0", "")):
        raise KiprisApiError(f"resultCode={code} {msg}")


def _parse_items(xml_bytes: bytes) -> list[dict]:
    """Extract title + abstract + application number from a getWordSearch response."""
    _check_response(xml_bytes)
    out = []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return out
    for item in root.iter("item"):
        def g(tag: str) -> str:
            el = item.find(tag)
            return (el.text or "").strip() if el is not None and el.text else ""
        title = g("inventionTitle") or g("inventionName")
        abstract = g("astrtCont")
        app_no = g("applicationNumber") or g("applicationNo")
        if abstract and len(abstract) >= 120:
            out.append({"title": title, "abstract": abstract, "app_no": app_no})
    return out


def collect(per_keyword: int, key: str, sleep: float) -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    plan = [("TS", k) for k in TS_KEYWORDS] + [("S1", k) for k in S1_KEYWORDS]
    for grade, word in plan:
        try:
            items = _parse_items(_call(word, key, per_keyword, 1))
        except Exception as e:  # noqa: BLE001
            print(f"[kipris] '{word}' 호출 실패: {repr(e)[:120]}", file=sys.stderr)
            continue
        kept = 0
        for it in items:
            dedup = (it["app_no"] or it["abstract"][:60])
            if dedup in seen:
                continue
            seen.add(dedup)
            text = f"{it['title']}\n\n{it['abstract']}"
            rows.append({
                "text": text,
                "label": grade,
                "source": "공개특허",        # provenance: published patent (gate → S3)
                "grade_basis": f"kipris:{word}",
                "domain": "기술",
                "app_no": it["app_no"],
                "label_source": "patent_proxy_content",
            })
            kept += 1
        print(f"[kipris] {grade} '{word}': {kept}건", file=sys.stderr)
        time.sleep(sleep)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="datasets/patent_proxy/raw.jsonl")
    ap.add_argument("--per-keyword", type=int, default=50)
    ap.add_argument("--sleep", type=float, default=0.5)
    ap.add_argument("--probe", action="store_true", help="키/엔드포인트 동작만 확인")
    args = ap.parse_args()

    key = _service_key()
    if not key:
        print("[kipris] KIPRIS_SERVICE_KEY 미설정 — poc/.env에 추가 후 재실행.\n"
              "  발급: https://plus.kipris.or.kr (무료) → API 서비스 신청 → getWordSearch", file=sys.stderr)
        return 2

    if args.probe:
        try:
            items = _parse_items(_call("반도체", key, 3, 1))
            print(f"[kipris] PROBE OK — '반도체' 초록 {len(items)}건 수신")
            if items:
                print("  샘플 제목:", items[0]["title"][:60])
            return 0
        except Exception as e:  # noqa: BLE001
            print(f"[kipris] PROBE 실패: {repr(e)[:200]}", file=sys.stderr)
            return 1

    rows = collect(args.per_keyword, key, args.sleep)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    print(f"[kipris] 수집 {len(rows)}건 → {out}  라벨분포={dict(Counter(r['label'] for r in rows))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
