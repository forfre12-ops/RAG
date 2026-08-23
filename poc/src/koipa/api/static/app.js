// KOIPA AI 데모 콘솔 — 라우터 호출 + 렌더 + 인터랙션 전체.
// V2 디자인 시스템 위에서 동작. 외부 의존 0.

import { DEMO_DATA } from "./samples.js";
import { postSSE } from "./sse.js";
import { translateError } from "./errors_ko.js";
import {
  renderBodyWithHighlights,
  flashKeywordInBody,
  focusLegalCard,
} from "./highlight.js";

// ──────────────────────────────────────────────────────────────────────
// State
// ──────────────────────────────────────────────────────────────────────
const state = {
  endpoint: window.location.origin,
  apiKey: window.localStorage?.getItem("koipa_api_key") || "",
  currentSampleId: null,
  currentSample: null,
  toggledOff: new Set(), // 와우 A — off 된 키워드들
  lastResult: null,
  warmupDone: false,
  health: null,
};

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

// ──────────────────────────────────────────────────────────────────────
// API helpers
// ──────────────────────────────────────────────────────────────────────
function apiUrl(path) {
  return state.endpoint.replace(/\/+$/, "") + path;
}
function ensureApiKey() {
  if (state.apiKey) return;
  const entered = window.prompt("시연 서버 API 키를 입력하세요. 입력값은 이 브라우저의 localStorage에만 저장됩니다.");
  if (entered) {
    state.apiKey = entered.trim();
    window.localStorage?.setItem("koipa_api_key", state.apiKey);
  }
}
function authHeaders() {
  ensureApiKey();
  return { "X-API-Key": state.apiKey };
}
async function apiGet(path) {
  const resp = await fetch(apiUrl(path), { headers: authHeaders() });
  const text = await resp.text();
  let data = null;
  try { data = JSON.parse(text); } catch { data = text; }
  return { ok: resp.ok, status: resp.status, data };
}
async function apiPost(path, body) {
  const resp = await fetch(apiUrl(path), {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(body),
  });
  const text = await resp.text();
  let data = null;
  try { data = JSON.parse(text); } catch { data = text; }
  return { ok: resp.ok, status: resp.status, data };
}

// ──────────────────────────────────────────────────────────────────────
// Health + warmup polling
// ──────────────────────────────────────────────────────────────────────
async function pollHealth() {
  try {
    const r = await apiGet("/api/v1/healthz");
    if (r.ok) {
      state.health = r.data;
      state.warmupDone = r.data.warmup_done !== false;
      renderHealthBadge();
      return;
    }
  } catch (_) { /* 무시 */ }
  state.warmupDone = false;
  renderHealthBadge();
}

function renderHealthBadge() {
  // 임계 표시는 배지와 독립이다 — 배지 자리(#nav-status)가 없다고 임계까지 '확인 중…' 으로
  // 굳으면 안 된다. 아래 early return 앞에 둔다.
  renderConfThreshold();
  const el = $("#nav-status");
  if (!el) return;
  const h = state.health || {};
  const profile = h.deploy_profile || "unknown";
  const provider = h.llm_provider || "—";
  const embedder = h.embedding_provider || "—";
  const cls = state.warmupDone ? "ok" : "warming";   // 정상 = 녹색 배지
  el.className = `nav-status ${cls}`;
  el.innerHTML = "";
  const dot = document.createElement("span");
  dot.className = "dot";
  el.appendChild(dot);
  const txt = document.createElement("span");
  txt.textContent = `${profile} · LLM ${provider} · emb ${embedder}`;
  el.appendChild(txt);
}

// [2026-08-24] 검수 라우팅 임계를 **서버에서** 받아 적는다.
// 종전에는 index.html 본문에 임계 숫자가 글자로 박혀 있었다. 8/24 에 그 값을 0.50 으로
// 내렸는데(config.py) 그 문장은 그대로 남아, 화면이 서버와 다른 값을 말하고 있었다
// (실측 2026-08-24 223 /api/v1/healthz: review_confidence_threshold=0.5).
// healthz 의 operational_config 가 실제 라우팅을 정하는 그 값이므로 그것만 쓴다.
// 공개등급 전용 임계(review_confidence_threshold_public)는 null 이 아닐 때만 덧붙인다 —
// 손잡이가 살아 있어서, 켜졌는데 화면이 한 숫자만 보이면 오독한다.
function renderConfThreshold() {
  const el = $("#conf-threshold");
  if (!el) return;
  const oc = (state.health || {}).operational_config || {};
  const t = oc.review_confidence_threshold;
  if (typeof t !== "number") { el.textContent = "서버 확인 실패"; return; }
  const pub = oc.review_confidence_threshold_public;
  el.textContent = (typeof pub === "number")
    ? `${t.toFixed(2)} · 공개등급 예측은 ${pub.toFixed(2)}`
    : t.toFixed(2);
}

// ──────────────────────────────────────────────────────────────────────
// 샘플 셀렉터
// ──────────────────────────────────────────────────────────────────────
function populateSamples() {
  const sel = $("#sample-select");
  if (!sel) return;
  sel.innerHTML = '<option value="">— 샘플 선택 —</option>';
  DEMO_DATA.samples.forEach((s) => {
    const opt = document.createElement("option");
    opt.value = s.id;
    opt.textContent = `[${s.grade_label}] ${s.domain}`;
    sel.appendChild(opt);
  });
  sel.addEventListener("change", (e) => loadSample(e.target.value));
}

function loadSample(id) {
  const s = DEMO_DATA.samples.find((x) => x.id === id);
  if (!s) return;
  state.currentSampleId = id;
  state.currentSample = s;
  state.toggledOff.clear();
  $("#doc-title").value = s.title;
  $("#doc-body").value = s.body;
  renderToggles(s);
  renderBodyPreview(s.body);
}

function renderToggles(sample) {
  const row = $("#toggle-row");
  if (!row) return;
  row.innerHTML = "";
  sample.toggle_keywords.forEach((kw) => {
    const label = document.createElement("label");
    label.className = "kw-toggle";
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = true;
    cb.addEventListener("change", () => {
      if (cb.checked) {
        state.toggledOff.delete(kw);
        label.classList.remove("off");
      } else {
        state.toggledOff.add(kw);
        label.classList.add("off");
      }
      // textarea 본문도 동기화 — 빠진 키워드는 일반어로 치환
      syncBodyFromToggles();
    });
    label.appendChild(cb);
    label.appendChild(document.createTextNode(" " + kw));
    row.appendChild(label);
  });
}

// 와우 A — 키워드 off 시 본문에서 일반어로 치환 → 등급 변동 시연
const NEUTRAL_REPLACEMENTS = {
  // TS·S1 핵심 키워드를 평범한 표현으로 — 등급 하향 유도
  "M&A 계획": "사업 검토",
  "회사 매각": "조직 검토",
  "비공개 합병 가격": "내부 검토 가격",
  "비상 경영 계획": "운영 계획",
  "Top Secret": "기밀",
  "특급기밀": "내부 자료",
  "반도체 공정 레시피": "공정 자료",
  "EUV 공정 파라미터": "공정 파라미터 자료",
  "차세대 제품 설계도": "제품 자료",
  "특수 합금 조성비": "합금 자료",
  "암호화 알고리즘 키": "암호 자료",
  "마스터 키": "운영 키 자료",
  "보안 인증서 개인키": "인증서 자료",
  "루트 CA 개인키": "CA 자료",
  "제로데이 취약점": "보안 점검 결과",
  "알고리즘 소스코드": "소스 자료",
  "핵심 모듈 소스": "모듈 자료",
  "공정 노하우": "공정 정리본",
  "수율 개선 방법": "공정 검토 자료",
  "1급 비밀": "내부 자료",
  "원가 구조": "비용 정리본",
  "고객 데이터베이스": "고객 정리본",
  "VIP 고객 명단": "고객 정리본",
  "원가율": "비용 비율",
  "영업비밀": "내부 자료",
  // 경계 데모(경계-영업-주간공유)용 — 치환어는 반드시 비-시드여야 등급이 실제로 하향된다
  "거래처 명단": "거래처 목록",
  "분기 매출": "분기 실적",
  "VPN 접속 정보": "접속 자료",
  "관리자 계정": "운영 계정 자료",
  "내부 인증 토큰": "인증 자료",
  "내부 API 키": "API 자료",
  "취약점 분석 보고서": "점검 보고서",
};

function syncBodyFromToggles() {
  if (!state.currentSample) return;
  let body = state.currentSample.body;
  state.toggledOff.forEach((kw) => {
    const replacement = NEUTRAL_REPLACEMENTS[kw] || "관련 자료";
    body = body.split(kw).join(replacement);
  });
  $("#doc-body").value = body;
  renderBodyPreview(body);
}

function renderBodyPreview(text) {
  const preview = $("#body-preview");
  if (!preview) return;
  if (!state.lastResult) {
    preview.textContent = "";
    return;
  }
  const kws = (state.lastResult.evidence || []).map((e) => ({
    keyword: e.text,
    weight: e.weight,
    factor: e.tag,
  }));
  renderBodyWithHighlights(preview, text, kws);
}

// ──────────────────────────────────────────────────────────────────────
// 분류 호출 (와우 2 — SSE 7단계 점등)
// ──────────────────────────────────────────────────────────────────────
// 서버 ClassifyService 가 실제 emit 하는 stage 이름과 1:1 매칭.
// 서버 SSE 검증 결과: extract / normalize / embed / llm / persist / finalize.
const STAGES = [
  { key: "extract", label: "본문 추출" },
  { key: "normalize", label: "정규화" },
  { key: "embed", label: "임베딩" },
  { key: "llm", label: "분류 추론" },
  { key: "persist", label: "저장" },
  { key: "finalize", label: "결과 합성" },
];

function renderStages(map) {
  const wrap = $("#stage-seq");
  if (!wrap) return;
  wrap.innerHTML = "";
  STAGES.forEach((s, i) => {
    const step = document.createElement("div");
    const cls = map[s.key] === "done" ? "done"
      : map[s.key] === "active" ? "active" : "";
    step.className = `seq-step ${cls}`;
    step.innerHTML = `
      <div class="seq-num">${i + 1}</div>
      <div class="seq-body">
        <div class="seq-title">${s.label}</div>
        <div class="seq-detail">${stageDetail(s.key, map[s.key])}</div>
      </div>
      <div class="seq-sla">${map[s.key + "_ms"] != null ? map[s.key + "_ms"] + " ms" : "—"}</div>
    `;
    wrap.appendChild(step);
    if (i < STAGES.length - 1) {
      const arrow = document.createElement("div");
      arrow.className = "seq-arrow";
      wrap.appendChild(arrow);
    }
  });
}

function stageDetail(key, state_) {
  if (state_ === "active") return "진행 중…";
  if (state_ === "done") return "완료";
  return "대기";
}

async function runClassify() {
  if (!ensureReady()) return;
  const body = $("#doc-body").value;
  const title = $("#doc-title").value;
  if (!body || body.length < 5) {
    showError("문서 본문이 너무 짧습니다. 5자 이상 입력하세요.");
    return;
  }
  $("#btn-classify").disabled = true;
  clearResult();
  hideParseDetail();
  const stageMap = {};
  STAGES.forEach((s) => { stageMap[s.key] = "pending"; });
  renderStages(stageMap);

  // 결과 영역으로 자동 스크롤 — §2 가 한참 아래라 사용자가 못 보는 사고 방지.
  scrollToResult();

  const t0 = performance.now();
  let lastStageT = t0;
  logLine("ev", "POST /api/v1/classify/stream  (SSE 실시간 스트림 시작)");

  try {
    await postSSE(apiUrl("/api/v1/classify/stream"), {
      headers: authHeaders(),
      body: {
        doc_id: state.currentSampleId || "demo-input",
        title: title || "demo",
        content: body,
        use_rag: false,
        return_evidence: true,
      },
      onEvent: ({ event, data }) => {
        if (event === "progress") {
          const k = ((data && data.stage) || "").toLowerCase();
          const matched = STAGES.find((s) => s.key === k);
          if (matched) {
            // 이전 active 들을 done 으로
            STAGES.forEach((s) => {
              if (stageMap[s.key] === "active") {
                stageMap[s.key] = "done";
              }
            });
            stageMap[matched.key] = "active";
            const now = performance.now();
            stageMap[matched.key + "_ms"] = Math.round(now - lastStageT);
            lastStageT = now;
            renderStages(stageMap);
            logLine("ev", `event: progress · stage=${matched.key} (+${stageMap[matched.key + "_ms"]}ms)`);
          }
        } else if (event === "partial") {
          // 임시 등급
          if (data && data.grade) {
            showPartial(data.grade);
          }
        } else if (event === "result") {
          STAGES.forEach((s) => { stageMap[s.key] = "done"; });
          renderStages(stageMap);
          state.lastResult = data;
          renderResult(data, Math.round(performance.now() - t0));
          logLine("ok", `← event: result  최종 ${data.label}  (룰 ${data.rule_grade || "—"} · 모델 ${data.model_grade || "미로드"})  ${Math.round(performance.now() - t0)}ms`);
        } else if (event === "error") {
          showError((data && data.message) || "서버 오류");
        }
      },
    });
  } catch (e) {
    // SSE 실패 → 동기 /classify 폴백
    console.warn("SSE 실패, /classify 폴백:", e);
    const r = await apiPost("/api/v1/classify", {
      doc_id: state.currentSampleId || "demo-input",
      title: title || "demo",
      content: body,
      use_rag: false,
      return_evidence: true,
    });
    if (r.ok) {
      STAGES.forEach((s) => { stageMap[s.key] = "done"; });
      renderStages(stageMap);
      state.lastResult = r.data;
      renderResult(r.data, Math.round(performance.now() - t0));
    } else {
      showError(`HTTP ${r.status}: ${JSON.stringify(r.data)}`);
    }
  } finally {
    $("#btn-classify").disabled = false;
  }
}

function showPartial(grade) {
  const head = $("#result-head");
  if (!head) return;
  // [2026-08-24] 중간 스트림 값에 "신뢰도 63%" 라고 이름 붙이지 않는다. 이 값은 게이트를
  // 아직 안 지난 softmax 파생값이고, 최종 판정(자동확정/검수)은 이 숫자 단독으로 나지 않는다.
  // 숫자를 먼저 크게 보여주면 화면이 설명하는 판정 논리가 실제 판정 논리와 어긋난다.
  head.innerHTML = `
    <span class="result-grade g-${grade}">${grade}</span>
    <div class="result-confidence">
      <div style="font-size:12px;color:var(--text-dim)">잠정 등급 — 게이트 판정 전</div>
    </div>
    <div class="result-time"><span class="pulse-dot"></span>최종 결과 대기 중…</div>
  `;
}

// ──────────────────────────────────────────────────────────────────────
// 결과 렌더 (자연어 3단 + 4 평가요소 + 키워드 + 본문 하이라이트)
// ──────────────────────────────────────────────────────────────────────
function renderResult(data, elapsedMs) {
  const head = $("#result-head");
  const grade = data.label;
  const elapsed = elapsedMs || data.elapsed_ms || 0;

  // [2026-08-24] 머리에 두는 것은 신뢰도 숫자가 아니라 **결정**이다.
  //   왜: 자동확정은 conf 단독이 아니라 다단 게이트로 난다(합의 게이트·희소근거·메타데이터 floor).
  //   숫자를 머리에 두면 화면이 설명하는 판정 논리가 실제와 어긋난다 — 실측 2026-08-21(223,
  //   demo_docs/03_S2_supplier_price.xlsx)에서 룰=모델=S2 로 일치하는데 conf 0.596 이라
  //   검수로 간 카드에 「검수 필요」 배너와 「…자동 확정」 설명이 동시에 떴다.
  //   게다가 conf 는 정답/오답 판별력이 약하다(골든500 AUROC 0.58) — 크게 띄울 값이 아니다.
  //   숫자는 없애지 않고 renderSummary 의 접힌 상세로 내린다(FUN-024 심층지표·감리 추적).
  const needsReview = data.status === "needs_review";
  const why = (typeof window !== "undefined" && window.KOIPA_GATE_REASON)
    ? window.KOIPA_GATE_REASON(data.warnings) : "";
  const verdict = needsReview
    ? `<div style="font-size:13px;font-weight:700;color:#e11d2e">검수 필요</div>
       <div style="font-size:12px;color:var(--text-dim);margin-top:2px">${escapeHtml(why || "자동 확정하지 않고 사람 검수로 라우팅")}</div>`
    : `<div style="font-size:13px;font-weight:700;color:#0a7f3f">자동 확정</div>
       <div style="font-size:12px;color:var(--text-dim);margin-top:2px">게이트 통과 — 사람 검수 없이 확정 경로</div>`;
  head.innerHTML = `
    <span class="result-grade g-${grade}" data-grade="${grade}" role="button" title="법령 근거 보기">${grade}</span>
    <div class="result-confidence">${verdict}</div>
    <div class="result-time">응답 <b>${elapsed} ms</b><br/>model_version ${data.model_version || "poc"}</div>
  `;

  // 등급 배지 클릭 → 법령 카드로 (와우 C-1)
  const badge = head.querySelector(".result-grade");
  if (badge) {
    badge.style.cursor = "pointer";
    badge.addEventListener("click", () => focusLegalCard(grade));
  }

  // 룰·분류기·최종 이중판정 (운영 하이브리드 그대로)
  renderDualVerdict(data);
  // 자연어 요약 3단 (양성·음성 근거) + 서버 status 반영
  renderSummary(data);
  // 원문 경고는 화면 카드가 아니라 실시간 로그로 보낸다 — 감리·디버깅에서 서버 응답과
  // 대조할 수 있어야 하지만, 판정 카드에 영어 원문과 수치가 섞이면 화면이 읽히지 않는다.
  (data.warnings || []).forEach((w) => logLine("info", `warning: ${w}`));
  // 평가요소 stats (factors_source=model_estimated 는 '모델 추정'으로 구분)
  renderFactors(data.evaluation_factors || {}, data.factors_source);
  // 키워드 칩 (weight 진하기)
  renderKeywordChips(data.evidence || []);
  // 본문 하이라이트
  renderBodyPreview($("#doc-body").value);
  // 결과 도착 시점에 한 번 더 스크롤 + 강조 펄스 + 토스트 (사용자 시선 확보).
  scrollToResult({ flash: true });
  showResultToast(grade, elapsed);
}

function _escLog(s) { return String(s == null ? "" : s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c])); }
function _gradeCls(g) { return g && ["TS", "S1", "S2", "S3"].includes(g) ? "g-" + g : "g-na"; }

// 룰·분류기·최종 이중판정 렌더 (운영 하이브리드와 동일 데이터)
function renderDualVerdict(data) {
  const wrap = $("#result-dual");
  if (!wrap) return;
  const rule = data.rule_grade || null;
  const model = data.model_grade || null;
  const final = data.label;
  const decision = data.decision_path || "";
  const adjustNote = (typeof window !== "undefined" && window.KOIPA_ADJUSTMENT_NOTE)
    ? window.KOIPA_ADJUSTMENT_NOTE(data) : "";
  const modelCard = model
    ? `<div class="dual-card"><div class="dual-label">② 분류기 (BERT)</div><span class="dual-grade ${_gradeCls(model)}">${_escLog(model)}</span><div class="dual-sub">학습 모델 단독 판정</div></div>`
    : `<div class="dual-card"><div class="dual-label">② 분류기 (BERT)</div><span class="dual-grade g-na">미로드</span><div class="dual-sub">모델 미로드 — 룰 단독</div></div>`;
  wrap.innerHTML =
    `<div class="dual-card"><div class="dual-label">① 룰 엔진</div><span class="dual-grade ${_gradeCls(rule)}">${_escLog(rule || "—")}</span><div class="dual-sub">시드 키워드 S×V×M</div></div>`
    + modelCard
    + `<div class="dual-card final"><div class="dual-label">③ 최종 (결합)</div><span class="dual-grade ${_gradeCls(final)}">${_escLog(final)}</span><div class="dual-sub">안전 규칙 결합 결과</div></div>`
    + (decision ? `<div class="dual-decision"><b>결합:</b> ${_escLog(decision)}</div>` : "")
    + (adjustNote ? `<div class="dual-decision"><b>안전 규칙:</b> ${_escLog(adjustNote)}</div>` : "");
}

// 실시간 요청 로그 — 실제 fetch/SSE 기록(위조 아님, 서버 터미널과 대조 가능)
function logLine(kind, msg) {
  const box = $("#live-log");
  if (!box) return;
  const empty = box.querySelector(".ll-empty");
  if (empty) empty.remove();
  const cls = kind === "err" ? "ll-err" : kind === "ev" ? "ll-ev" : kind === "ok" ? "ll-ok" : "";
  const line = document.createElement("div");
  line.className = "ll";
  line.innerHTML = `<span class="ll-time">${new Date().toLocaleTimeString()}</span> <span class="${cls}">${_escLog(msg)}</span>`;
  box.appendChild(line);
  box.scrollTop = box.scrollHeight;
}

// 파일 업로드 → 실제 백엔드 파싱+이중판정 (POST /documents/analyze)
// 업로드 경로용 타임라인 — analyze 응답의 실제 단계를 단계별 실측 시간(ms)과 함께 렌더.
function renderStagesFromAnalyze(stages) {
  const wrap = $("#stage-seq");
  if (!wrap) return;
  wrap.innerHTML = "";
  const arr = stages || [];
  arr.forEach((s, i) => {
    const cls = s.status === "done" ? "done"
      : (s.status === "review" || s.status === "fail" || s.status === "skipped") ? "active" : "";
    const step = document.createElement("div");
    step.className = `seq-step ${cls}`;
    step.innerHTML = `
      <div class="seq-num">${i + 1}</div>
      <div class="seq-body">
        <div class="seq-title">${_escLog(s.name)}</div>
        <div class="seq-detail">${_escLog(s.detail || "")}</div>
      </div>
      <div class="seq-sla">${s.ms != null ? s.ms + " ms" : "—"}</div>`;
    wrap.appendChild(step);
    if (i < arr.length - 1) {
      const arrow = document.createElement("div");
      arrow.className = "seq-arrow";
      wrap.appendChild(arrow);
    }
  });
}

// ──────────────────────────────────────────────────────────────────────
// 파일 분석 — 파싱 → 검수 게이트 → 등급까지 한 번에
//   [2026-08-24] 별도 구역(#sec-parse, 구 parse_demo.html)의 인라인 스크립트를 여기로
//   합쳤다. 같은 엔드포인트를 두 코드가 각각 호출하고 등급 결과 카드를 두 벌 그리고
//   있었다(사용자 지적). 결과 렌더는 §2 한 곳(renderResult)으로 모으고, 파싱 세부와
//   검수 게이트만 §2 안의 접이식 「파싱 상세」로 내린다.
// ──────────────────────────────────────────────────────────────────────

// ICD 문서 속성 — API 는 진작부터 받는데 입력칸이 없어 아무도 쓸 수 없던 세 필드.
// 관리성(M)은 본문에서 관측되지 않으므로(짧은 문서가 자기 접근통제를 진술하지 않는다)
// 이 값이 유일한 근거다. ⚠ 빈 값은 보내지 않는다 — documents.py 주석: 빈 값을 넣으면
// "unknown" 과 "명시적으로 없음" 이 구분되지 않아 관리성 판정이 뒤집힌다.
const ICD_FIELDS = [
  ["source_type", "#icd-source"],
  ["security_marking", "#icd-marking"],
  ["access_scope", "#icd-scope"],
];
function icdEntries() {
  const out = [];
  ICD_FIELDS.forEach(([field, sel]) => {
    const el = $(sel);
    const v = el && el.value ? el.value.trim() : "";
    if (v) out.push([field, v]);
  });
  return out;
}

/* 분석 진행 팝업 — 큰 문서는 몇 분이 걸린다.
 * 서버가 진행률을 주지 않으므로 **경과 시간**과 **예상 시간**만 보여 준다.
 * 예상은 223 실측(2026-08-21)에서 나온 계수다: 14청크 6.2초 → 고정비 약 0.5초 + 0.4초/청크.
 * 청크 수는 서버가 파싱해야 알 수 있으므로 파일 크기로 어림한다(1청크 ≈ 350자 ≈ 700바이트).
 * 어림이라는 것을 화면에 그대로 적는다 — 정확한 척하지 않는다. */
const analyzeProgress = (function () {
  let box = null, t0 = 0, timer = null, est = 0;
  function ensure() {
    if (box) return box;
    box = document.createElement("div");
    box.id = "analyze-progress";
    box.style.cssText = "position:fixed;inset:0;background:rgba(17,17,17,.72);display:flex;"
      + "align-items:center;justify-content:center;z-index:300;padding:20px";
    box.innerHTML = '<div style="background:#fff;max-width:420px;width:100%;padding:26px 28px;'
      + 'box-shadow:0 12px 40px rgba(0,0,0,.28)">'
      + '<div style="display:flex;align-items:center;gap:10px">'
      + '<span id="ap-spin" style="display:inline-block;width:15px;height:15px;border:2px solid #d9d9d6;'
      + 'border-top-color:#111;border-radius:50%;animation:ap-rot .8s linear infinite"></span>'
      + '<b style="font-size:15px">문서를 분석하고 있습니다</b></div>'
      + '<div id="ap-file" style="margin-top:10px;font-size:12.5px;color:#70757a;word-break:break-all"></div>'
      + '<div style="display:flex;gap:22px;margin-top:16px">'
      + '<div><div style="font-size:11px;color:#8f9498">경과</div>'
      + '<b id="ap-el" style="font:700 22px/1.2 Arial,sans-serif">0초</b></div>'
      + '<div><div style="font-size:11px;color:#8f9498">예상(어림)</div>'
      + '<b id="ap-est" style="font:700 22px/1.2 Arial,sans-serif;color:#70757a">–</b></div></div>'
      + '<div style="height:4px;background:#eceae7;margin-top:14px;overflow:hidden">'
      + '<div id="ap-bar" style="height:100%;width:0;background:#111;transition:width .4s linear"></div></div>'
      + '<div id="ap-note" style="margin-top:12px;font-size:12px;color:#70757a;line-height:1.6"></div></div>';
    const st = document.createElement("style");
    st.textContent = "@keyframes ap-rot{to{transform:rotate(360deg)}}";
    document.body.appendChild(st);
    document.body.appendChild(box);
    return box;
  }
  function fmtSec(s) { return s < 60 ? s + "초" : Math.floor(s / 60) + "분 " + String(s % 60).padStart(2, "0") + "초"; }
  return {
    start(file) {
      ensure().style.display = "flex";
      const bytes = (file && file.size) || 0;
      const chunks = Math.max(1, Math.round(bytes / 700));
      est = Math.max(3, Math.round(0.5 + chunks * 0.4));
      document.getElementById("ap-file").textContent =
        (file ? file.name : "") + (bytes ? ("  ·  " + bytes.toLocaleString() + "B  ·  청크 약 " + chunks.toLocaleString() + "개") : "");
      document.getElementById("ap-est").textContent = fmtSec(est);
      document.getElementById("ap-note").textContent = chunks > 60
        ? "큰 문서라 몇 분 걸릴 수 있습니다. 창을 닫지 마십시오 — 끝나면 결과가 바로 나옵니다."
        : "첫 요청은 모델을 올리느라 조금 더 걸립니다.";
      t0 = Date.now();
      clearInterval(timer);
      timer = setInterval(() => {
        const el = Math.round((Date.now() - t0) / 1000);
        document.getElementById("ap-el").textContent = fmtSec(el);
        /* 예상을 넘기면 막대를 95%에서 멈춘다 — 100%로 채워 놓고 안 끝나면 그게 거짓말이다. */
        const pct = Math.min(95, Math.round(el / Math.max(est, 1) * 100));
        document.getElementById("ap-bar").style.width = pct + "%";
        if (el > est) document.getElementById("ap-est").textContent = fmtSec(est) + " 초과";
      }, 1000);
    },
    note(msg) {
      const el = box && document.getElementById("ap-note");
      if (el) el.textContent = msg;
    },
    stop() { clearInterval(timer); if (box) box.style.display = "none"; },
  };
})();

function _pdFmt(x) { return (x == null) ? "—" : Number(x).toFixed(3); }

// 파싱 상세 — 추출기·품질·글자수·청크·표·PII·오류 + 검수 게이트.
// 파일 분석에만 있는 정보라 텍스트 분류에서는 접이식 자체를 숨긴다.
function renderParseDetail(j) {
  const fold = $("#parse-detail");
  const kvBox = $("#parse-kv");
  const gateBox = $("#gate-box");
  if (!fold || !kvBox || !gateBox) return;
  const p = j.parse || {};
  /* [2026-08-21] 파싱 결과를 이름/값 11줄로 세워 놓으니 세로로만 길고 읽기 나빴다
     (사용자 지적). 숫자는 타일로 묶고 나머지는 한 줄짜리 메모로 내린다. */
  const tile = (cap, val, sub, warn) => `
    <div style="border:1px solid var(--border,#e1e1de);padding:9px 11px;min-width:0">
      <div style="font-size:11px;color:var(--text-dim,#8f9498);letter-spacing:.02em">${escapeHtml(cap)}</div>
      <div style="font:700 17px/1.25 var(--font-sans,sans-serif);margin-top:2px;color:${warn ? "#bf2337" : "inherit"};word-break:break-all">${escapeHtml(val)}</div>
      ${sub ? `<div style="font-size:11px;color:var(--text-dim,#8f9498);margin-top:1px">${escapeHtml(sub)}</div>` : ""}
    </div>`;
  const note = (cap, val, warn) => `
    <div style="display:flex;gap:10px;padding:6px 0;border-top:1px solid var(--border,#e1e1de);font-size:12.5px">
      <span style="flex:0 0 92px;color:var(--text-dim,#8f9498)">${escapeHtml(cap)}</span>
      <span style="flex:1;min-width:0;word-break:break-all;color:${warn ? "#bf2337" : "inherit"}">${escapeHtml(val)}</span>
    </div>`;
  const q = Number(p.extraction_quality || 0);
  kvBox.innerHTML = `
    <div style="font:600 13.5px/1.45 var(--font-sans,sans-serif);word-break:break-all">${escapeHtml(j.filename || "")}</div>
    <div style="font-size:12px;color:var(--text-dim,#8f9498);margin:2px 0 12px">
      ${escapeHtml((p.source_format || "?").toUpperCase())} · ${(j.file_size_bytes || 0).toLocaleString()}B ·
      추출기 ${escapeHtml(p.extraction_method || "—")}${p.ocr_used ? " (OCR)" : ""}
    </div>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(112px,1fr));gap:8px">
      ${tile("추출 품질", _pdFmt(p.extraction_quality), "콘텐츠 " + _pdFmt(p.content_quality), q > 0 && q < 0.8)}
      ${tile("글자수", (p.char_count || 0).toLocaleString(), "청크 " + (p.chunk_count || 0) + "개")}
      ${tile("구조화 표", (p.table_count || 0) + "개", "셀 " + (p.table_cell_count || 0) + "개")}
      ${tile("PII 마스킹", (p.pii_masked_count || 0) + "건", p.table_coverage ? ("표 커버리지 " + p.table_coverage) : "")}
    </div>
    <div style="margin-top:12px">
      ${note("추출 오류", p.extract_error || "없음", !!p.extract_error)}
      ${(p.warnings || []).length ? note("파싱 경고", (p.warnings || []).join(", ")) : ""}
    </div>`;
  const g = j.gate || {};
  gateBox.innerHTML = g.requires_review
    ? `<div class="gate-review"><b>검수 필요</b> — 자동 확정하지 않고 사람 검수로 라우팅됩니다.<div>${(g.reasons || []).map((r) => `<span class="reason-chip">${escapeHtml(r)}</span>`).join("")}</div></div>`
    : `<div class="gate-ok"><b>자동 경로 통과</b> — 추출 품질·표·OCR 이상 없음(무오탐).</div>`;
  fold.open = true;
}

// 텍스트 분류에는 파싱 단계가 없다 — 접이식은 남기되 내용을 안내로 되돌린다.
// (숨기지 않는 이유: #sec-parse 앵커가 사용설명서·배포 가이드에 인쇄돼 있어
//  그 주소로 온 사람이 빈 자리에 떨어지면 안 된다.)
function hideParseDetail() {
  const fold = $("#parse-detail");
  if (fold) fold.open = false;
  const kv = $("#parse-kv");
  if (kv) kv.innerHTML = '<span class="hint">파일을 올리면 추출기·추출 품질·글자수·청크 수·구조화 표·PII 마스킹 건수가 여기에 표시됩니다.</span>';
  const gate = $("#gate-box");
  if (gate) gate.innerHTML = "";
  const pb = $("#persist-box");
  if (pb) pb.innerHTML = "";
}

// analyze 응답 → §2 결과 렌더 (동기·비동기 경로 공통)
function renderAnalyzeResult(j, ms, file) {
  const c = j.classification;
  renderStagesFromAnalyze(j.stages);
  renderParseDetail(j);
  if (!c) {
    logLine("err", "← 200 그러나 분류 없음 (본문 0자/게이트) — 검수 필요");
    showError("본문 추출 0자 또는 안전 게이트 → 검수 필요");
    return null;
  }
  const data = {
    label: c.label, confidence: c.confidence, scores: c.scores,
    evaluation_factors: c.factors, factors_source: c.factors_source,
    evidence: (j.evidence || []), model_version: c.model_version, elapsed_ms: c.elapsed_ms,
    warnings: c.warnings || [], status: c.status,
    rule_grade: c.rule_grade, model_grade: c.model_grade, decision_path: c.decision_path,
  };
  state.currentSampleId = null;
  state.lastResult = data;
  $("#doc-title").value = file.name;
  $("#doc-body").value = j.text_preview || "";
  renderResult(data, ms);
  // 본문 하이라이트 명시적 재호출(업로드 경로 확실히 반영)
  renderBodyPreview($("#doc-body").value);
  const g = j.gate || {};
  logLine("ok", `← 200  최종 ${data.label}  (룰 ${data.rule_grade} · 모델 ${data.model_grade})  ${ms}ms · 파싱 ${(j.parse && j.parse.char_count) || 0}자${g.requires_review ? " · 게이트:검수" : ""}`);
  return data;
}

async function analyzeFile(file) {
  if (!ensureReady()) return;
  if (!file) return;
  $("#btn-classify").disabled = true;
  const _dz = $("#doc-drop"); if (_dz) _dz.classList.add("busy");
  clearResult();
  hideParseDetail();
  // 이전 샘플 잔재 초기화 — '지금 분석 중 = 이 파일'만 명확히 보이게
  const _sel = $("#sample-select"); if (_sel) _sel.value = "";
  const _tr = $("#toggle-row"); if (_tr) _tr.innerHTML = "";
  if (state.toggledOff && state.toggledOff.clear) state.toggledOff.clear();
  state.currentSample = null;
  state.currentSampleId = null;
  $("#doc-title").value = file.name;
  $("#doc-body").value = `업로드 파일 분석 중… (${file.name})`;
  const stageMap = {};
  STAGES.forEach((s) => { stageMap[s.key] = "pending"; });
  renderStages(stageMap);
  scrollToResult();
  analyzeProgress.start(file);
  const fd = new FormData();
  fd.append("file", file);
  fd.append("return_evidence", "true");
  icdEntries().forEach(([field, v]) => fd.append(field, v));
  const t0 = performance.now();
  // 대용량 비동기 경로로 넘어갔는가 — 그 경로에서는 실적재를 하지 않는다.
  let wentAsync = false;
  logLine("ev", `POST /api/v1/documents/analyze  ← ${file.name} (${file.size.toLocaleString()} bytes)`);
  try {
    const resp = await fetch(apiUrl("/api/v1/documents/analyze"), { method: "POST", headers: authHeaders(), body: fd });
    let ms = Math.round(performance.now() - t0);
    let j = await resp.json();
    if (!resp.ok) {
      const detail = (j && (j.detail || j.message)) || "";
      /* [2026-08-23] 대용량 자동 전환. 상한(analyze_sync_max_chunks)이 있는 이유는 이 경로가
         추출부터 분류까지 한 요청 안에서 끝내기 때문이지 문서를 처리할 수 없어서가 아니다.
         classify=false 로 본문만 회수해 POST /classify/async 로 넘기면 워커에서 분류된다.
         DB 적재는 하지 않는다 — doc_id 를 비-UUID 로 보내 서버 영속화 가드가 건너뛰게 한다. */
      const cap = String(detail).match(/(\d+) chunks > (\d+)/);
      if (resp.status === 413 && cap) {
        j = await analyzeLargeAsync(file, cap);
        if (!j) return;
        ms = Math.round(performance.now() - t0);
        wentAsync = true;
      } else {
        logLine("err", `HTTP ${resp.status}  ${JSON.stringify(j).slice(0, 160)}`);
        showError(`HTTP ${resp.status}: ${JSON.stringify(j).slice(0, 200)}`);
        return;
      }
    }
    const data = renderAnalyzeResult(j, ms, file);
    // 실적재 — 검수 대상일 때만. 자동 확정 건까지 큐에 넣으면 큐가 데모로 오염된다.
    // 대용량 비동기 경로는 제외한다 — 큰 문서를 조용히 적재하지 않는다(원 구역과 같은 규칙).
    const persist = $("#persist-demo");
    if (persist && persist.checked && !wentAsync && (!data || data.status === "needs_review")) {
      await persistToQueue(j, file);
    } else if (persist && persist.checked && wentAsync) {
      logLine("info", "실적재 건너뜀 — 대용량 비동기 경로는 적재하지 않습니다.");
    }
  } catch (e) {
    logLine("err", `오류: ${e.message}`);
    showError(e.message);
  } finally {
    analyzeProgress.stop();
    $("#btn-classify").disabled = false;
    const dz = $("#doc-drop"); if (dz) dz.classList.remove("busy");
  }
}

/* 대용량 자동 전환 — 적재 없이 비동기로 분류한다.
   (1) POST /documents/analyze (classify=false) — 추출·정규화·PII·청킹까지만. 이 엔드포인트는
       설계상 read-only 라 documents/chunks 를 쓰지 않는다(documents.py docstring).
   (2) POST /classify/async {doc_id:"demo-nopersist-<ts>", content} — 브로커가 살아 있으면
       Celery 워커에서 돈다(gunicorn 요청 타임아웃 밖). doc_id 가 UUID 가 아니라서 서버의
       영속화 가드가 저장을 건너뛴다 → documents·classifications 어디에도 행이 생기지 않는다.
   (3) GET /classify/jobs/{id} 폴링 → 결과를 (1)의 응답에 합쳐 평소와 같은 화면으로 그린다.
   주의: 「실적재」는 이 경로에서 동작하지 않는다 — 대용량 건을 조용히 적재하지 않는다. */
async function analyzeLargeAsync(file, cap) {
  analyzeProgress.note("큰 문서 — 본문을 추출한 뒤 비동기 분류로 넘깁니다…");
  logLine("info", `대용량 전환: 청크 ${cap[1]}개 > 동기 한도 ${cap[2]}개 → classify=false 추출 + /classify/async (DB 적재 없음)`);
  try {
    // (1) 추출만 — 청크 상한 게이트를 타지 않는다(상한은 분류 비용 때문에 있는 것이다).
    const fd = new FormData();
    fd.append("file", file);
    fd.append("return_evidence", "true");
    fd.append("full_text", "true");
    fd.append("classify", "false");
    icdEntries().forEach(([field, v]) => fd.append(field, v));
    const r = await fetch(apiUrl("/api/v1/documents/analyze"), { method: "POST", headers: authHeaders(), body: fd });
    const j = await r.json();
    if (!r.ok) throw new Error(`추출 실패 HTTP ${r.status} ${String((j && j.detail) || "").slice(0, 200)}`);
    const text = (j.text || j.text_preview || "").trim();
    if (!text) {
      renderStagesFromAnalyze(j.stages);
      renderParseDetail(j);
      showError("본문 추출 0자 — 분류하지 않았습니다.");
      return null;
    }
    // ClassifyRequest.content 의 max_length. 넘기면 422 가 나므로 먼저 이유를 말한다.
    const MAX = 1048576;
    if (text.length > MAX) throw new Error(`본문 ${text.length.toLocaleString()}자 — 비동기 분류 본문 상한 ${MAX.toLocaleString()}자 초과`);
    logLine("info", `추출 완료 ${text.length.toLocaleString()}자 · 청크 ${(j.parse || {}).chunk_count || "?"} — 비동기 분류 제출`);

    // (2) 제출. doc_id 를 비-UUID 로 보내 서버가 저장을 건너뛰게 한다.
    const meta = {};
    icdEntries().forEach(([field, v]) => { meta[field] = v; });
    const sub = await fetch(apiUrl("/api/v1/classify/async"), {
      method: "POST",
      headers: Object.assign({ "Content-Type": "application/json" }, authHeaders()),
      body: JSON.stringify({
        doc_id: "demo-nopersist-" + Date.now(),
        title: file.name,
        content: text,
        metadata: Object.keys(meta).length ? meta : null,
        return_evidence: true,
      }),
    });
    const sj = await sub.json().catch(() => ({}));
    if (!sub.ok || !sj.job_id) throw new Error(`분류 제출 실패 HTTP ${sub.status} ${JSON.stringify(sj).slice(0, 200)}`);
    logLine("info", `비동기 분류 job ${sj.job_id} (${sj.status || "?"})`);

    // (3) 폴링. 서버 작업 상한(celery soft 900초)보다 길게 기다리지 않는다.
    let res = null;
    for (let i = 0; i < 450; i++) {
      await new Promise((z) => setTimeout(z, 2000));
      const st = await fetch(apiUrl(`/api/v1/classify/jobs/${encodeURIComponent(sj.job_id)}`), { headers: authHeaders() });
      const sd = await st.json().catch(() => ({}));
      if (!st.ok) throw new Error(`잡 조회 실패 HTTP ${st.status}`);
      analyzeProgress.note(`비동기 분류 중 — ${sd.status || "?"} (${(i + 1) * 2}초)`);
      if (sd.status === "done" || sd.status === "partial") { res = (sd.results || [])[0]; break; }
      if (sd.status === "failed") throw new Error(`분류 실패: ${sd.error || "사유 미상"}`);
    }
    if (!res) throw new Error("900초 안에 끝나지 않았습니다 — 서버 작업 시간 상한(celery soft 900초) 확인 필요");

    /* ClassifyResponse → AnalyzeClassification 모양으로 맞춘다(같은 렌더를 쓴다).
       'persistence skipped: ... is not a UUID' 는 **설계대로 저장을 건너뛴 것**이라 경고에서
       뺀다 — 동기 경로도 같은 이유로 뺀다(documents.py). 다른 persistence 경고는 남긴다. */
    const warns = (res.warnings || []).filter((w) => !(String(w).indexOf("persistence skipped") >= 0 && String(w).indexOf("is not a UUID") >= 0));
    const gate = j.gate || {};
    let status = res.status;
    // 동기 경로와 같은 승격 규칙 — 추출 게이트가 검수를 요구하면 needs_review 로 올린다.
    if (gate.requires_review && status !== "needs_review") {
      status = "needs_review";
      warns.push(`extraction_gate: 열화 추출(표누락/OCR/저품질)→검수 라우팅 (${(gate.reasons || []).join(", ")})`);
    }
    j.classification = {
      label: res.label, confidence: Math.round((res.confidence || 0) * 1000) / 1000,
      scores: res.scores || {}, status: status, model_version: res.model_version,
      factors: res.evaluation_factors || {}, factors_source: res.factors_source || null,
      rule_factors: res.rule_evaluation_factors || null,
      warnings: warns, elapsed_ms: res.elapsed_ms || 0,
      rule_grade: res.rule_grade || null, model_grade: res.model_grade || null,
      decision_path: res.decision_path || null,
    };
    j.evidence = res.evidence || [];
    const sk = (j.stages || []).find((s) => s.name === "분류");
    if (sk) {
      sk.status = "done";
      sk.detail = `${res.label} · ${res.model_version} (비동기 워커 · DB 미적재)`;
      sk.ms = res.elapsed_ms || null;
    }
    (j.stages || []).push({
      name: "결과",
      status: (status === "needs_review" ? "review" : "done"),
      detail: (status === "needs_review" ? "검수 필요(needs_review)" : "자동 확정(staging)") + " — 비동기 경로, 데이터베이스에 저장하지 않았습니다",
    });
    logLine("ok", `비동기 분류 OK ${res.label} status=${status}`);
    return j;
  } catch (e) {
    logLine("err", `비동기 분류 오류: ${e.message}`);
    showError(`비동기 분류 중단 — ${e.message}`);
    return null;
  }
}

// 실적재 — 분석과 별개로 실제 서빙 경로(POST /documents → /classify)로 적재해
// needs_review 건이 거버넌스 콘솔 DB 검수 큐에 나타나게 한다. created_by=demo-console 마커라
// admin의 「데모 데이터 초기화」로 스코프 삭제 가능. RAG 는 'demo' 컬렉션(평가 'docs' 분리)에 색인.
async function persistToQueue(analysis, file) {
  const box = $("#persist-box");
  if (!file || !box) return;
  box.innerHTML = '<span class="hint">실적재 중… (POST /documents → /classify)</span>';
  const hdrRole = Object.assign({ "X-Actor-Role": "admin" }, authHeaders());
  try {
    // 1) 실제 적재 — documents/chunks 저장 + RAG demo 색인, created_by=demo-console 마커
    const fd = new FormData();
    fd.append("actor", JSON.stringify({ user_id: "demo-console", role: "admin" }));
    fd.append("index_for_rag", "true");
    fd.append("rag_namespace", "demo");
    fd.append("file", file);
    /* [2026-08-21] 분석과 **같은 ICD 값**을 실적재에도 싣는다.
       종전에는 이 경로가 세 필드를 안 보내서, 화면에는 S1 로 분석돼 있는데 검수 큐에 들어간
       문서는 S2 가 되는 모순이 났다(같은 파일·같은 화면인데 등급이 다르다). */
    icdEntries().forEach(([field, v]) => fd.append(field, v));
    const up = await fetch(apiUrl("/api/v1/documents"), { method: "POST", headers: hdrRole, body: fd });
    const uj = await up.json().catch(() => ({}));
    if (!up.ok || !uj.doc_id) {
      box.innerHTML = `<div class="gate-review"><b>실적재 실패(업로드)</b>: HTTP ${up.status} ${escapeHtml(JSON.stringify(uj).slice(0, 180))}</div>`;
      logLine("err", `실적재 업로드 실패 HTTP ${up.status}`);
      return;
    }
    logLine("ok", `적재 OK doc_id=${uj.doc_id} (검수필요=${uj.requires_review} · RAG색인=${uj.rag_indexed})`);
    // 2) 실제 서빙 경로 분류 — 본문을 직접 전달. 적재 시 ingestion needs_review 격리는
    //    서버가 존중(_ingestion_flagged_for_doc)해 검수 큐에 실린다.
    const content = (analysis && analysis.text_preview) || "";
    const cl = await fetch(apiUrl("/api/v1/classify"), {
      method: "POST",
      headers: Object.assign({ "Content-Type": "application/json" }, hdrRole),
      body: JSON.stringify(content ? { doc_id: uj.doc_id, content: content } : { doc_id: uj.doc_id }),
    });
    const cj = await cl.json().catch(() => ({}));
    if (!cl.ok) {
      box.innerHTML = `<div class="gate-review"><b>실적재 분류 실패</b>: HTTP ${cl.status} ${escapeHtml(JSON.stringify(cj).slice(0, 180))}</div>`;
      logLine("err", `실적재 분류 실패 HTTP ${cl.status}`);
      return;
    }
    const st = cj.status || "?";
    const routed = (st === "needs_review");
    box.innerHTML = `<div class="${routed ? "gate-review" : "gate-ok"}">
      <b>실적재 완료</b> — doc_id <code>${escapeHtml(uj.doc_id)}</code> · 등급 <b>${escapeHtml(cj.label || "?")}</b> · 상태 <b>${escapeHtml(st)}</b>
      <div style="margin-top:4px">${routed
        ? "→ <b>거버넌스 콘솔 → 「DB 검수 큐 불러오기」</b> 하면 이 문서가 검수 대기로 나타납니다."
        : "자동 확정(staging) — needs_review 가 아니라 검수 큐에는 나타나지 않습니다."}</div></div>`;
    logLine("ok", `실적재 분류 OK ${cj.label || "?"} status=${st}`);
  } catch (e) {
    box.innerHTML = `<div class="gate-review"><b>실적재 오류</b>: ${escapeHtml(e.message)}</div>`;
    logLine("err", `실적재 오류: ${e.message}`);
  }
}

// 참고 샘플 6종 — 정적 자산을 받아 업로드와 **같은 경로**로 분석한다(별도 분기 없음).
async function loadFileSample(url, name) {
  try {
    const r = await fetch(url);
    if (!r.ok) { logLine("err", `샘플 로드 실패 ${name} HTTP ${r.status}`); showError(`샘플 로드 실패: HTTP ${r.status}`); return; }
    const blob = await r.blob();
    const f = new File([blob], name, { type: blob.type || "application/octet-stream" });
    logLine("ev", `시연 샘플 로드: ${name} (${f.size.toLocaleString()}B) → 분석 실행`);
    const nameBox = $("#doc-file-name");
    if (nameBox) nameBox.textContent = `${f.name} (${f.size.toLocaleString()} bytes)`;
    await analyzeFile(f);
  } catch (e) {
    logLine("err", `샘플 로드 오류: ${e.message}`);
    showError(e.message);
  }
}

// 운영 대시보드 — DB 실측 카운트 폴링
async function refreshDashboard() {
  const box = $("#ops-tiles");
  if (!box) return;
  try {
    const r = await apiGet("/api/v1/dashboard/summary");
    if (!r.ok) return;
    const d = r.data || {};
    const g = d.by_grade || {};
    const chips = ["TS", "S1", "S2", "S3"].map((k) => `<span class="gchip g-${k}">${k} ${g[k] || 0}</span>`).join("");
    const tiles = [
      { n: d.total_classifications || 0, l: "총 분류수" },
      { html: chips, l: "등급 분포", grade: true },
      { n: d.review_queue || 0, l: "검수 대기" },
      { n: d.auto_confirmed || 0, l: "자동 확정" },
      { n: d.corrections || 0, l: "검수 교정" },
      { n: d.verified_labels || 0, l: "검수 반영" },
    ];
    box.innerHTML = tiles.map((t) =>
      `<div class="ops-tile ${t.grade ? "grade" : ""}"><div class="ops-num">${t.grade ? t.html : t.n}</div><div class="ops-lbl">${t.l}</div></div>`
    ).join("");
  } catch (e) { /* best-effort */ }
}

// 원클릭 "실시간 반영 시연" — 등록→분류→교정→승급→재분류, 전부 서버 실호출
async function runReflectDemo() {
  if (!ensureReady()) return;
  const btn = $("#btn-reflect");
  if (btn) btn.disabled = true;
  const stepsBox = $("#reflect-steps");
  const S = [];
  const render = () => { stepsBox.innerHTML = S.map((s) => `<div class="rstep ${s.state || ""}"><span class="rdot"></span><span>${s.html}</span></div>`).join(""); };
  const add = (html) => { S.push({ html, state: "active" }); render(); };
  const finish = (html) => { if (S.length) { S[S.length - 1].state = "done"; if (html) S[S.length - 1].html = html; } render(); };
  const rev = { user_id: "reviewer-demo", role: "reviewer" };
  const jhdr = () => ({ ...authHeaders(), "Content-Type": "application/json" });
  const busy = $("#reflect-busy");
  busy.textContent = "실행 중…";
  try {
    const content = "본 문서는 일반 사내 공지입니다. 다음 주 회의 일정과 점심 메뉴를 안내합니다. (검수 반영 시연용)";
    add("① 문서 등록 (POST /documents)…");
    const fd = new FormData();
    fd.append("file", new File([content], `reflect_${Date.now()}.txt`, { type: "text/plain" }));
    fd.append("actor", JSON.stringify(rev));
    const regj = await (await fetch(apiUrl("/api/v1/documents"), { method: "POST", headers: authHeaders(), body: fd })).json();
    const docId = regj.doc_id;
    if (!docId) throw new Error("등록 실패(doc_id 없음): " + JSON.stringify(regj).slice(0, 120));
    finish(`① 등록 완료 · doc_id=${String(docId).slice(0, 8)}…`);

    add("② 분류 (POST /classify)…");
    const c1 = await (await fetch(apiUrl("/api/v1/classify"), { method: "POST", headers: jhdr(), body: JSON.stringify({ doc_id: docId, content, return_evidence: false }) })).json();
    finish(`② 최초 분류: <b class="g-${c1.label}">${c1.label}</b> (룰 ${c1.rule_grade || "—"} · 모델 ${c1.model_grade || "—"})`);
    await refreshDashboard();

    const corrected = c1.label === "S1" ? "TS" : "S1";
    add(`③ 검수자 교정 → <b class="g-${corrected}">${corrected}</b> + 승급 (POST /relabel, /promotions/promote)…`);
    await fetch(apiUrl("/api/v1/relabel"), { method: "POST", headers: jhdr(), body: JSON.stringify({ doc_id: docId, inference_id: c1.inference_id, original_label: c1.label, corrected_label: corrected, actor: rev }) });
    await fetch(apiUrl("/api/v1/promotions/promote"), { method: "POST", headers: jhdr(), body: JSON.stringify({ doc_id: docId, actor: rev, expected_label: corrected }) });
    finish(`③ 검수 교정·승급 완료 → <b class="g-${corrected}">${corrected}</b>`);
    await refreshDashboard();

    add("④ 같은 문서 재분류 (POST /classify)…");
    const c2 = await (await fetch(apiUrl("/api/v1/classify"), { method: "POST", headers: jhdr(), body: JSON.stringify({ doc_id: docId, content, return_evidence: false }) })).json();
    const reflected = c2.label === corrected;
    finish(`④ 재분류: <b class="g-${c2.label}">${c2.label}</b> — ${reflected ? `✅ 검수 결과 반영됨 (최초 ${c1.label} → 교정 ${corrected})` : "반영 확인 필요"}`);
    await refreshDashboard();
    busy.textContent = reflected ? "완료 — 교정이 다음 분류에 즉시 반영됨" : "완료";
  } catch (e) {
    busy.textContent = "오류: " + e.message;
  } finally {
    if (btn) btn.disabled = false;
  }
}

function scrollToResult({ flash = false } = {}) {
  const sec = $("#sec-result");
  if (!sec) return;
  // 진행바도 sec-result 안에 있으므로 sec-result 헤더로 스크롤하면
  // 진행바 점등 + 결과 카드 갱신을 한 시야에서 본다.
  sec.scrollIntoView({ behavior: "smooth", block: "start" });
  if (flash) {
    const card = sec.querySelector(".result");
    if (!card) return;
    card.classList.remove("flash-card");
    void card.offsetWidth;
    card.classList.add("flash-card");
    setTimeout(() => card.classList.remove("flash-card"), 1400);
  }
}

let _toastTimer = null;
function showResultToast(grade, elapsedMs) {
  let toast = $("#result-toast");
  if (!toast) {
    toast = document.createElement("div");
    toast.id = "result-toast";
    toast.className = "toast";
    document.body.appendChild(toast);
  }
  toast.className = `toast g-${grade}`;
  toast.innerHTML = `
    <span class="toast-grade">${grade}</span>
    <span>분류 완료 · ${elapsedMs} ms</span>
    <a id="toast-jump">결과 보기 →</a>
  `;
  // forced reflow 후 show 클래스
  void toast.offsetWidth;
  toast.classList.add("show");
  const jump = toast.querySelector("#toast-jump");
  if (jump) {
    jump.addEventListener("click", () => {
      scrollToResult({ flash: true });
    });
  }
  if (_toastTimer) clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => {
    toast.classList.remove("show");
  }, 4200);
}

function renderSummary(data) {
  const grade = data.label;
  const ev = data.evidence || [];
  const matched = ev.slice(0, 3).map((e) => e.text);
  const factors = data.evaluation_factors || {};
  const topFactors = Object.entries(factors)
    .filter(([k]) => k !== "" && typeof factors[k] === "number")
    .sort((a, b) => b[1] - a[1])
    .slice(0, 2);

  const factorLabels = {
    secrecy: "비공지성(S)",
    value: "경제적 유용성(V)",
    management: "비밀관리성(M)",
  };

  // 음성 근거 — 더 상위 등급의 키워드가 안 들어왔다
  const higherGrade = { TS: null, S1: "TS", S2: "S1", S3: "S2" }[grade];
  const higherSamples = {
    TS: "특급기밀·국가핵심기술·M&A 계획",
    S1: "1급 비밀·핵심 모듈 소스·고객 데이터베이스",
    S2: "대외비·내부 자료·분기 매출",
  };
  const negEvidence = higherGrade
    ? `「${higherSamples[higherGrade]}」 같은 ${higherGrade} 키워드가 본문에서 검출되지 않아 ${higherGrade} 이상으로는 분류되지 않았습니다.`
    : "본 등급이 시드 v4 기준 최상위 등급입니다.";

  const matchedTxt = matched.length > 0
    ? `「${matched.join("」 「")}」 키워드가 감지되었습니다.`
    : "본문에서 시드 키워드 매칭이 없어 기본 등급으로 판정되었습니다.";
  // factors_source=model_estimated 는 룰 미탐으로 등급에 맞춰 역산한 추정치(법리 근거 아님).
  const estimated = data.factors_source === "model_estimated";
  const factorTxt = topFactors.length > 0
    ? `3요건(S·V·M) 중 ${topFactors
        .map(([k, v]) => `<b>${factorLabels[k] || k}(${v.toFixed(2)})</b>`)
        .join("·")}가 ${estimated ? "가장 높게 <b>추정</b>되었습니다 (모델 역산 — 법리 근거 아님)" : "가장 높게 측정되었습니다"}.`
    : "";

  // 서버가 계산한 라우팅 status 를 반드시 노출 — needs_review 를 확정처럼 보이지 않게.
  //
  // [2026-08-24] 종전엔 여기서 warnings 를 **원문 그대로** 이어 붙였다. 그 결과
  //   "사유: low-confidence: confidence=0.46 < 0.50 — review recommended ·
  //    persistence skipped: doc_id='S1-기술-SW' is not a UUID"
  // 가 화면에 떴다. 두 가지가 잘못이다 — ① 화면에서 뺐다고 한 신뢰도 수치가 여기로 샜고,
  // ② persistence skipped 처럼 판정과 무관한 내부 사정이 '검수 사유' 자리에 섞였다
  // (비-UUID doc_id 라 저장을 건너뛴 것은 설계대로다).
  // 사유는 review_reason_ko.js 가 옮긴 한 줄만 쓴다. 원문 경고는 아래 실시간 로그로 보낸다.
  const needsReview = data.status === "needs_review";
  const warns = Array.isArray(data.warnings) ? data.warnings : [];
  const whyKo = (typeof window !== "undefined" && window.KOIPA_GATE_REASON)
    ? window.KOIPA_GATE_REASON(warns) : "";
  // 안전 규칙이 모델 최고점과 다른 등급을 채택했으면 그 사실을 같이 적는다 — 「룰·모델 모두
  // TS 로 일치」 인데 검수로 가는 카드가 검수자에게 설명되지 않던 자리(2026-08-24 사용자 지적).
  const adjNote = (typeof window !== "undefined" && window.KOIPA_ADJUSTMENT_NOTE)
    ? window.KOIPA_ADJUSTMENT_NOTE(data) : "";
  const banner = needsReview
    ? `<p class="neg" style="font-weight:700;border:1px solid #e11d2e;border-radius:0;padding:8px 12px;background:rgba(225,29,46,.06);">⚠ 자동 확정 아님 — <b>검수 필요</b>로 라우팅됨<br><span style="font-weight:500;font-size:12px;">사유: ${escapeHtml(whyKo || "자동 확정하지 않고 사람 검수로 라우팅")}</span>${adjNote ? `<br><span style="font-weight:500;font-size:12px;">${escapeHtml(adjNote)}</span>` : ""}</p>`
    : "";
  const verdictTxt = needsReview
    ? `본 문서는 <b>${grade} (${gradeLabel(grade)})</b>로 <b>잠정 분류</b>되었으나 자동 확정되지 않고 검수로 라우팅되었습니다.`
    : `본 문서는 <b>${grade} (${gradeLabel(grade)})</b>로 판정되었습니다.`;

  // [2026-08-24] 화면에서는 신뢰도 수치를 **보여주지 않는다**(사용자 지시).
  //   근거: 화면 표시는 요건이 아니다 - RTM FUN-024 의 구현범위는 "검수자 큐 배분·심층지표"
  //   이고 "신뢰도를 화면에 표시" 라는 요건은 없다. 그리고 이 값은 정답확률이 아니라
  //   softmax 파생값이라(골든500 AUROC 0.58 · ECE 0.18) 화면에 숫자로 서면 실제보다
  //   확정적으로 읽힌다. 자동확정을 가르는 것도 이 값 단독이 아니다.
  //
  //   ⚠ 지운 것은 **화면뿐**이다. confidence 는 API 응답(openapi_koipa_kl.yaml 에서
  //   required) · DB(tb_classifications) · 감사로그 · reports 에 그대로 남는다. 감리에서
  //   "왜 이 판정인가" 를 재구성할 근거는 없어지지 않는다.
  const wrap = $("#result-summary");
  wrap.innerHTML = `
    ${banner}
    <p class="pos">${verdictTxt}</p>
    <p class="pos">${matchedTxt} ${factorTxt}</p>
    <p class="neg">${negEvidence}</p>
  `;
}

function gradeLabel(g) {
  return ({ TS: "특급기밀", S1: "1급 비밀", S2: "2급 대외비", S3: "3급 공개" })[g] || g;
}

function renderFactors(f, factorsSource) {
  const wrap = $("#result-factors");
  const labels = {
    secrecy: "비공지성(S)",
    value: "경제적 유용성(V)",
    management: "비밀관리성(M)",
  };
  // 서버 응답은 키워드 가중치 누적값이라 등급에 따라 0~5+ 범위.
  // 화면 표시는 3 요소(S·V·M) 상대값을 0~1로 정규화해서 비교 가능하게 한다.
  wrap.innerHTML = "";
  // 역산 추정치는 법리 근거가 아님을 구분 표기(번들C 컴플라이언스 계약).
  if (factorsSource === "model_estimated") {
    const note = document.createElement("div");
    note.style.cssText = "grid-column:1/-1;font-size:12px;color:#d97706;margin-bottom:4px;";
    note.textContent = "⚠ 모델 추정치 — 룰이 근거를 못 찾아 등급에 맞춰 역산(법리 근거 아님)";
    wrap.appendChild(note);
  }
  const values = Object.keys(labels).map((k) => (typeof f[k] === "number" ? f[k] : 0));
  const maxV = Math.max(1, ...values);
  Object.entries(labels).forEach(([k, l], i) => {
    const v = values[i];
    const norm = maxV > 0 ? v / maxV : 0;
    const stat = document.createElement("div");
    stat.className = "stat";
    stat.innerHTML = `
      <div class="v">${v.toFixed(2)}</div>
      <div class="l">${l}</div>
      <div style="margin-top:8px;height:4px;background:var(--bg);border-radius:0;overflow:hidden;">
        <div style="width:${(norm * 100).toFixed(0)}%;height:100%;background:var(--text);"></div>
      </div>
    `;
    wrap.appendChild(stat);
  });
}

function renderKeywordChips(evidence) {
  const wrap = $("#result-keywords");
  wrap.innerHTML = "";
  if (!evidence || evidence.length === 0) {
    wrap.innerHTML = '<div style="font-size:13px;color:var(--text-dim)">근거 키워드 없음</div>';
    return;
  }
  evidence.slice(0, 12).forEach((e) => {
    const chip = document.createElement("span");
    chip.className = "kw-chip";
    const w = typeof e.weight === "number" ? e.weight : 0;
    const intensity = Math.min(1, Math.max(0.15, w));
    chip.style.background = `rgba(0,0,0,${intensity * 0.12 + 0.04})`;
    chip.innerHTML = `${escapeHtml(e.text)} <span class="w">${w.toFixed(2)}</span>`;
    chip.addEventListener("click", () => {
      flashKeywordInBody($("#body-preview"), e.text);
    });
    wrap.appendChild(chip);
  });
}

function clearResult() {
  hideParseDetail();
  $("#result-head").innerHTML = "";
  $("#result-summary").innerHTML = "";
  $("#result-factors").innerHTML = "";
  $("#result-keywords").innerHTML = "";
}

function showError(msg) {
  // B3-3 (2026-05-30): 영문 detail 을 한국어로 매핑 (errors_ko.js 50항목).
  const ko = translateError(msg);
  const wrap = $("#result-summary");
  wrap.innerHTML = `
    <div class="callout danger">
      <svg class="callout-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>
      </svg>
      <div class="callout-body">
        <span class="callout-label">오류</span>
        <p>${escapeHtml(ko)}</p>
        ${ko !== msg ? `<p style="font-size:11.5px;color:var(--text-dim);margin-top:6px;font-family:var(--font-mono);">원문: ${escapeHtml(msg)}</p>` : ""}
      </div>
    </div>
  `;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function ensureReady() {
  if (!state.warmupDone) {
    showError("시스템 준비 중입니다 (warmup). 잠시 후 다시 시도해주세요.");
    return false;
  }
  return true;
}

// ──────────────────────────────────────────────────────────────────────
// 법령 카드 (와우 C-2: 키워드 클릭 → 본문 하이라이트)
// ──────────────────────────────────────────────────────────────────────
function renderLegal() {
  const wrap = $("#legal-grid");
  if (!wrap) return;
  wrap.innerHTML = "";
  const grades = [
    { code: "TS", title: "특급기밀" },
    { code: "S1", title: "1급 비밀" },
    { code: "S2", title: "2급 대외비" },
    { code: "S3", title: "3급 공개" },
  ];
  grades.forEach((g) => {
    const data = DEMO_DATA.legal[g.code];
    if (!data) return;
    const card = document.createElement("div");
    card.className = "legal-card";
    card.setAttribute("data-grade", g.code);
    // 시드 v4 에서 해당 등급 키워드 5개 (각 도메인 카드의 toggle_keywords 기반)
    const sampleKeywords = DEMO_DATA.samples
      .filter((s) => s.grade === g.code)
      .flatMap((s) => s.toggle_keywords.slice(0, 2));
    const uniqKw = [...new Set(sampleKeywords)].slice(0, 5);
    card.innerHTML = `
      <div class="legal-card-head">
        <span class="pill grade-${g.code}">${g.code} · ${g.title}</span>
      </div>
      <div class="legal-card-body">
        <p><b>법령 근거.</b> ${escapeHtml(data.law)}</p>
        <p><b>해당 조항.</b> ${escapeHtml(data.clause)}</p>
        <p><b>가이드.</b> ${escapeHtml(data.guide)}</p>
        <p style="margin-top:10px;color:var(--text);">${escapeHtml(data.summary)}</p>
        <div style="margin-top:10px;font-size:12px;color:var(--text-dim)">참고: ${escapeHtml(data.extra)}</div>
      </div>
      <div class="legal-keywords"></div>
    `;
    const kwWrap = card.querySelector(".legal-keywords");
    uniqKw.forEach((kw) => {
      const chip = document.createElement("span");
      chip.className = "kw-chip";
      chip.textContent = kw;
      chip.addEventListener("click", () => {
        if (state.lastResult) {
          flashKeywordInBody($("#body-preview"), kw);
          $("#sec-result").scrollIntoView({ behavior: "smooth", block: "start" });
        }
      });
      kwWrap.appendChild(chip);
    });
    wrap.appendChild(card);
  });
}

// ──────────────────────────────────────────────────────────────────────
// Config row binding
// ──────────────────────────────────────────────────────────────────────
function bindConfig() {
  // config row 는 발주처 시연에서 노출되지 않도록 HTML 에서 생략됨.
  // 개발자가 직접 추가했을 때만 바인딩 (null-safe).
  // tenant 제거: 격리는 KL 포털 전담. cfg-tenant 바인딩 없음.
  const ep = $("#cfg-endpoint");
  const ak = $("#cfg-apikey");
  if (ep) { ep.value = state.endpoint; ep.addEventListener("change", (e) => { state.endpoint = e.target.value || window.location.origin; }); }
  if (ak) {
    // localStorage 저장키가 있으면 그것을, 없으면 HTML에 미리 채운 값(기본 시연키)을 채택.
    if (state.apiKey) {
      ak.value = state.apiKey;
    } else if (ak.value && ak.value.trim()) {
      state.apiKey = ak.value.trim();
      window.localStorage?.setItem("koipa_api_key", state.apiKey);
    }
    const _apply = (v) => {
      state.apiKey = (v || "").trim();
      window.localStorage?.setItem("koipa_api_key", state.apiKey);
      const st = $("#apikey-status");
      if (st) st.textContent = state.apiKey ? "키 설정됨 ✓ — 이 브라우저에만 저장됩니다." : "키를 입력하세요.";
    };
    ak.addEventListener("change", (e) => _apply(e.target.value));
    ak.addEventListener("input", (e) => { state.apiKey = (e.target.value || "").trim(); });
  }
}

// ──────────────────────────────────────────────────────────────────────
// Init
// ──────────────────────────────────────────────────────────────────────
async function init() {
  bindConfig();
  populateSamples();
  renderLegal();
  await pollHealth();
  if (!state.warmupDone) {
    // 1초 간격으로 최대 60초 폴링
    let n = 0;
    const t = setInterval(async () => {
      n += 1;
      await pollHealth();
      if (state.warmupDone || n >= 60) clearInterval(t);
    }, 1000);
  }
  $("#btn-classify").addEventListener("click", runClassify);
  $("#btn-print").addEventListener("click", () => window.print());
  // 운영 대시보드 + 실시간 반영 시연
  const btnReflect = $("#btn-reflect");
  if (btnReflect) btnReflect.addEventListener("click", runReflectDemo);
  const btnDash = $("#btn-dash-refresh");
  if (btnDash) btnDash.addEventListener("click", refreshDashboard);
  if ($("#ops-tiles")) { refreshDashboard(); setInterval(refreshDashboard, 5000); }
  // 파일 직접 업로드 (드래그드롭 / 클릭) → 즉시 백엔드 파싱+이중판정
  const dropZone = $("#doc-drop");
  const fileInput = $("#doc-file");
  function handleFile(f) {
    if (!f) return;
    $("#doc-file-name").textContent = `${f.name} (${f.size.toLocaleString()} bytes)`;
    analyzeFile(f);
  }
  if (dropZone && fileInput) {
    dropZone.addEventListener("click", () => fileInput.click());
    dropZone.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); } });
    fileInput.addEventListener("change", (e) => handleFile(e.target.files && e.target.files[0]));
    ["dragover", "dragenter"].forEach((ev) => dropZone.addEventListener(ev, (e) => { e.preventDefault(); dropZone.classList.add("over"); }));
    ["dragleave", "dragend"].forEach((ev) => dropZone.addEventListener(ev, (e) => { e.preventDefault(); dropZone.classList.remove("over"); }));
    dropZone.addEventListener("drop", (e) => {
      e.preventDefault();
      dropZone.classList.remove("over");
      handleFile(e.dataTransfer.files && e.dataTransfer.files[0]);
    });
  }
  // 참고 샘플 6종 — 인라인 onclick 대신 data 속성으로 바인딩(모듈이라 전역이 없다)
  $$("[data-doc]").forEach((btn) => {
    btn.addEventListener("click", () => loadFileSample(btn.dataset.doc, btn.dataset.name || btn.dataset.doc));
  });
  $("#btn-reset").addEventListener("click", () => {
    $("#doc-title").value = "";
    $("#doc-body").value = "";
    $("#toggle-row").innerHTML = "";
    clearResult();
    state.currentSample = null;
    state.currentSampleId = null;
    state.lastResult = null;
    state.toggledOff.clear();
    $("#sample-select").value = "";
  });

  // 첫 샘플 자동 로드 (TS-반도체)
  if (DEMO_DATA.samples.length > 0) {
    $("#sample-select").value = DEMO_DATA.samples[0].id;
    loadSample(DEMO_DATA.samples[0].id);
  }
}

document.addEventListener("DOMContentLoaded", init);
