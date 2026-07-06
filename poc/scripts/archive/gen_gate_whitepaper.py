# -*- coding: utf-8 -*-
"""분류 검수 게이트 강화 — 실증·설계 보고서(HTML) 생성.

기술구현_백서_v5.html의 head(CSS)·nav(로고)·script(ToC 추적)를 그대로 차용해 폼 일관성을
유지하고(메모리: html-doc-form-consistency), 본 세션의 분석·설계·측정 내용을 본문으로 주입한다.
산출물은 doc/result/open/ 정본 폴더(메모리: open-folder-only-policy).
"""
from __future__ import annotations
import re
from pathlib import Path

OPEN = Path("../doc/result/open")
SRC = OPEN / "기술구현_백서_v5.html"
DST = OPEN / "분류검수게이트_강화_보고서.html"

src = SRC.read_text(encoding="utf-8")
head = src[: src.index("</head>") + len("</head>")]
head = re.sub(r"<title>.*?</title>", "<title>분류 검수 게이트 강화 — 실증·설계 보고서</title>", head, flags=re.S)
nav = re.search(r'<header class="nav">.*?</header>', src, re.S).group(0)
# nav 링크를 본 문서 섹션에 맞게 교체
nav = re.sub(r'<a class="nav-link.*?</a>\s*', "", nav, flags=re.S)
nav = nav.replace(
    "Search whitepaper",
    "분류 검수 게이트 강화 보고서",
).replace("<kbd>⌘K</kbd>", "")
script = re.findall(r"<script>.*?</script>", src, re.S)[-1]

BODY = r"""
<body>
__NAV__
<div class="page">
  <aside class="sidebar">
    <div class="toc-title">목차</div>
    <div class="toc-group">
      <a class="toc-item active" href="#s01">01 배경 — 왜 검수가 필요한가</a>
      <a class="toc-item" href="#s02">02 발견 — 런타임 무인 미탐(silent FNR)</a>
      <a class="toc-item" href="#s03">03 1차 방어 — sparse-evidence 게이트</a>
      <a class="toc-item" href="#s04">04 모델 보정과 그 한계</a>
      <a class="toc-item" href="#s05">05 핵심 — confidence는 믿을 수 있나</a>
      <a class="toc-item" href="#s06">06 채택 — 등급차등·합의 게이트</a>
      <a class="toc-item" href="#s07">07 기각된 접근</a>
      <a class="toc-item" href="#s08">08 본질 로드맵</a>
      <a class="toc-item" href="#s09">09 한계·전제</a>
    </div>
  </aside>

  <article>
    <div class="meta-row">
      <span class="badge">실증·설계 보고서</span>
      <span class="badge blue">2026-06 · 보조문서</span>
      <span class="meta-dot">·</span>
      <span class="meta-soft">분류 검수 게이트 강화 — silent FNR 방어 · 자동확정 신뢰성</span>
      <span class="meta-dot">·</span>
      <span class="meta-soft">Lloydk AI Engine</span>
    </div>

    <h1 class="title">분류 검수 게이트 강화</h1>
    <p class="lede">"AI가 매긴 보안등급을 사람이 언제 검수해야 하는가"를 골든셋으로 실증해, <b>자동확정(무인 확정)을 confidence 단독으로 판단하던 구조의 위험</b>을 측정으로 드러내고, 그 대안을 설계·검증한 보고서다. 핵심 결론: <b>confidence는 자동확정의 단독 근거로 신뢰할 수 없으며(AUROC 0.58)</b>, 확신은 <b>등급차등 + 룰·모델 합의</b>라는 독립 신호에서 나온다.</p>

    <div class="hero-kpis">
      <div class="hero-kpi pass"><span class="dot"></span> 런타임 무인 미탐 <b>발견·차단</b></div>
      <div class="hero-kpi pass"><span class="dot"></span> sparse-evidence 게이트 <b>TS 미탐 5→0</b></div>
      <div class="hero-kpi pass"><span class="dot"></span> conf 신뢰성 <b>AUROC 0.58(부정)</b></div>
      <div class="hero-kpi pass"><span class="dot"></span> 합의 게이트 정밀도 <b>63→81%</b></div>
      <div class="hero-kpi pass"><span class="dot"></span> 고등급 미탐 <b>46→8</b></div>
      <div class="hero-kpi pass"><span class="dot"></span> 적대 리뷰 <b>결함 0</b></div>
      <div class="hero-kpi"><span class="dot"></span> 모든 수치 <b>합성 골든셋(실데이터 검증 예정)</b></div>
    </div>
    <hr class="hero-sep">

    <div class="callout info">
      <span class="callout-icon">ℹ</span>
      <div class="callout-body"><b>한눈에 보기.</b> 비밀문서를 자동으로 4등급(TS·S1·S2·S3)으로 분류하는데, 기계가 <b>확신(confidence)이 높으면 사람 검수 없이 그대로 확정</b>한다. 그런데 "확신 점수"가 시험 본 학생이 <b>모르는 문제도 자신 있게 답하는 것</b>과 같아서, <b>틀린 곳에서 가장 확신</b>하는 일이 벌어졌다(TS 비밀을 낮은 등급으로 자신 있게 확정). 그래서 "확신 점수" 대신 <b>두 분류기(룰·AI모델)가 의견이 일치할 때만 자동확정</b>하도록 바꿨더니, 자동확정의 정확도가 63%→81%로 오르고 위험한 누락이 46건→8건으로 줄었다. 상세는 §5·§6.</div>
    </div>

    <section id="s01">
      <h2><span class="num">01</span> 배경 — 왜 검수가 필요한가</h2>
      <p class="h2-sub">보안등급 오판은 곧 사고다 — 과소분류는 비밀 유출, 과대분류는 업무 마찰.</p>
      <p>문서를 TS(특급기밀)·S1(기밀)·S2(대외비)·S3(일반/공개) 4등급으로 자동분류하고, 등급에 따라 접근제어·암호화·감사로그가 적용된다. 따라서 <b>등급을 잘못 매기면 곧 보안사고</b>다 — 특히 <b>과소분류</b>(비밀을 낮게)는 통제 없이 유출되는 미탐(FNR)으로, 이 시스템이 "절대 금지"로 두는 오류다. 그래서 기계가 확신하지 못하는 문서는 <b>자동확정하지 않고 사람 검수 큐로 보내는 게이트</b>가 안전의 핵심이다. 본 보고서는 그 게이트가 실제 런타임에서 제대로 동작하는지 골든셋으로 검증하고, 발견된 허점을 수정·재설계한 기록이다.</p>
      <div class="callout warn">
        <span class="callout-icon">⚠</span>
        <div class="callout-body"><b>본 보고서의 모든 수치는 합성 골든셋(golden100·golden500) 기준</b>이다. golden셋은 등급별로 균등하게, 경계 사례만 모아 만든 <b>스트레스 테스트셋</b>이라 실제 트래픽보다 어렵다. 절대 수치가 아니라 <b>구성 간 상대 비교·방향성</b>으로 읽어야 하며, 정식 수치는 실데이터로 확정한다(§9).</div>
      </div>
    </section>

    <section id="s02">
      <h2><span class="num">02</span> 발견 — 런타임 무인 미탐(silent FNR)</h2>
      <p class="h2-sub">학습모델·LLM이 없는 폐쇄망 초기상태(룰 폴백)에서 TS 5건이 무인으로 낮게 확정.</p>
      <p>룰 폴백 경로의 confidence는 <code>최고등급 점수 ÷ 전체 점수</code>로 계산된다. 그래서 <b>약한 키워드 하나만 매칭돼도 그 등급에 점수가 전부 몰려 confidence=1.0</b>이 된다. 즉 "단 하나의 약한 근거"가 최고신뢰로 자동확정돼, 저신뢰 검수 게이트(conf&lt;0.7)를 그대로 통과한다.</p>
      <p>golden100 실증: 정답이 <b>TS인데 룰의 TS 신호가 0</b>인 문서 5건(<code>G50-TS-07/08/16/22/23</code>)이 S1·S2로 <b>conf=1.0 자동확정</b>됐다 — 사람이 못 보고 비밀이 새는 전형적 silent FNR이다.</p>
    </section>

    <section id="s03">
      <h2><span class="num">03</span> 1차 방어 — sparse-evidence 게이트</h2>
      <p class="h2-sub">빈약한 증거에 기댄 자동확정을 검수로 라우팅(설정 <code>rule_fallback_min_evidence</code>, 기본 0.9).</p>
      <p>자동확정에는 <b>최소 절대 증거량</b>이 필요하다는 불변식을 추가했다. 룰 점수 합이 임계 미만이면 <code>sparse-evidence</code> 경고를 남겨 confidence와 무관하게 needs_review로 보낸다.</p>
      <div class="card-grid col4" style="margin:16px 0;">
        <div class="card card-pass"><div class="card-label">TS 무인 미탐</div><div class="card-value sm">5 → 0</div><div class="card-desc">golden100, 결정론적</div></div>
        <div class="card"><div class="card-label">검수 부하</div><div class="card-value sm">+18 / 100</div><div class="card-desc">빈약근거 자동확정분</div></div>
        <div class="card card-pass"><div class="card-label">하위호환</div><div class="card-value sm">보장</div><div class="card-desc">임계 0이면 동작 원복</div></div>
        <div class="card card-pass"><div class="card-label">패턴</div><div class="card-value sm">경고 라우팅</div><div class="card-desc">cap-conflict와 동일 방식</div></div>
      </div>
    </section>

    <section id="s04">
      <h2><span class="num">04</span> 모델 보정과 그 한계</h2>
      <p class="h2-sub">temperature 보정은 학습분포(val)에선 효과적이나 OOD(golden)엔 전이되지 않는다.</p>
      <p>학습모델(bpilot_v2)을 서빙하려면 미보정 시 과신해 TS를 무인 미탐하므로 <b>temperature 보정</b>이 필요하다. 자체 검증셋으로 적합한 결과 <b>T*=2.0</b>, ECE(보정오차) <b>0.131 → 0.045</b>로 3배 개선됐다.</p>
      <div class="callout warn">
        <span class="callout-icon">⚠</span>
        <div class="callout-body"><b>보정은 필요조건이지 충분조건이 아니다.</b> val에서 ECE 0.045였던 같은 보정이 <b>golden500(OOD)에선 ECE 0.184</b>로 다시 나빠진다 — val에서 맞춘 온도가 분포가 다른 셋엔 전이되지 않는다. golden100에서도 보정 후 TS 2건(<code>G50-TS-10/24</code>)이 여전히 누출됐다. 이 사각지대는 보정이 아니라 §6·§8의 게이트·재학습으로 다룬다.</div>
      </div>
    </section>

    <section id="s05">
      <h2><span class="num">05</span> 핵심 — confidence는 믿을 수 있나</h2>
      <p class="h2-sub">측정 결과 아니다 — conf의 정답/오답 판별력은 거의 무작위(AUROC 0.58).</p>
      <p>confidence는 분류기가 학습한 신뢰도가 아니라, 파이프라인이 <code>logits → 온도 → softmax → 청크집계 → 정규화</code>로 만든 <b>파생값</b>이다. softmax 분류기는 cross-entropy로 "정답 맞히기"만 학습하므로 OOD에서 과신하는 게 정설이고(코드 감사상 버그 아님), 그게 그대로 재현됐다.</p>
      <h3>5.1 신뢰도 다이어그램 (golden500)</h3>
      <div class="tbl-wrap">
        <table>
          <thead><tr><th>conf 구간</th><th>문서수</th><th>실제 정답률</th><th>평균 conf</th><th>과신 갭</th></tr></thead>
          <tbody>
            <tr><td>0.70 – 0.80</td><td>52</td><td>57.7%</td><td>75.6%</td><td class="td-fail">+17.9%p</td></tr>
            <tr><td>0.80 – 0.90</td><td>99</td><td>55.6%</td><td>85.6%</td><td class="td-fail">+30.1%p</td></tr>
            <tr><td>0.90 – 1.00</td><td>196</td><td>68.9%</td><td>93.9%</td><td class="td-fail">+25.0%p</td></tr>
          </tbody>
        </table>
      </div>
      <p><b>conf 0.9짜리 문서가 실제론 69%만 맞는다.</b> 기대보정오차 <b>ECE = 0.184</b>(통상 0.1 초과면 나쁨).</p>
      <h3>5.2 게이트가 옳고 그름을 가르는가</h3>
      <div class="card-grid col3" style="margin:16px 0;">
        <div class="card card-fail"><div class="card-label">conf 판별력 (AUROC)</div><div class="card-value sm">0.58</div><div class="card-desc">0.5=무작위 · 0.8+=쓸만</div></div>
        <div class="card"><div class="card-label">자동확정 정답률</div><div class="card-value sm">63.4%</div><div class="card-desc">conf≥0.7</div></div>
        <div class="card"><div class="card-label">검수대상 정답률</div><div class="card-value sm">56.2%</div><div class="card-desc">conf&lt;0.7 — 차이 7%p뿐</div></div>
      </div>
      <p>conf를 0.9로 더 엄격히 해도 자동확정 정밀도는 63→69%로 거의 오르지 않는다 — conf 신호 자체가 약하다는 또 하나의 증거다. 등급별로는 <b>S3(공개)만 conf 신뢰(94%)</b>, S2는 51%(동전던지기), TS·S1은 67~75%.</p>
    </section>

    <section id="s06">
      <h2><span class="num">06</span> 채택 — 등급차등·합의 게이트</h2>
      <p class="h2-sub">확신을 conf가 아니라 독립 신호(룰·모델 합의)에서 얻는다 (설정 <code>agreement_gate_enabled</code>, 기본 off).</p>
      <p>자동확정 조건을 다음으로 좁혔다: <b>예측이 공개등급(S3)이면 conf 단독 허용</b>(S3 conf 정밀도 94%, 과소분류 위험 없음), <b>그 외(TS·S1·S2)는 룰엔진 원시등급 == 모델등급일 때만</b> 자동확정하고 불일치하면 검수로 보낸다. 등급은 무인으로 바꾸지 않고 라우팅만 한다.</p>
      <div class="tbl-wrap">
        <table>
          <thead><tr><th>셋</th><th>구성</th><th>자동확정 정밀도</th><th>고등급(TS·S1) 미탐</th><th>자동확정 커버</th></tr></thead>
          <tbody>
            <tr><td rowspan="2">golden100</td><td>conf 단독(현행)</td><td>69.1%</td><td class="td-fail">9</td><td>81</td></tr>
            <tr><td><b>합의 게이트</b></td><td class="td-pass">94.3%</td><td class="td-pass">0</td><td>35</td></tr>
            <tr><td rowspan="2">golden500</td><td>conf 단독(현행)</td><td>63.3%</td><td class="td-fail">46</td><td>346</td></tr>
            <tr><td><b>합의 게이트</b></td><td class="td-pass">81.0%</td><td class="td-pass">8</td><td>189</td></tr>
          </tbody>
        </table>
      </div>
      <p>정밀도가 오르고 고등급 미탐이 ~6배 줄었다. 대가는 자동확정 커버리지 하락(검수 증가)이며, 이는 "확신 있는 것만 자동확정"의 비용이다 — 다만 <b>실트래픽은 S3가 다수</b>라 운영 검수율은 golden(고등급 편중)보다 낮다.</p>
      <div class="callout info">
        <span class="callout-icon">ℹ</span>
        <div class="callout-body"><b>적대적 다중에이전트 리뷰 통과.</b> 9개 에이전트·5개 차원(정확성·FNR안전·통합·호환·성능)으로 구현을 적대 검증한 결과 <b>correctness 결함 0</b>, 불변식(검수→자동 강등 없음 · off 시 no-op · fail-safe · 원시 룰등급 비교) 모두 확인. 확정된 2건은 LOW 성능(룰엔진 중복 실행)으로, 산출된 룰등급을 재사용하도록 수정했다.</div>
      </div>
    </section>

    <section id="s07">
      <h2><span class="num">07</span> 기각된 접근</h2>
      <p class="h2-sub">검증으로 효과가 부정되어 채택하지 않은 대안들 — 기각도 결과다.</p>
      <h3>7.1 LLM 2차의견 (qwen3:14b)</h3>
      <p>모델이 비-TS를 자동확정할 때 로컬 LLM에게 2차의견을 받는 방식. golden100에서 <b>검수 19→49로 폭증</b>(LLM이 "모호하면 한 단계 위" 편향으로 과잉 에스컬레이션)하고 <b>TS 누출은 여전히 잔존(비결정론적)</b>, 100건에 57콜·21분. 비용 대비 효과가 나빠 <b>기본 off로 보존(비권장)</b>.</p>
      <h3>7.2 FNR-safe override 임계 인하</h3>
      <p>룰의 고등급 신호가 모델을 덮는 임계(3.0)를 1.5로 낮추는 방안. golden100에선 과대분류 +1로 싸 보였으나 <b>golden500에선 +12로 일반화 실패</b>(과적합)가 드러나 <b>미반영</b>(현행 유지).</p>
    </section>

    <section id="s08">
      <h2><span class="num">08</span> 본질 로드맵</h2>
      <p class="h2-sub">게이트는 다리(임시). 본질은 학습 시스템 개선이다.</p>
      <p>게이트는 "못 믿을 모델을 안전하게 우회"하는 장치다. 근본 해법은 셋이며, <b>재학습 가능 여부</b>에 따라 갈린다(본 프로젝트는 주기적 재학습 가능).</p>
      <div class="card-grid col3" style="margin:16px 0;">
        <div class="card card-pass"><div class="card-label">① 능동학습 환류</div><div class="card-value sm">최우선</div><div class="card-desc">검수→검증라벨→재학습. 검수가 데이터 엔진이 되어 미탐·검수량이 시간이 갈수록 감소. (corrections_rebuild·active_learning 존재)</div></div>
        <div class="card"><div class="card-label">② 목적함수</div><div class="card-value sm">비용민감·순서형</div><div class="card-desc">고등급 과소분류에 큰 페널티 + 순서형(TS&gt;S1&gt;S2&gt;S3) → 안전을 모델에 내장, 외부 보철 불요</div></div>
        <div class="card"><div class="card-label">③ 불확실성</div><div class="card-value sm">conformal</div><div class="card-desc">학습 불요. 보장된 등급집합 → 단일등급이면 자동확정, 복수면 검수</div></div>
      </div>
      <div class="callout info">
        <span class="callout-icon">ℹ</span>
        <div class="callout-body"><b>최우선 재학습 타깃(실증).</b> 합의 게이트를 통과한 잔여 미탐의 정체는 <b>인사·재무 관리문서를 한 단계 낮게 본 "공유 사각지대"</b>다 — golden500 S1→S2 미탐 7건이 전부 임원 보상·자금조달·고객원가 문서이고, <b>룰·모델이 둘 다 S2로 합의</b>해 불일치 기반 게이트를 통과했다(합의 게이트로는 구조적으로 못 잡음). 로컬 LLM(qwen3) 2차의견은 이 중 <b>4/7만 S1로 회복</b>(임원 보상 문서는 LLM도 일부 놓침)이라 독립 신호로 부분 도움이나 단독 해법은 아니다. → <b>인사·재무(임원 보상·자금조달·고객별 매출·원가구조) S1 예시를 라벨링해 재학습</b>하는 것이 가장 효과 큰 본개발 타깃이며, 그 전까지는 TS/S1 자동확정분 표본감사로 보완한다.</div>
      </div>
      <div class="callout info">
        <span class="callout-icon">ℹ</span>
        <div class="callout-body"><b>메타데이터 상향 게이트 — 재학습 전 결정론적 차단(구현됨, opt-in).</b> 이 사각지대의 본질은 내용 분류기가 <b>비밀관리성(M)·출처(S)를 못 본다</b>는 것 — 그 축은 <b>문서가 아니라 메타데이터</b>에 있다. KL ICD R6(보안표시·접근범위)를 상향 신호로 쓰는 게이트(<code>metadata_floor_enabled</code>)를 붙였다: ① <b>보안표시</b>가 예측보다 높으면 그 등급으로 상향 floor(명시표기 우선, 하향은 안 함); ② 표기 없고 <b>접근범위가 제한적</b>(임원/승인자 한정)인데 예측이 낮으면 검수 라우팅. 실증(7건 사각지대): 보안표시=기밀 → <b>7/7 S1 상향</b>, 접근범위=임원한정 → <b>7/7 검수</b>. 즉 메타데이터만 신뢰 가능하면 <b>재학습 전에 결정론적으로</b> 닫힌다. 단 <b>메타데이터 신뢰도에 전적 의존</b>(KL이 정확히 줘야) — 부재·오기 문서는 사각지대 잔존이라 재학습과 병행한다.</div>
      </div>
      <div class="callout warn">
        <span class="callout-icon">⚠</span>
        <div class="callout-body"><b>학습이 막히는 경우의 공백.</b> 서빙의 교정 반영(<code>_get_verified_label</code>)은 doc_id가 업로드마다 유니크해 재업로드에 안 먹혔는데, <b>내용해시(file_hash) 재사용</b>(<code>verified_label_content_reuse</code>, 기본 on)을 추가해 <b>동일 내용 재업로드</b>엔 검증등급이 전파되게 했다(구현 완료). 다만 <b>유사한 새 문서</b>로의 일반화는 여전히 안 된다 — 그건 임베딩 kNN(다른 등급 전파 위험)이라 보류했고, 근본은 재학습의 몫이다. 학습이 없으면 TS·S1 표본감사는 영구 상수다.</div>
      </div>
    </section>

    <section id="s09">
      <h2><span class="num">09</span> 한계·전제</h2>
      <p class="h2-sub">정식 오픈의 전제 = 실데이터 검증.</p>
      <ul>
        <li><b>모든 수치는 합성·OOD 골든셋</b> 기준이다. 실문서 분포에서의 정밀도·미탐·검수율은 <b>실데이터로 재측정해야</b> 확정된다.</li>
        <li><b>합의 게이트도 완벽은 아니다</b> — 정밀도 81%는 자동확정 5건 중 1건이 여전히 틀린다는 뜻(특히 S2). <b>TS·S1 자동확정분의 표본감사</b>는 게이트와 별개로 필수다.</li>
        <li>게이트·보정·LLM 2차의견은 <b>전부 기본 off</b>다. 켜는 것은 검수 인력·SLA를 고려한 운영 결정이다.</li>
      </ul>
      <div class="callout info">
        <span class="callout-icon">ℹ</span>
        <div class="callout-body"><b>요약.</b> "conf만 믿고 자동확정해도 되나"라는 물음에 데이터가 <b>No</b>라고 답했고(AUROC 0.58), 그 대안으로 <b>등급차등·합의 게이트</b>를 설계·측정·적대검증해 정밀도 63→81%·고등급 미탐 46→8을 확인했다. 본질은 <b>실데이터 능동학습 환류</b>이며, 그 전까지 게이트와 표본감사가 안전을 떠받친다.</div>
      </div>
    </section>

  </article>
</div>

<footer>
  <span>분류 검수 게이트 강화 — 실증·설계 보고서 · 2026-06 · Lloydk AI Engine</span>
  <span>silent FNR 방어 · 자동확정 신뢰성 · 합성 골든셋 기준</span>
</footer>
__SCRIPT__
</body>
</html>
"""

body = BODY.replace("__NAV__", nav).replace("__SCRIPT__", script)
DST.write_text(head + "\n" + body, encoding="utf-8")
print(f"wrote {DST} ({len(head)+len(body)} bytes)")
