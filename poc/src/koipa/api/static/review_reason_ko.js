/* 검수 라우팅 사유를 사람 말로 옮긴다 — 화면 두 곳이 같은 표를 쓰게 하는 단일 출처.
 *
 * 왜 파일로 뺐나(2026-08-24). 같은 표가 index.html 인라인 스크립트와 app.js 두 곳에
 * 따로 있었다. 게이트가 하나 늘 때마다 두 곳을 같이 고쳐야 하는데, 한쪽만 고치면
 * 화면에 따라 사유가 보였다 안 보였다 한다. 서버 쪽은 이미 한 곳(services/review_reasons.py)
 * 으로 모아 뒀다 — 화면도 같은 규율을 따른다.
 *
 * ⚠ 순서가 의미를 가진다. agreement-gate 를 먼저 본다: 신뢰도가 임계를 넘겨도 이 조건이면
 *    검수로 간다(실측 2026-08-21, 신뢰도 0.744 인데 룰 S3·모델 TS 불일치로 라우팅).
 *    신뢰도를 먼저 보면 "신뢰도가 낮아서" 라는 틀린 사유가 화면에 뜬다.
 *
 * 모듈이 아니라 평범한 스크립트다 — index.html 의 인라인 스크립트(비모듈)와 app.js(모듈)가
 * 둘 다 써야 하고, 모듈이면 인라인 쪽에서 import 할 수 없다.
 */
(function (global) {
  "use strict";

  var GATE_REASONS = [
    [/agreement-gate: model=(\S+) vs rule=(\S+)/,
      function (m) { return "두 엔진이 갈립니다 — 분류기 " + m[1] + " · 룰 " + m[2] + " (신뢰도만으로는 확정하지 않습니다)"; }],
    [/low-confidence: confidence=([\d.]+) < ([\d.]+)/,
      function (m) { return "판정 근거가 임계에 못 미칩니다 (" + m[1] + " < " + m[2] + ")"; }],
    [/document flagged at ingestion/, function () { return "추출 품질이 낮은 문서입니다(스캔·OCR 등)"; }],
    [/body_below_classifiable_threshold/, function () { return "판정할 본문이 사실상 없습니다"; }],
    [/cap-conflict/, function () { return "출처 기준 하향과 내용 기준 상향이 충돌합니다"; }],
    [/sparse-evidence/, function () { return "룰 판정이 약한 근거 하나에 기대고 있습니다"; }],
    [/abbrev-only-escalation/, function () { return "영문 약어 밀도만으로 높은 등급이 나왔습니다"; }],
    [/metadata-access-conflict/, function () { return "접근 제한 표기에 비해 내용 예측이 낮습니다"; }],
    [/metadata-management-conflict/, function () { return "관리성 부재 표기인데 내용 예측이 비공개 등급입니다"; }],
    [/gate-fail-open/, function () { return "안전 게이트 하나가 적용되지 못했습니다"; }],
    [/s2-underclass-risk/, function () { return "내부 문서 신호가 있는데 공개 등급으로 예측되었습니다"; }],
  ];

  /* warnings 배열에서 **검수로 보낸 사유** 한 줄을 찾는다. 못 찾으면 빈 문자열.
     빈 문자열을 부르는 쪽이 "자동 확정하지 않고 사람 검수로 라우팅" 같은 기본 문구로 받는다. */
  function gateReason(warnings) {
    var ws = warnings || [];
    for (var i = 0; i < ws.length; i++) {
      for (var j = 0; j < GATE_REASONS.length; j++) {
        var m = String(ws[i]).match(GATE_REASONS[j][0]);
        if (m) return GATE_REASONS[j][1](m);
      }
    }
    return "";
  }

  global.KOIPA_GATE_REASON = gateReason;
})(typeof window !== "undefined" ? window : globalThis);
