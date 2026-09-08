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
    "M01": {"evidence": "외부 생성형 AI 로 데이터를 전송할 때 출처가 확인된 구분(공개 실문서 · 합성문서)만 허용하는 허용목록을 적용한다. 출처가 확인되지 않은 자료는 자동 차단하며, 전송 대상에 하나라도 포함되면 해당 실행 전체를 외부 전송 없이 처리한다."},
    "M02": {"evidence": "의존 패키지는 잠금 파일로 버전을 고정하고 CI 가 잠금본으로만 설치한다. 컨테이너 이미지는 태그가 아닌 다이제스트(SHA-256)로 고정한다. 오픈소스 구성요소는 라이선스 대장으로 관리한다."},
    "M03": {"evidence": "수집 · 전처리 단계에서 개인정보를 검출 · 마스킹한다. 학습 데이터는 품질 기준 검사와 등급 정보 노출 검사를 통과한 것만 학습에 사용한다."},
    "M04": {"evidence": "원본 문서는 AES-256-GCM 으로 암호화하여 저장한다. 운영 배포 구성에서 강제 적용되며, 암호화 키가 설정되지 않으면 기동을 거부하여 평문 저장을 차단한다."},
    "M05": {"evidence": "역할 기반 접근통제를 적용하고, 관리 화면은 공유 인증키를 거부하고 개인 계정 기반 토큰 인증만 허용한다. 등급 결정 행위를 실계정으로 식별하기 위한 조치다."},
    "M06": {"evidence": "기관 내부 보고 · 승인 절차에 해당한다. 시스템은 문서 보안등급과 처리 이력을 기록하여 승인 판단에 필요한 근거를 제공한다."},
    "M07": {"evidence": "데이터를 4개 보안등급(TS · S1 · S2 · S3)으로 구분하여 구성 · 활용하며, 등급별 · 분야별 구성 현황을 확인할 수 있다. 평가 기준 데이터는 검수자 서명을 거친 것만 편입한다."},
    "M08": {"evidence": "데이터 접근 · 변경 기록을 해시 연결 구조의 감사 기록으로 남겨 변조를 탐지한다. 학습 수행 이력은 별도 이력으로 보관한다."},
    "M09": {"evidence": "모든 요청이 감사 기록 계층을 거치며 입 · 출력 정보가 기록된다. 운영 지표는 표준 모니터링 규격으로 수집한다."},
    "M10": {"evidence": "학습 · 평가 데이터는 명세 파일로 관리하며 파일마다 SHA-256 해시를 기록한다. 서버 이전 시에도 동일 명세로 대조하여 전송 중 손상을 검출한다."},
    "M11": {"evidence": "시스템 구성요소 명세(SBOM)를 산출하고, 릴리스 단위로 구성 · 버전 · 변경 이력을 기록한다."},
    "M12": {"evidence": "빌드 식별자를 이미지에 기록하고 배포 시 대조한다. 추론 파이프라인은 배포본 계약 해시가 일치하지 않으면 모델 적재를 거부한다."},
    "M13": {"evidence": "입력 문서는 개인정보 마스킹을 거친다. 외부 생성형 AI 로의 출력 전송은 출처 허용목록으로 통제하며, 확인되지 않은 자료는 차단한다."},
    "M14": {"evidence": "요청 본문 크기 상한을 문서 해석 이전 단계에서 적용하여 과대 요청을 차단한다. 업로드 파일의 형식과 크기도 제한한다."},
    "M15": {"evidence": "모델 추론과 규칙 판정을 중첩 적용하고 두 결과의 합치 여부를 검수 라우팅에 반영한다. 안전 게이트가 비활성화된 상태로는 기동되지 않도록 하여 보호장치가 조용히 해제되는 것을 방지한다."},
    "M16": {"evidence": "모델 저장 영역을 읽기 전용으로 마운트하고 폐쇄망 내부에 배치한다. 모델 활성 전환 · 회수는 관리자 권한으로만 수행할 수 있다."},
    "M17": {"caveat": "배포 검증 항목이므로 <strong>차기 서버 구축 시 수행</strong>된다.",
            "evidence": "데이터베이스 · 캐시 · 응용 서버를 호스트 내부 주소에만 바인딩하고, 외부 연계 구간은 역방향 프록시에서 종단한다. 배포 검증 절차가 설정 파일이 아니라 기동된 컨테이너의 실제 포트 바인딩을 확인하여, 응용 서버가 외부에 직접 노출된 상태로 운영되지 않도록 한다."},
    "M18": {"caveat": "바인딩 설정은 <strong>차기 서버 구축 시 반영</strong>된다. mTLS <strong>운영 인증서(PKI)는 발주기관 발급 사항</strong>이며, 발급 후 적용한다.",
            "evidence": "연계 구간은 상호 TLS(mTLS)로 보호하며 클라이언트 인증서를 검증하고 TLS 1.3 만 허용한다. 인증 토큰은 스크립트 접근이 차단된 쿠키로 전달한다. 응용 서버는 호스트 내부 주소에만 바인딩하며 외부 노출은 프록시를 통해서만 이루어진다."},
    "M19": {"evidence": "역할에 따라 접근 가능한 기능과 데이터를 제한한다. 컨테이너는 관리자 권한이 아닌 전용 계정으로 실행하며, 호스트 사전점검 절차가 권한 불일치를 배포 전에 확인한다."},
    "M20": {"evidence": "모델 활성 전환 · 회수는 관리자 권한 API 로만 수행되며 동시 실행이 직렬화된다. 평가 기준 데이터 편입은 검수자 서명 절차를 거치지 않으면 성립하지 않는다."},
    "M21": {"evidence": "오동작이 확인된 모델은 프로세스 재기동 없이 이전 배포본으로 즉시 회수할 수 있다. 데이터 · 모델 백업과 복구 절차를 별도로 운영한다."},
    "M22": {"evidence": "판정 결과에 근거 문장과 요인별 판단 근거를 함께 제시한다. 검수 화면은 신뢰도 수치가 아니라 처리 결정(자동확정 · 검수 대상 및 사유)을 우선 표시한다."},
    "M23": {"evidence": "적대적 시나리오 평가 집합을 상시 회귀 검사에 포함한다. 고보안 문서를 낮은 등급으로 판정하는 방향의 오류가 기준치를 초과하면 배포 검사를 실패 처리한다."},
    "M24": {"evidence": "용어 혼동 · 출처 오인 등 오판단 유도 유형을 수집한 평가 집합을 학습 · 평가 경로에 유지하고 회귀를 지속 감시한다."},
    "M25": {"evidence": "CI 가 잠금된 의존성 목록에 대해 알려진 취약점 점검을 수행한다. 예외 처리 항목은 별도 목록으로 명시 관리한다."},
    "M26": {"evidence": "데이터 · 모델 백업과 복구 절차를 운영하며, 모델은 버전 단위로 보관하여 이전 배포본으로 복원할 수 있다."},
    "M27": {"evidence": "요청 속도 제한을 적용하고 초과 시 표준 오류 응답으로 차단한다. 요청 본문 크기 상한을 함께 적용한다."},
    "M28": {"caveat": "시스템 폐기 시점에 수행하는 절차다.",
            "evidence": (
        "폐기 절차와 전용 도구를 두어 구성요소를 삭제하고 <strong>삭제되었음을 재확인</strong>한다. "
        "원본 문서는 <strong>암호 소거</strong> 방식으로 처리한다 &mdash; AES-256-GCM 으로 암호화 저장되어 있어 "
        "암호화 키를 파기하면 잔존 암호문은 복호할 수 없으며, 대용량 원본을 물리적으로 덮어쓸 필요가 없다. "
        "그 밖의 구성요소는 데이터 볼륨 6종 &middot; 모델 &middot; 학습 데이터 &middot; 컨테이너를 삭제한 뒤 "
        "동일 절차로 잔존 여부를 재확인하고 폐기 확인서를 산출한다. "
        "되돌릴 수 없는 작업이므로 기본 동작은 모의 실행이며, 실제 삭제에는 대상 재확인과 키 파기 확인이 필요하다. "
        "키 파기는 백업 &middot; 인수인계본 등 도구가 관할하지 않는 사본이 있을 수 있어 운영자 확인 절차로 둔다."
    )},
    "M29": {"evidence": "발주기관이 수급인의 보안관리 실태를 점검하는 항목으로, 수급인 자체 점검으로 갈음할 수 없다. 점검 요청 시 필요한 자료를 제출한다."},
    "M30": {"evidence": "기관 사용자 대상 교육 및 내부 보안정책 수립에 해당한다. 시스템 사용 방법과 검수 절차는 관리자 사용 매뉴얼로 제공한다."},
}

_AGENTIC_WHY = (
    "이 시스템은 문서 등급 분류기다. 스스로 목표를 세우거나 도구를 자율 호출하지 않고, "
    "에이전트끼리 통신하지도 않는다. 합성문서 생성에서 외부 상용 LLM 을 부르는 구간이 있으나 "
    "그것은 에이전틱 AI 가 아니라 가이드북 제2장 <strong>유형②(내부 시스템의 외부 AI 연계)</strong>에 "
    "해당하며, 중점 항목 M09·M13·M14·M24·M27 로 위 공통 표에서 이미 점검했다."
)
_NO_GAP = (
    '<p class="ok">공통 30개 항목 중 시스템이 담당하는 27개 항목에 보안대책이 모두 갖춰져 있다.</p>'
    "<p>미적용 항목은 없다. 나머지 3개(M06 &middot; M29 &middot; M30)는 기관 내부 절차 &middot; "
    "수급인 점검 &middot; 사용자 교육에 해당하여 발주기관이 수행한다.</p>"
    "<p>에이전틱 AI(17개) &middot; 피지컬 AI(10개)는 시스템 특성상 해당하지 않으며, 사유는 각 절에 적었다.</p>"
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
            mark, cell = "발주기관", "기관 수행 항목"
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

        cls = {"예": "ok", "아니오": "gap", "발주기관": "part"}[mark]
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
  <p>「AI 보안 가이드북」 부록1 체크리스트 57개 항목에 대한 점검 결과다.</p>
  <table class="docinfo">
    <tbody>
      <tr><th>대상 시스템</th><td>AI 영업비밀 관리시스템 &mdash; 문서 보안등급 자동분류</td></tr>
      <tr><th>준거 문서</th><td>국가정보원 「AI 보안 가이드북」(2025.12) 부록1 &lsquo;AI시스템 보안대책 체크리스트&rsquo;</td></tr>
      <tr><th>점검 범위</th><td>시스템 구성요소 &middot; 배포 구성 &middot; 운영 절차 (공통 30 &middot; 에이전틱 17 &middot; 피지컬 10 = 57개 항목)</td></tr>
      <tr><th>점검 방법</th><td>항목별 보안대책의 구현 근거를 소스 &middot; 설정 &middot; 절차 문서에서 직접 확인</td></tr>
      <tr><th>점검 일자</th><td>2026. 9. 8.</td></tr>
      <tr><th>작성</th><td>주식회사 로이드케이</td></tr>
    </tbody>
  </table>
  <div class="note">
    <p><strong>이 표가 뜻하는 것.</strong> 체크리스트가 묻는 것은 &ldquo;대책을 <strong>마련</strong>하였는가&rdquo;이며,
    &lsquo;예&rsquo;는 그 대책이 구현되어 근거 파일을 제시할 수 있다는 뜻이다.
    <strong>운영 서버에서의 실측 검증과는 다르다.</strong> 이 구분이 필요한 항목에는 &lsquo;단서&rsquo;를 함께 적었다.</p>
    <p>근거 자료(항목별 구현 위치 &middot; 설정값 &middot; 검증 결과)는 별도 보유하며,
    요청 시 제출한다.</p>
  </div>
</header>

<section>
  <h2>점검 결과 요약</h2>
  <table>
    <thead><tr><th>구분</th><th>항목 수</th><th>내용</th></tr></thead>
    <tbody>
      <tr><td>가. 공통 — 예</td><td><strong>{yes}</strong></td><td>대책이 구현되어 있고 근거 파일을 제시할 수 있다</td></tr>
      <tr><td>가. 공통 — 아니오</td><td><strong>{no}</strong></td><td>보안대책이 마련되지 않은 항목</td></tr>
      <tr><td>가. 공통 — 발주기관 수행</td><td><strong>{org}</strong></td><td>기관 내부 절차 · 수급인 점검 · 사용자 교육. 해당하는 항목이며 발주기관이 수행한다</td></tr>
      <tr><td>나·다. 에이전틱 · 피지컬</td><td><strong>{na}</strong></td><td>해당없음. 사유는 각 절에 적었다</td></tr>
    </tbody>
  </table>
  <p class="meta">전체 {yes + no + org + na}개 항목. 항목별로 해당 보안대책이 구현된 위치를 지정하고
  그 구현이 실재하는지 확인하는 방식으로 판정하였다. 명칭이 유사하나 목적이 다른 구성요소를
  근거로 계상하지 않도록, 항목마다 근거를 개별 지정하여 대조하였다.</p>
</section>

<section>
  <h2>점검 결론</h2>
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
  <h2>발주기관 수행 사항</h2>
  <p>다음은 시스템으로 대체할 수 없어 발주기관이 수행하는 사항이다.</p>
  <table>
    <thead><tr><th style="width:64px">항목</th><th style="width:230px">내용</th><th>비고</th></tr></thead>
    <tbody>
      <tr><td><code>M06</code></td><td>민감정보 사용 사전 승인</td><td>기관 내부 보고 &middot; 승인 절차</td></tr>
      <tr><td><code>M29</code></td><td>용역업체 보안관리</td><td>발주기관이 수급인을 점검하는 항목이므로 수급인 자체 점검으로 갈음할 수 없다</td></tr>
      <tr><td><code>M30</code></td><td>사용자 교육 및 보안정책 수립</td><td>기관 사용자 대상 교육 &middot; 내부 보안정책</td></tr>
      <tr><td><code>M18</code></td><td>mTLS 운영 인증서(PKI) 발급</td><td>기관 PKI 정책에 따르는 사항. 발급 후 적용한다(설정 및 검증 절차는 준비되어 있다)</td></tr>
    </tbody>
  </table>
  <p class="meta">본 체크리스트 양식의 작성 &middot; 서명 주체는 발주기관(사업담당자 &middot; 정보화사업담당관)이며,
  본 문서는 그 작성에 사용하는 근거 자료다.</p>
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
