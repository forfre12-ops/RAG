"""공개문서 과분류(FPR) 평가용 negative 코퍼스 수집 — 평가 전용, 학습 투입 금지.

왜 이 스크립트가 있나
---------------------
현행 v5 배포 게이트의 "공개문서 과분류" 축은 재료가 **공개 판례 단일 성격**이다
(scripts/eval_real_public_fpr.py, n=400). 판례는 문체·어휘가 한 종류라, 실제 회원사
환경에서 마주칠 공개문서(공시·행정문서·공고문)를 비밀로 잘못 올리는지는 측정되지 않는다.
이 스크립트는 그 측정 재료를 넓힌다.

학습에 넣지 말 것 (중요)
------------------------
공개 판례·특허를 고등급 프록시로 **학습**시켰던 v4_step2 는 공개문서 과분류 79% 를
유발했고, 제외한 v4_clean·v5 에서 4% 로 내려갔다. 여기서 수집하는 문서도 같은 성격이다.
산출물 manifest 에 usage=eval_only 를 박고 학습 빌더가 읽는 경로와 분리해 둔다.

라벨의 권위
-----------
DART 공시·공공데이터포털 공개문서는 **법정·제도적으로 공개된 서류**다. 따라서 정답은
정의상 S3 이고, S3 이외 예측은 전부 과분류(false positive)로 센다. LLM 심판도 사람
라벨링도 필요 없는 축이라, 이 코퍼스는 machine-silver 한계를 받지 않는다.

두 가지 수집 경로
-----------------
  dart  : OpenDART OpenAPI 자동 수집 (인증키 무료·즉시 발급)
  files : 수동 내려받은 문서 파일(data.go.kr 파일데이터 등) → 프로젝트 M2 추출기로 본문화
          data.go.kr 는 데이터셋마다 스키마가 달라 범용 자동수집이 불가능하다. 파일을
          받아 --raw-dir 에 넣으면 hwp/pdf/docx/xlsx 를 배포본과 동일한 추출기로 처리한다.

사용
----
  # 1) DART 인증키 발급(무료·즉시): https://opendart.fss.or.kr/  → 환경변수로 전달
  export OPENDART_API_KEY=...            # PowerShell: $env:OPENDART_API_KEY="..."
  python scripts/collect_public_eval_negatives.py --source dart --dry-run
  python scripts/collect_public_eval_negatives.py --source dart --max-docs 300

  # 2) data.go.kr 파일데이터 수동 다운로드 후
  python scripts/collect_public_eval_negatives.py --source files \
      --raw-dir datasets/raw/D2_public_data_portal --label 공공데이터포털

  # 3) 게이트에 물리기
  python scripts/eval_real_public_fpr.py --extra-dir datasets/public_eval_negatives

결정론: 수집 대상은 접수번호/파일명 정렬 후 앞에서 자른다(무작위 샘플링 없음).
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_POC = _HERE.parent
if str(_POC / "src") not in sys.path:
    sys.path.insert(0, str(_POC / "src"))
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except (AttributeError, ValueError):
    pass

OUT_DIR = _POC / "datasets" / "public_eval_negatives"

DART_LIST_URL = "https://opendart.fss.or.kr/api/list.json"
DART_DOC_URL = "https://opendart.fss.or.kr/api/document.xml"

# OpenDART status code → 사람이 읽는 사유. 무음 실패 금지.
DART_STATUS = {
    "000": "정상",
    "010": "등록되지 않은 키",
    "011": "사용할 수 없는 키(오픈API 이용점검·권한 정지)",
    "012": "접근할 수 없는 IP",
    "013": "조회된 데이터 없음",
    "014": "파일이 존재하지 않음",
    "020": "요청 제한 초과(일 20,000건)",
    "100": "필드의 부적절한 값",
    "101": "부적절한 접근",
    "800": "시스템 점검 중",
    "900": "정의되지 않은 오류",
    "901": "사용자 계정의 개인정보보유기간 만료",
}

# DART 공시서류 원본 XML 의 태그를 걷어내고 본문만 남긴다.
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t ]+")
_NL_RE = re.compile(r"\n{3,}")
_ENTITY = {"&lt;": "<", "&gt;": ">", "&amp;": "&", "&quot;": '"', "&apos;": "'", "&nbsp;": " "}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha16(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _norm_text(raw: str) -> str:
    txt = _TAG_RE.sub("\n", raw)
    for ent, ch in _ENTITY.items():
        txt = txt.replace(ent, ch)
    txt = _WS_RE.sub(" ", txt)
    txt = "\n".join(line.strip() for line in txt.splitlines())
    return _NL_RE.sub("\n\n", txt).strip()


def _http_get(url: str, params: dict[str, str], timeout: int = 60) -> bytes:
    full = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(full, headers={"User-Agent": "lloydk-eval-collector/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (고정 도메인)
        return resp.read()


# ── DART ────────────────────────────────────────────────────────────────────
def _date_windows(bgn: str, end: str, days: int = 89) -> list[tuple[str, str]]:
    """[bgn, end] 를 days 이하 구간으로 분할.

    OpenDART 공시검색은 corp_code 없이 조회하면 검색기간이 3개월로 제한된다
    (status=100). 긴 구간을 넣으면 조용히 잘리는 게 아니라 오류라서, 호출부에서
    창을 쪼개 순회해야 한다.
    """
    d0 = datetime.strptime(bgn, "%Y%m%d").date()
    d1 = datetime.strptime(end, "%Y%m%d").date()
    if d1 < d0:
        raise SystemExit(f"[ERR] --end-de({end}) 가 --bgn-de({bgn}) 보다 앞섭니다")
    out: list[tuple[str, str]] = []
    cur = d0
    while cur <= d1:
        nxt = min(cur + timedelta(days=days), d1)
        out.append((cur.strftime("%Y%m%d"), nxt.strftime("%Y%m%d")))
        cur = nxt + timedelta(days=1)
    return out


def dart_list(key: str, bgn: str, end: str, ptype: str, corp_cls: str, want: int,
              sleep_ms: int) -> list[dict]:
    """공시검색 — 접수번호 목록. 3개월 창을 순회하며 want 건까지 모은다."""
    out: list[dict] = []
    for w_bgn, w_end in _date_windows(bgn, end):
        if len(out) >= want:
            break
        page = 1
        while len(out) < want and page <= 20:
            body = _http_get(DART_LIST_URL, {
                "crtfc_key": key, "bgn_de": w_bgn, "end_de": w_end, "pblntf_ty": ptype,
                "corp_cls": corp_cls, "page_no": str(page), "page_count": "100",
            })
            payload = json.loads(body.decode("utf-8"))
            status = str(payload.get("status"))
            if status == "013":       # 이 창에 데이터 없음 — 다음 창으로
                break
            if status != "000":
                reason = DART_STATUS.get(status, "알 수 없음")
                raise SystemExit(
                    f"[ERR] OpenDART list status={status} ({reason}) — {payload.get('message','')}")
            items = payload.get("list") or []
            if not items:
                break
            out.extend(items)
            if page >= int(payload.get("total_page") or 1):
                break
            page += 1
            time.sleep(sleep_ms / 1000)
        print(f"[dart]   창 {w_bgn}~{w_end} → 누적 {len(out)}건")
        time.sleep(sleep_ms / 1000)
    # 결정론: 접수번호 오름차순 고정 후 절단.
    out.sort(key=lambda r: str(r.get("rcept_no", "")))
    return out[:want]


def dart_document_text(key: str, rcept_no: str) -> tuple[str, str | None]:
    """공시서류 원본(ZIP of XML) → 본문 텍스트. (text, error) 반환."""
    body = _http_get(DART_DOC_URL, {"crtfc_key": key, "rcept_no": rcept_no})
    # 오류일 때는 ZIP 이 아니라 XML(<result><status>...)로 온다.
    if not body[:2] == b"PK":
        head = body[:400].decode("utf-8", errors="replace")
        m = re.search(r"<status>(\d+)</status>", head)
        code = m.group(1) if m else "?"
        return "", f"status={code} ({DART_STATUS.get(code, '알 수 없음')})"
    chunks: list[str] = []
    with zipfile.ZipFile(io.BytesIO(body)) as zf:
        for name in sorted(zf.namelist()):
            if not name.lower().endswith((".xml", ".html", ".htm")):
                continue
            raw = zf.read(name)
            for enc in ("utf-8", "cp949", "euc-kr"):
                try:
                    chunks.append(raw.decode(enc))
                    break
                except UnicodeDecodeError:
                    continue
    if not chunks:
        return "", "zip 내 XML 없음"
    return _norm_text("\n".join(chunks)), None


# 사업보고서 표지·목차 제거용 앵커. 목차에도 같은 제목이 있으므로 "앞 60% 안에서
# 마지막 출현"을 본문 시작으로 본다.
_DART_CONTENT_ANCHORS = ("I. 회사의 개요", "1. 회사의 개요", "회사의 개요")


def _dart_content_start(text: str) -> int:
    """표지·목차가 끝나는 지점.

    사업보고서 앞 2~3천 자는 표지(제출법인 유형·대표이사·주소)와 목차라 문서마다
    거의 동일하다. 그대로 두면 평가가 본문 앞부분만 보는 구조에서 200건이 같은
    보일러플레이트로 수렴해 과분류 측정이 퇴화한다. 잘라낸 위치는 레코드에
    content_offset 으로 남겨 추적 가능하게 둔다.
    """
    limit = int(len(text) * 0.6)
    for anchor in _DART_CONTENT_ANCHORS:
        pos = text.rfind(anchor, 0, limit)
        if pos > 0:
            return pos
    return 0


def collect_dart(args: argparse.Namespace, key: str) -> list[dict]:
    print(f"[dart] 공시검색 {args.bgn_de}~{args.end_de} (pblntf_ty={args.report_type}, "
          f"corp_cls={args.corp_cls}) 최대 {args.max_docs}건")
    listing = dart_list(key, args.bgn_de, args.end_de, args.report_type, args.corp_cls,
                        args.max_docs, args.sleep_ms)
    print(f"[dart]   목록 확보: {len(listing)}건")
    if args.dry_run:
        for r in listing[:10]:
            print(f"[dart]   - {r.get('rcept_no')} {r.get('corp_name')} / {r.get('report_nm')}")
        print("[dart] --dry-run: 본문 내려받지 않고 종료")
        return []

    docs: list[dict] = []
    short = failed = 0
    for i, r in enumerate(listing, 1):
        rcept = str(r.get("rcept_no", ""))
        text, err = dart_document_text(key, rcept)
        offset = _dart_content_start(text) if text else 0
        body = text[offset: offset + args.max_chars]
        if err:
            failed += 1
            print(f"[dart]   ! {rcept} 본문 실패: {err}", file=sys.stderr)
        elif len(body) < args.min_len:
            short += 1
        else:
            docs.append({
                "title": f"{r.get('corp_name','')} {r.get('report_nm','')}".strip(),
                "body": body,
                "source": "DART",
                "domain": "",
                "grade": "S3",
                "grade_basis": "법정 공시서류 — 정의상 공개(S3)",
                "upstream": {"api": "opendart", "rcept_no": rcept,
                             "corp_code": r.get("corp_code"), "rcept_dt": r.get("rcept_dt"),
                             "content_offset": offset},
            })
        if i % 20 == 0:
            print(f"[dart]   {i}/{len(listing)} 처리 (수집 {len(docs)} · 짧음 {short} · 실패 {failed})")
        time.sleep(args.sleep_ms / 1000)
    print(f"[dart] 수집 {len(docs)}건 (본문부족 {short} · 실패 {failed})")
    return docs


# ── 수동 다운로드 파일 (data.go.kr 등) ───────────────────────────────────────
def collect_files(args: argparse.Namespace) -> list[dict]:
    from lloydk.modules.m2_preprocess.extractor import extract  # 배포본과 동일한 추출기

    raw_dir = Path(args.raw_dir)
    if not raw_dir.is_dir():
        raise SystemExit(f"[ERR] --raw-dir 없음: {raw_dir}")
    paths = [p for p in sorted(raw_dir.rglob("*")) if p.is_file()
             and p.suffix.lower() not in {".md", ".json", ".yaml", ".yml"}]
    print(f"[files] {raw_dir} 파일 {len(paths)}건 → 추출 (label={args.label})")
    if args.dry_run:
        for p in paths[:10]:
            print(f"[files]   - {p.relative_to(raw_dir)}")
        print("[files] --dry-run: 추출하지 않고 종료")
        return []

    docs: list[dict] = []
    short = failed = 0
    for p in paths:
        res = extract(p)
        if res.error or not res.text:
            failed += 1
            print(f"[files]   ! {p.name} 추출 실패: {res.error}", file=sys.stderr)
            continue
        if len(res.text) < args.min_len:
            short += 1
            continue
        docs.append({
            "title": p.stem,
            "body": res.text[: args.max_chars],
            "source": args.label,
            "domain": "",
            "grade": "S3",
            "grade_basis": "공개 배포 문서 — 정의상 공개(S3)",
            "upstream": {"file": str(p.relative_to(raw_dir)), "extract_method": res.method,
                         "extract_quality": res.quality},
        })
    print(f"[files] 수집 {len(docs)}건 (본문부족 {short} · 실패 {failed})")
    return docs


# ── 저장 ────────────────────────────────────────────────────────────────────
def write_corpus(docs: list[dict], label: str) -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    written = 0
    for d in docs:
        key = _sha16(d["body"][:2000])
        if key in seen:
            continue
        seen.add(key)
        d["synth_id"] = key
        d["collected_at"] = _now()
        (OUT_DIR / f"{key}.json").write_text(
            json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        written += 1

    existing = [f for f in sorted(OUT_DIR.glob("*.json")) if f.name != "manifest.json"]
    by_source: dict[str, int] = {}
    for f in existing:
        try:
            s = json.loads(f.read_text(encoding="utf-8")).get("source", "?")
        except (OSError, json.JSONDecodeError):
            continue
        by_source[s] = by_source.get(s, 0) + 1

    (OUT_DIR / "manifest.json").write_text(json.dumps({
        "dataset": "datasets/public_eval_negatives",
        "purpose": "공개문서 과분류(FPR) 측정 전용 negative 코퍼스",
        "usage": "eval_only",
        "training_prohibited": True,
        "training_prohibited_reason":
            "공개 판례·특허를 고등급 프록시로 학습한 v4_step2 가 공개문서 과분류 79%를 유발했다"
            "(제외한 v4_clean·v5 는 4%). 이 코퍼스도 같은 성격이라 학습 투입 금지.",
        "truth_label": "S3 (정의상 공개) — S3 이외 예측은 전부 과분류",
        "consumed_by": "scripts/eval_real_public_fpr.py --extra-dir datasets/public_eval_negatives",
        "docs_total": len(existing),
        "by_source": by_source,
        "updated_at": _now(),
        "last_run_label": label,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return written


def main() -> int:
    ap = argparse.ArgumentParser(description="공개문서 FPR 평가용 negative 코퍼스 수집(평가 전용)")
    ap.add_argument("--source", choices=("dart", "files"), required=True)
    ap.add_argument("--api-key", default=None, help="OpenDART 인증키(미지정 시 OPENDART_API_KEY)")
    ap.add_argument("--bgn-de", default="20250101", help="DART 공시검색 시작일 YYYYMMDD")
    ap.add_argument("--end-de", default="20251231", help="DART 공시검색 종료일 YYYYMMDD")
    ap.add_argument("--report-type", default="A", help="DART 공시유형 (A=정기공시)")
    ap.add_argument("--corp-cls", default="Y", help="DART 법인구분 (Y=유가·K=코스닥)")
    ap.add_argument("--raw-dir", default="datasets/raw/D2_public_data_portal",
                    help="--source files: 수동 다운로드 문서 폴더")
    ap.add_argument("--label", default=None, help="--source files: source 값(기본 공공데이터포털)")
    ap.add_argument("--max-docs", type=int, default=300)
    ap.add_argument("--min-len", type=int, default=800, help="본문 최소 길이(짧으면 버림)")
    ap.add_argument("--max-chars", type=int, default=20000, help="문서당 저장 상한")
    ap.add_argument("--sleep-ms", type=int, default=250, help="API 호출 간격")
    ap.add_argument("--dry-run", action="store_true", help="목록만 확인하고 저장하지 않음")
    args = ap.parse_args()

    if args.source == "dart":
        key = args.api_key or os.environ.get("OPENDART_API_KEY", "")
        if not key:
            print("[ERR] OpenDART 인증키가 없습니다. https://opendart.fss.or.kr/ 에서 무료·즉시 발급 후\n"
                  "      OPENDART_API_KEY 환경변수로 넣거나 --api-key 로 전달하세요.", file=sys.stderr)
            return 2
        docs = collect_dart(args, key)
        label = "dart"
    else:
        args.label = args.label or "공공데이터포털"
        docs = collect_files(args)
        label = args.label

    if not docs:
        return 0
    written = write_corpus(docs, label)
    print(f"[out] {OUT_DIR} 에 {written}건 저장(중복 제외) · manifest.json 갱신")
    print("[out] 다음: python scripts/eval_real_public_fpr.py --extra-dir datasets/public_eval_negatives")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
