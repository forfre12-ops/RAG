#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""문서가 적은 **런타임 사실**이 코드·배포 구성과 맞는지 대조한다.

왜 필요한가(2026-08-29). 하루에 커밋 14건이 들어온 날, 발주처 회신 묶음에서 어긋난
자리 아홉이 나왔다. 그중 다섯은 다른 세션이 코드를 고치는 동안 문서가 따라오지 못한
것이었다.

  · 칼럼 10개를 스키마에서 뺐는데 문서 한 줄이 옛 수를 그대로 들고 있었다
  · 마이그레이션 head 가 두 판 앞서갔는데 문서는 옛 판을 "코드와 일치"라고 적었다
  · 프록시 타임아웃을 120초에서 300초로 고쳐 놓고, 문서는 같은 날 저녁에
    "120초라 끊긴다"고 적었다
  · 파일 업로드 상한이 설정에 그대로 있는데 "실측 후 회신하겠다"고 비워 뒀다
  · SSE 스트리밍 엔드포인트가 배포돼 있는데 회신문에 한 줄도 없었다

전부 **파일 하나만 열면 되는 것**이었다. 사람이 읽어서 잡는 방식은 커밋 속도를
따라가지 못한다. 그래서 도구로 만든다.

`audit_doc_claims.py` 와 보는 축이 다르다 — 그쪽은 *문서끼리·ORM 과의 수치 정합*을
보고, 이쪽은 *문서 ↔ 실행 구성(설정값·마이그레이션·라우트·프록시)* 을 본다.

⚠ 판정하지 않는다. "문서가 X 라 적었는데 코드는 Y 다"를 드러낼 뿐이다. 시점이 다른
스냅샷을 일부러 적은 자리도 있으므로, 어느 쪽이 맞는지는 사람이 근거를 열어 정한다.

사용:
    cd poc && TESTING=1 python scripts/audit_doc_runtime.py
    cd poc && TESTING=1 python scripts/audit_doc_runtime.py --root ../doc/result/KL_AI자료_2026-08
"""
from __future__ import annotations

import argparse
import glob
import io
import os
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

os.environ.setdefault("TESTING", "1")
# 라우트·설정은 배포 프로파일에 따라 달라진다 — lite 계열은 학습·합성 라우터를 아예
# 싣지 않는다. 문서는 운영 배포(full-train) 기준으로 쓰였으므로 koipa 를 import 하기
# **전에** 프로파일을 고정한다. 이러면 경로 수가 시험 서버(223)의 openapi.json 과 같아진다.
# ⚠ 이름은 접두사 없는 DEPLOY_PROFILE 이다. KOIPA_DEPLOY_PROFILE 로도 세우던 줄이
# 있었으나 그 이름으로는 안 먹는다(실측: 그것만 세우면 lite-noapi 로 떨어진다) — 지웠다.
os.environ.setdefault("DEPLOY_PROFILE", "full-train")

_REPO = _HERE.parent.parent
_DEFAULT_ROOT = _REPO / "doc" / "result" / "KL_회신_2026-08-28"


# ── 참값 뽑기 ────────────────────────────────────────────────────────────────
def truth() -> dict:
    """코드·구성 파일에서 참값을 읽는다. 문서는 보지 않는다."""
    from koipa.config import _PROFILE_DEFAULTS, Settings  # noqa: PLC0415
    from koipa.schemas.classify import ClassifyRequest, ClassifyResponse  # noqa: PLC0415
    from koipa.services.review_reasons import REVIEW_GATE_TAGS  # noqa: PLC0415

    fields = Settings.model_fields
    prof = _PROFILE_DEFAULTS.get("full-train", {})

    t = {
        "max_upload_mb": fields["max_upload_mb"].default,
        "max_request_body_mb": fields["max_request_body_mb"].default,
        "max_seq_len": fields["max_seq_len"].default,
        "fnr_rule_ts_threshold": fields["fnr_rule_ts_threshold"].default,
        "review_confidence_threshold": prof.get("review_confidence_threshold"),
        "classifier_temperature": prof.get("classifier_temperature"),
        "classifier_escalation_tau": prof.get("classifier_escalation_tau"),
        "content_max_length": _content_max_length(ClassifyRequest),
        "response_fields": len(ClassifyResponse.model_fields),
        # extraction-gate 는 진단·시연 엔드포인트(/documents/analyze) 전용이라
        # 운영 분류 경로의 게이트 수에서 뺀다 — 문서도 15 로 적고 그 사실을 밝힌다.
        "review_gates": len([t for t in REVIEW_GATE_TAGS if t != "extraction-gate"]),
        "alembic_head": _alembic_head(),
        "routes": _routes(),
    }
    t.update(_nginx())
    return t


def _content_max_length(model):
    """pydantic 이 max_length 를 어디에 두든 찾아낸다(판에 따라 자리가 다르다)."""
    f = model.model_fields["content"]
    for meta in getattr(f, "metadata", ()) or ():
        v = getattr(meta, "max_length", None)
        if v:
            return int(v)
    return None


def _alembic_head() -> str:
    """자식이 없는 리비전이 head 다. 여럿이면 어긋난 상태이므로 그대로 드러낸다."""
    revs, downs = {}, set()
    for f in glob.glob(str(_HERE.parent / "alembic" / "versions" / "*.py")):
        s = io.open(f, encoding="utf-8").read()
        m = re.search(r'^revision(?:\s*:\s*str)?\s*=\s*["\']([^"\']+)', s, re.M)
        dn = re.search(r'^down_revision(?:\s*:[^=]+)?\s*=\s*["\']([^"\']+)', s, re.M)
        if m:
            revs[m.group(1)] = os.path.basename(f)
            if dn:
                downs.add(dn.group(1))
    heads = sorted(r for r in revs if r not in downs)
    return heads[0] if len(heads) == 1 else ("|".join(heads) or "?")


def _routes() -> set:
    """FastAPI 앱을 세워 실제 라우트를 뽑는다. 배포본 openapi 조회 없이도 같은 답이 나온다."""
    try:
        from koipa.api.app import app  # noqa: PLC0415
        # app.routes 는 이 코드베이스에서 라우터 래퍼를 담고 있어 경로가 안 나온다.
        # openapi() 는 배포본 /openapi.json 과 같은 목록이라 그쪽을 정본으로 쓴다.
        return set(app.openapi().get("paths", {}))
    except Exception as exc:  # noqa: BLE001
        print("  [경고] 라우트를 못 읽었다(%s) — 경로 검사는 건너뛴다" % type(exc).__name__)
        return set()


def _nginx() -> dict:
    p = _HERE.parent / "infra" / "mtls" / "nginx.mtls.conf"
    if not p.exists():
        return {}
    s = io.open(p, encoding="utf-8").read()
    out = {}
    m = re.search(r"proxy_read_timeout\s+(\d+)s", s)
    if m:
        out["proxy_read_timeout_s"] = int(m.group(1))
    m = re.search(r"client_max_body_size\s+(\d+)M", s)
    if m:
        out["client_max_body_size_mb"] = int(m.group(1))
    return out


# ── 문서가 주장하는 값 ───────────────────────────────────────────────────────
# (표시명, 참값 키, 문서에서 값을 걷는 정규식, 값 변환)
CHECKS = [
    ("멀티파트 업로드 상한(MB)", "max_upload_mb", r"멀티파트[^<]{0,24}?(\d+)\s*MB", int),
    ("요청 본문 상한(MB)", "max_request_body_mb", r"요청 본문[^<]{0,24}?(\d+)\s*MB", int),
    ("검수 신뢰도 임계", "review_confidence_threshold",
     r"(?:검수[^<]{0,14}임계|자동확정[^<]{0,10}임계)[^<]{0,26}?(0\.\d+)", float),
    ("서빙 temperature", "classifier_temperature",
     r"(?:온도 보정|서빙 temperature)(?:</td><td[^>]*>|[^<]{0,20})(\d\.\d+)", float),
    ("룰 상향 임계 TS", "fnr_rule_ts_threshold", r"TS\s*(\d\.\d)\s*(?:·|&middot;)\s*S1", float),
    # "N,NNN 자"만 보면 청크 길이·칼럼 주석까지 걸린다 — 본문 상한을 말하는 자리만 본다.
    ("본문 상한(자)", "content_max_length",
     r"(?:본문 상한|content[^<]{0,12}상한)[^<]{0,20}?(\d{1,3}(?:,\d{3})+)\s*자",
     lambda v: int(v.replace(",", ""))),
    ("응답 필드 수", "response_fields", r"현행\s*(\d+)\s*필드|(\d+)\s*필드다", int),
    # "게이트 N개"만 보면 재학습 배포 게이트(7종)까지 걸린다 — 검수 문맥일 때만 센다.
    ("검수 게이트 수", "review_gates", r"(?:검수|라우팅)[^<]{0,20}게이트\s*(\d+)\s*개|게이트\s*(\d+)\s*개가[^<]{0,24}검수", int),
    ("프록시 read timeout(초)", "proxy_read_timeout_s",
     r"proxy_read_timeout</code>\s*<b>(\d+)초", int),
    ("프록시 본문 상한(MB)", "client_max_body_size_mb",
     r"client_max_body_size</code>\s*<b>(\d+)M", int),
]

_REV_RE = re.compile(r"\b([0-9a-f]{12})\b")
# 옛 판을 이력으로 인용하는 자리(“이 칼럼은 X 가 만든다”)까지 걸면 오탐이 된다.
# **현행이라고 주장하는 문맥**에서만 head 와 대조한다.
_REV_CTX = re.compile(r"head|현행|현재 판|적용돼|스키마 판|alembic_version")


_SRC_BLOB = None


def _in_source(path: str) -> bool:
    """openapi 에 안 실리는 경로가 있다 — /api/v1/metrics-prom 은 include_in_schema=False
    로 숨어 있지만 서버에서 200 을 돌려준다. 소스에 리터럴로 있으면 실재로 본다."""
    global _SRC_BLOB
    if _SRC_BLOB is None:
        buf = []
        for dp, _dn, fn in os.walk(_HERE.parent / "src"):
            if "__pycache__" in dp:
                continue
            for f in fn:
                if f.endswith(".py"):
                    buf.append(io.open(os.path.join(dp, f), encoding="utf-8", errors="ignore").read())
        _SRC_BLOB = chr(10).join(buf)
    return path in _SRC_BLOB or path.replace("/api/v1", "") in _SRC_BLOB


def _blank_but_newlines(text: str) -> str:
    """줄 번호를 유지한 채 내용만 지운다 — 줄바꿈만 남기고 나머지는 공백."""
    return "".join(ch if ch == chr(10) else " " for ch in text)


def _first_group(m):
    for g in m.groups():
        if g:
            return g
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(_DEFAULT_ROOT), help="검사할 문서 폴더")
    args = ap.parse_args()
    root = Path(args.root)

    T = truth()
    print("=" * 74)
    print(" 코드·구성에서 뽑은 참값")
    print("=" * 74)
    for k in ("max_upload_mb", "max_request_body_mb", "review_confidence_threshold",
              "classifier_temperature", "classifier_escalation_tau", "content_max_length",
              "response_fields", "review_gates", "alembic_head",
              "proxy_read_timeout_s", "client_max_body_size_mb"):
        if T.get(k) is not None:
            print("  %-32s %s" % (k, T[k]))
    print("  %-32s %d개" % ("routes", len(T["routes"])))

    files = sorted(glob.glob(str(root / "**" / "*.html"), recursive=True))
    print()
    print("=" * 74)
    print(" 문서 %d개 대조 — %s" % (len(files), root))
    print("=" * 74)

    findings = []
    for f in files:
        rel = os.path.relpath(f, root)
        s = io.open(f, encoding="utf-8").read()
        # <script> 안은 화면용 데이터 덩어리다(골든셋 근거표 등). 수치 대조 대상이 아니며
        # 줄 번호는 유지해야 하므로 같은 길이의 공백으로 덮는다.
        s = re.sub(r"<script[^>]*>.*?</script>",
                   lambda m: _blank_but_newlines(m.group(0)), s, flags=re.S)
        for name, key, pat, conv in CHECKS:
            if T.get(key) is None:
                continue
            for m in re.finditer(pat, s):
                raw = _first_group(m)
                if raw is None:
                    continue
                try:
                    got = conv(raw)
                except Exception:  # noqa: BLE001
                    continue
                if abs(float(got) - float(T[key])) > 1e-9:
                    findings.append((rel, s[:m.start()].count("\n") + 1, name, got, T[key]))
        for m in _REV_RE.finditer(s):
            rev = m.group(1)
            if _REV_CTX.search(s[max(0, m.start() - 120):m.start()]) and rev != T["alembic_head"]:
                findings.append(
                    (rel, s[:m.start()].count("\n") + 1, "alembic 판", rev, T["alembic_head"]))
        if T["routes"]:
            for m in re.finditer(r"/api/v1/[a-z0-9_\-/{}.]+", s):
                path = m.group(0).rstrip("/.")
                # 문서·스키마 노출용 메타 경로는 openapi() 목록에 자기 자신이 안 들어간다.
                if path.rsplit("/", 1)[-1] in ("openapi.json", "docs", "redoc"):
                    continue
                # 초안(발주처 제안서)의 경로를 인용해 "우리와 다르다"고 적은 자리는
                # 우리 라우트가 아니다 — 그 절 전체를 건너뛴다(표 안의 행에는 '초안'이라는
                # 말이 없고 절 제목에만 있다).
                h2 = s.rfind("<h2>", 0, m.start())
                sec = s[h2:s.find("</h2>", h2)] if h2 >= 0 else ""
                line_start = s.rfind(chr(10), 0, m.start()) + 1
                line_end = s.find(chr(10), m.end())
                line = s[line_start:line_end if line_end > 0 else len(s)]
                if "초안" in sec or "초안" in line:
                    continue
                # "현재 라우터에 없다"고 밝혀 둔 자리는 경고이지 주장이 아니다.
                # [2026-09-05] 상대편(KL 관리시스템)이 여는 경로를 적은 자리도 같다 —
                # IF-03 콜백 수신 경로가 그렇다. 엔진은 callback_url 로 보내기만 한다.
                if re.search(r"없다|없습니다|존재하지 않|미구현"
                             r"|라우트가 아닙니다|수신 경로", line):
                    continue
                if (path not in T["routes"]
                        and not any(p.startswith(path) for p in T["routes"])
                        and not _in_source(path)):
                    findings.append(
                        (rel, s[:m.start()].count("\n") + 1, "없는 경로", path, "앱 라우트에 없음"))

    if not findings:
        print("  어긋난 자리 없음")
        return 0
    print("  어긋난 자리 %d건 — 각 자리를 열어 근거를 확인할 것" % len(findings))
    print()
    for rel, line, name, got, want in findings:
        print("  %s:%d" % (rel, line))
        print("      %s — 문서 %r · 참값 %r" % (name, got, want))
    return 1


if __name__ == "__main__":
    sys.exit(main())
