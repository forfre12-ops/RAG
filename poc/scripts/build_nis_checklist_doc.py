# -*- coding: utf-8 -*-
"""국정원 AI 보안대책 체크리스트(부록1) 제출본 HTML 생성.

왜 생성기인가(2026-09-08). 체크리스트는 57개 항목이고 근거는 코드에 있다. 손으로 적으면
코드가 바뀌어도 문서가 그대로 남는다 - 이 리포에서 이미 겪은 문제다(문서-코드 어긋남).
근거의 유무는 audit_nis_ai_security.py 가 실제로 파일을 열어 판정하고, 이 스크립트는
그 결과에 사람이 쓴 설명과 대안을 붙여 문서로 만든다.

    audit_nis_ai_security.py   근거가 있는가 (기계 판정)
    build_nis_checklist_doc.py 그것을 어떻게 설명하고 무엇이 부족한가 (사람 작성)

서식은 감리문서 정본(KL_질의사항_회신서.html)의 style·nav 를 그대로 읽어 쓴다.
자체 클래스를 만들지 않는다 - 2026-08-29 지시.

사용:
    python scripts/build_nis_checklist_doc.py
    python scripts/build_nis_checklist_doc.py --check   # 다시 생성해 현재 파일과 같은지만 본다
"""
from __future__ import annotations

import argparse
import html
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

try:
    from _cli_io import force_utf8_stdio
except ImportError:
    from scripts._cli_io import force_utf8_stdio

force_utf8_stdio()

from audit_nis_ai_security import audit  # noqa: E402

_POC = Path(__file__).resolve().parents[1]
_REPO = _POC.parent
_TEMPLATE = _REPO / "doc" / "result" / "KL_회신_2026-08-28" / "KL_질의사항_회신서.html"
_OUT = _REPO / "doc" / "result" / "KL_AI자료_2026-08_미첨부문서" / "AI시스템_보안대책_체크리스트.html"

# ── 항목별 설명과 대안 ───────────────────────────────────────────────────────
#
# evidence: 무엇으로 그 대책을 세웠는가 (사람이 읽는 한 문장)
# gap:      부족한 것이 있으면 무엇이 어떻게 부족한가
# remedy:   그 부족을 어떻게 메울 것인가 (구체적으로 - 이름만 적지 않는다)
NOTES: dict[str, dict] = {
    "M01": {"evidence": "상용 LLM 반출 허용목록(public_real·synthetic)으로 출처가 확인된 데이터만 외부로 보낸다. 출처 미상은 자동 차단하고, 한 건이라도 섞이면 실행분 전체를 폐쇄망 처리한다."},
    "M02": {"evidence": "의존성은 uv.lock 으로 잠그고 CI 가 잠금본으로만 설치한다. 컨테이너 이미지는 태그가 아닌 digest(@sha256)로 고정한다. 오픈소스 라이선스는 dump_licenses 로 대장화한다."},
    "M03": {"evidence": "전처리 단계에 PII 마스커가 있고, 합성문서는 품질 정책(SYNTHETIC_QUALITY_POLICY)으로 검사한다. 학습셋은 등급 누출 게이트(grade_token_exposed·tell_coverage)를 통과해야 학습에 쓰인다."},
    "M04": {"evidence": "원본 문서는 AES-256-GCM(EncryptingStorage)으로 저장한다. 운영 프로파일(onprem-local·full-train)에서 강제 ON 이며, 암호화 키가 없으면 기동이 거부된다(평문 저장 차단)."},
    "M05": {"evidence": "역할 기반 접근통제(X-Actor-Role)와 포털 JWT 로그인을 쓴다. 골든셋 관리 콘솔은 공유 API 키를 거부하고 실계정 JWT 만 받는다 - 누가 등급을 정했는지 남기기 위해서다."},
    "M06": {"evidence": "기관 내부 보고·승인 절차. 시스템이 대신할 수 없다."},
    "M07": {"evidence": "TS·S1·S2·S3 4등급 체계로 데이터를 나누고, 등급별·도메인별 커버리지 격자로 구성 현황을 본다. 평가용 정답은 사람 서명을 거친 것만 편입한다."},
    "M08": {"evidence": "감사 체인(audit_chain)으로 기록을 연결해 변조를 탐지한다. 학습 실행은 이력 테이블에 남는다."},
    "M09": {"evidence": "모든 요청이 감사 미들웨어를 지나며 입·출력이 기록된다. 지표는 Prometheus 로 수집한다."},
    "M10": {"evidence": "데이터셋은 매니페스트로 관리하고 파일마다 sha256 을 적는다. 서버 이관 시에도 매니페스트로 대조해 전송 중 손상을 잡는다."},
    "M11": {"evidence": "SBOM(구성요소 명세)을 산출하고 릴리스 매니페스트로 버전·변경이력을 남긴다."},
    "M12": {"evidence": "빌드 SHA 를 이미지에 각인하고 배포 시 대조한다. 서빙 파이프라인은 계약 해시(contract_sha256)가 어긋나면 모델 로드를 거부한다."},
    "M13": {"evidence": "입력은 PII 마스킹을 거치고, 출력이 외부 상용 LLM 으로 나갈 때는 출처 허용목록으로 차단한다."},
    "M14": {"evidence": "본문 크기 상한 미들웨어가 파싱보다 먼저 과대 요청을 끊는다. 업로드 확장자·크기도 제한한다."},
    "M15": {"evidence": "분류기와 규칙 판정을 합의 게이트로 겹쳐 둔다. 안전 게이트 3종이 꺼지면 기동 자체가 차단된다(게이트 OFF 로 조용히 운영되는 것을 막는다)."},
    "M16": {"evidence": "모델 볼륨을 읽기전용으로 마운트하고 폐쇄망에 배치한다. 모델 활성 전환·롤백은 관리자 권한 뒤에 있다. (체크리스트 문구가 '암호화하거나 접근권한을 통제'이므로 접근통제로 충족한다)"},
    "M17": {"caveat": "2026-09-08 신규. 배포 검증 항목이라 <strong>새 서버 배포 시점에 처음 수행</strong>된다.",
            "evidence": "DB·Redis·API 를 호스트 루프백에만 바인드하고, 외부 노출은 역방향 프록시(nginx mTLS 종단)가 맡는다. 프록시가 옵션 기동이라 켜지지 않을 수 있으므로, 배포 검증 스크립트가 <strong>실제로 뜬 컨테이너</strong>를 보고 앱이 외부에 직접 서 있으면 경고한다(YAML 이 아니라 docker ps 를 본다 - 서버에서 만든 노출 설정이 배포마다 되살아난 전례가 있다)."},
    "M18": {"caveat": "루프백 기본값은 2026-09-08 변경분으로 <strong>새 서버 배포 시 반영</strong>된다(기존 운영본은 배포 전까지 종전 설정). mTLS <strong>운영 인증서(PKI)는 발주기관 발급 대상</strong>이며 현재 저장소에는 개발용 자체서명 인증서만 있다.",
            "evidence": "mTLS 프록시가 클라이언트 인증서를 검증하고(ssl_verify_client on) TLS 1.3 만 허용한다. 콘솔 토큰은 HttpOnly 쿠키로 전달한다. 앱 자신은 루프백에만 서고(기본값 127.0.0.1), 외부로 열려면 .env 에 <code>API_BIND</code> 를 명시해야 한다 - 노출이 기본값이 아니라 결정이 되게 했다."},
    "M19": {"evidence": "역할 검사로 접근 가능한 기능을 나누고, 컨테이너는 비-root uid 로 돈다. 호스트 사전점검 스크립트가 uid 불일치를 미리 잡는다."},
    "M20": {"evidence": "모델 활성 전환은 관리자 API 로만 가능하고 advisory lock 으로 직렬화된다. 평가용 정답 편입은 사람 서명(human_signoff_v1) 없이는 성립하지 않는다."},
    "M21": {"evidence": "잘못된 모델이 서빙되면 `/model/rollback` 으로 프로세스 재기동 없이 즉시 되돌린다. 백업·복구 절차가 별도로 있다."},
    "M22": {"evidence": "판정마다 근거 스팬과 요인별 재집계를 함께 낸다. 검수 화면은 신뢰도 숫자가 아니라 결정(자동확정 / 검수 + 사유)을 먼저 보여준다."},
    "M23": {"evidence": "적대 시나리오 회귀 게이트(eval_adversarial)가 배포 모델을 상시 감시한다. 고비밀 문서를 공개로 흘리는 방향의 오분류가 기준치를 넘으면 실패로 끊는다."},
    "M24": {"evidence": "단어 함정·출처 혼동 등 적대 케이스를 모은 golden_100 을 평가·학습 경로에 두고 회귀를 감시한다."},
    "M25": {"evidence": "CI 가 잠금 의존성에 pip-audit --strict 를 돌린다. 예외는 무시 목록(.pip-audit-ignore)에 명시해 관리한다."},
    "M26": {"evidence": "백업·복구 도구가 있고 모델은 버전 단위로 보관되어 이전 배포본으로 되돌릴 수 있다."},
    "M27": {"evidence": "요청 속도 제한기(slowapi)가 붙어 있고 초과 시 429 를 낸다. 본문 크기 상한도 함께 둔다."},
    "M28": {"caveat": "폐기 시점에 사용하는 도구다. 현재까지 실행 이력 없음(정상).",
            "evidence": (
        "폐기 도구(scripts/decommission.py)가 구성요소를 지우고 <strong>지웠다는 것을 확인</strong>한다. "
        "원본 문서는 <strong>암호 소거</strong>로 처리한다 - AES-256-GCM 으로 저장돼 있으므로 키를 파기하면 "
        "남은 암호문은 복구할 수 없고, 대용량 원본을 물리적으로 덮어쓸 필요가 없다. 나머지는 볼륨 6종"
        "(pgdata·mariadata·redisdata·storagedata·golden_data·artifacts_out) · 모델 · 학습셋 · 컨테이너를 "
        "지운 뒤 같은 조사 함수로 재확인하고 폐기 확인서(JSON)를 남긴다. "
        "되돌릴 수 없는 작업이라 기본은 dry-run 이고, 실행에는 프로젝트명 재입력과 키 파기 확인이 필요하다. "
        "도구는 키를 대신 지우지 않는다 - 우리가 모르는 사본이 있을 수 있어 운영자 확인을 요구한다."
    )},    "M29": {"evidence": "원청(KL)이 수급인을 점검하는 항목. 수급인 자기점검으로 갈음할 수 없다."},
    "M30": {"evidence": "기관 사용자 대상 교육·내부 보안정책 수립. 기관 소관."},
}

_AGENTIC_WHY = (
    "이 시스템은 문서 등급 분류기다. 스스로 목표를 세우거나 도구를 자율 호출하지 않고, "
    "에이전트끼리 통신하지도 않는다. 합성문서 생성에서 외부 상용 LLM 을 부르는 구간이 있으나 "
    "그것은 에이전틱 AI 가 아니라 가이드북 제2장 <strong>유형②(내부 시스템의 외부 AI 연계)</strong>에 "
    "해당하며, 중점 항목 M09·M13·M14·M24·M27 로 위 공통 표에서 이미 점검했다."
)
_NO_GAP = (
    '<p class="ok">공통 27개 항목에 대책이 모두 갖춰져 있다.</p>'
    "<p>2026-09-08 점검에서 세 자리가 비어 있었고 그 자리를 메웠다. "
    "① 폐쇄망 배포본에서 앱이 외부에 직접 붙던 것을 루프백으로 내리고(M18), "
    "② 그 노출이 되살아나는지 배포마다 실제 컨테이너로 검사하게 했으며(M17), "
    "③ 폐기 절차가 없던 자리에 암호 소거 기반 폐기 도구를 만들었다(M28).</p>"
    "<p>남은 3개(M06·M29·M30)는 조직 통제라 시스템이 대신할 수 없다.</p>"
)
_PHYSICAL_WHY = (
    "구동기·센서·로봇을 제어하지 않는다. 문서를 입력받아 등급을 내는 서버 소프트웨어이며 "
    "물리 세계에 작용하는 출력이 없다."
)


def _template_parts() -> tuple[str, str]:
    """정본에서 style 과 nav 를 그대로 가져온다. 서식을 새로 만들지 않는다."""
    text = _TEMPLATE.read_text(encoding="utf-8")
    style = re.search(r"<style>.*?</style>", text, re.S)
    nav = re.search(r"<nav class=\"nav\">.*?</nav>", text, re.S)
    if not style or not nav:
        raise SystemExit(f"정본에서 style/nav 를 찾지 못했다: {_TEMPLATE}")
    nav_html = nav.group(0).replace("KL 질의사항 회신서", "AI시스템 보안대책 체크리스트")
    nav_html = nav_html.replace(">감리 산출물<", ">보안 점검<")
    return style.group(0), nav_html


def _esc(s: str) -> str:
    return html.escape(s, quote=False)


def _rows(controls: list[dict]) -> str:
    out: list[str] = []
    for c in controls:
        if c["scope"] == "na":
            continue
        note = NOTES.get(c["id"], {})
        if c["scope"] == "org":
            mark, cell = "해당없음", "기관 소관"
        elif c["verdict"] == "근거있음":
            mark, cell = "예", "적용"
        else:
            mark, cell = "아니오", "미적용"

        body = _esc(note.get("evidence", "")) if note.get("evidence") else ""
        if c["scope"] == "org":
            body = _esc(note.get("evidence", ""))
        if note.get("caveat"):
            body += f'<br /><span class="part">단서</span> {note["caveat"]}'
        if note.get("gap"):
            body += (
                f'<br /><span class="gap">부족: {_esc(note["gap"])}</span>'
                f'<br /><strong>대안</strong> {note["remedy"]}'
            )
        where = ", ".join(w for p in c["probes"] for w in p["where"][:1])
        if where:
            body += f'<br /><span class="src">{_esc(where)}</span>'

        cls = {"예": "ok", "아니오": "gap", "해당없음": "off"}[mark]
        out.append(
            f'<tr><td><code>{c["id"]}</code></td>'
            f'<td>{_esc(c["title"])}</td>'
            f'<td><span class="{cls}">{mark}</span><br /><span class="meta">{cell}</span></td>'
            f"<td>{body}</td></tr>"
        )
    return "\n".join(out)


def _na_rows(controls: list[dict], prefix: str) -> str:
    return "\n".join(
        f'<tr><td><code>{c["id"]}</code></td><td>{_esc(c["title"])}</td>'
        f'<td><strong>해당없음</strong></td></tr>'
        for c in controls if c["id"].startswith(prefix)
    )


def render() -> str:
    style, nav = _template_parts()
    data = audit()
    controls = data["controls"]

    common = [c for c in controls if c["scope"] != "na"]
    yes = sum(1 for c in common if c["scope"] == "code" and c["verdict"] == "근거있음")
    no = sum(1 for c in common if c["scope"] == "code" and c["verdict"] != "근거있음")
    org = sum(1 for c in common if c["scope"] == "org")
    na = sum(1 for c in controls if c["scope"] == "na")

    gaps = [(c["id"], NOTES[c["id"]]) for c in common
            if c["id"] in NOTES and NOTES[c["id"]].get("gap")]

    gap_html = "\n".join(
        f'<div class="note"><h3>{cid} · {_esc(next(c["title"] for c in common if c["id"] == cid))}</h3>'
        f'<p class="gap">{_esc(n["gap"])}</p>'
        f'<p><strong>대안</strong> {n["remedy"]}</p></div>'
        for cid, n in gaps
    )

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>AI시스템 보안대책 체크리스트</title>
{style}
</head>
<body>
{nav}
<main class="wrap">

<header>
  <p class="eyebrow">국가정보원 「AI 보안 가이드북」(2025.12) 부록1</p>
  <h1>AI시스템 보안대책 체크리스트</h1>
  <p>AI 영업비밀 관리시스템(문서 등급 분류) 대상 점검 결과다.
  항목별 적용 여부는 <strong>소스 코드를 실제로 열어</strong> 판정했고, 재현 명령은 문서 끝에 있다.</p>
  <div class="note">
    <p><strong>이 표가 뜻하는 것.</strong> 체크리스트가 묻는 것은 &ldquo;대책을 <strong>마련</strong>하였는가&rdquo;이며,
    &lsquo;예&rsquo;는 그 대책이 구현되어 근거 파일을 제시할 수 있다는 뜻이다.
    <strong>운영 서버에서의 실측 검증과는 다르다.</strong> 이 구분이 필요한 항목에는 &lsquo;단서&rsquo;를 함께 적었다.</p>
    <p>2026-09-08 점검에서 세 항목(M17 · M18 · M28)의 대책을 새로 마련했다.
    그중 배포 설정에 해당하는 것은 <strong>다음 서버 구축 시 반영·확인</strong>된다.</p>
  </div>
</header>

<section>
  <h2>점검 결과 요약</h2>
  <table>
    <thead><tr><th>구분</th><th>항목 수</th><th>내용</th></tr></thead>
    <tbody>
      <tr><td>가. 공통 — 예</td><td><strong>{yes}</strong></td><td>대책이 구현되어 있고 근거 파일을 제시할 수 있다</td></tr>
      <tr><td>가. 공통 — 아니오</td><td><strong>{no}</strong></td><td>대책이 없다. 대안을 아래에 제시한다</td></tr>
      <tr><td>가. 공통 — 기관 소관</td><td><strong>{org}</strong></td><td>조직 통제(사전승인·용역업체 점검·사용자 교육). 시스템이 대신할 수 없다</td></tr>
      <tr><td>나·다. 에이전틱 · 피지컬</td><td><strong>{na}</strong></td><td>해당없음. 사유는 각 절에 적었다</td></tr>
    </tbody>
  </table>
  <p class="meta">분모 {yes + no + org + na}개. 판정 방법은 항목마다 이름 붙인 근거를 두고 그 근거가
  소스에 실재하는지 확인하는 것이다. 키워드만 맞고 뜻이 다른 오탐(예: 판정 게이트의 "safety" 를
  취약점 점검으로 세는 것)을 걷어내기 위해서다.</p>
</section>

<section>
  <h2>부족한 것과 대안</h2>
  {gap_html or _NO_GAP}
</section>

<section>
  <h2>가. 보안대책 체크리스트 (공통)</h2>
  <table>
    <thead><tr><th style="width:64px">순번</th><th style="width:210px">항목</th>
    <th style="width:88px">적용여부</th><th>대책 내용 · 근거</th></tr></thead>
    <tbody>
{_rows(controls)}
    </tbody>
  </table>
</section>

<section>
  <h2>나. 에이전틱 AI (자율형 AI)</h2>
  <p>{_AGENTIC_WHY}</p>
  <table>
    <thead><tr><th style="width:64px">순번</th><th>항목</th><th style="width:88px">적용여부</th></tr></thead>
    <tbody>
{_na_rows(controls, "A-M")}
    </tbody>
  </table>
</section>

<section>
  <h2>다. 피지컬 AI (로봇 · 기계 등)</h2>
  <p>{_PHYSICAL_WHY}</p>
  <table>
    <thead><tr><th style="width:64px">순번</th><th>항목</th><th style="width:88px">적용여부</th></tr></thead>
    <tbody>
{_na_rows(controls, "P-M")}
    </tbody>
  </table>
</section>

<section>
  <h2>시스템 구성 유형</h2>
  <p>가이드북 제2장은 시스템을 네 유형으로 나누고 유형별 중점 항목을 지정한다.
  이 시스템은 <strong>세 유형에 걸친다.</strong></p>
  <table>
    <thead><tr><th style="width:280px">유형</th><th style="width:200px">이 시스템에서</th><th>중점 항목</th></tr></thead>
    <tbody>
      <tr><td>① 기관 내부망 단독 구축 · 운영</td><td>회원사 폐쇄망 배포</td><td><code>M05 · M16 · M17 · M19</code></td></tr>
      <tr><td>② 내부망 시스템을 외부 AI와 연계</td><td>합성문서 생성 시 상용 LLM 호출</td><td><code>M09 · M13 · M14 · M24 · M27</code></td></tr>
      <tr><td>③ 내부망 AI가 외부 인터넷 자료 수집 · 학습</td><td>공개 판례 수집 · 학습</td><td><code>M01 · M02 · M03 · M04 · M10 · M17</code></td></tr>
      <tr><td>④ 클라우드 등 외부망 구축 · 운영</td><td class="meta">해당없음</td><td class="meta">-</td></tr>
    </tbody>
  </table>
  <p>중점 항목 중 대책이 비어 있는 것은 <strong>없다.</strong> 다만 ①의 M17 은
  프록시가 옵션 기동이라는 단서가 붙는다(위 대안 참조).</p>
</section>

<section>
  <h2>재현</h2>
  <pre>cd poc
python scripts/audit_nis_ai_security.py --verbose   # 항목별 근거 위치
python scripts/build_nis_checklist_doc.py --check   # 이 문서가 최신인지 확인</pre>
  <p class="meta">이 문서는 위 검사기의 판정에 설명과 대안을 붙여 생성한다. 코드가 바뀌면
  다시 생성해야 하며, <code>--check</code> 가 어긋남을 알려 준다.</p>
</section>

</main>
</body>
</html>
"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="AI 보안대책 체크리스트 문서 생성")
    ap.add_argument("--check", action="store_true", help="생성 결과가 현재 파일과 같은지만 확인")
    args = ap.parse_args(argv)

    rendered = render()
    if args.check:
        if not _OUT.exists():
            print(f"[check] 문서가 없다: {_OUT}")
            return 1
        if _OUT.read_text(encoding="utf-8") != rendered:
            print("[check] 문서가 코드와 어긋난다. 다시 생성할 것.")
            return 1
        print("[check] 문서와 코드가 일치한다.")
        return 0

    _OUT.parent.mkdir(parents=True, exist_ok=True)
    _OUT.write_text(rendered, encoding="utf-8")
    print(f"[생성] {_OUT}")
    print(f"[크기] {len(rendered):,}자")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
