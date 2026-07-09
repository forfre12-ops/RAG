// KOIPA AI 데모 콘솔 — 라우터 호출 + 렌더 + 인터랙션 전체.
// V2 디자인 시스템 위에서 동작. 외부 의존 0.

import { DEMO_DATA } from "./samples.js";
import { INCIDENT } from "./incident.js";
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
  apiKey: window.localStorage?.getItem("lloydk_api_key") || "",
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
    window.localStorage?.setItem("lloydk_api_key", state.apiKey);
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
  const el = $("#nav-status");
  if (!el) return;
  const h = state.health || {};
  const profile = h.deploy_profile || "unknown";
  const provider = h.llm_provider || "—";
  const embedder = h.embedding_provider || "—";
  const cls = state.warmupDone ? "" : "warming";
  el.className = `nav-status ${cls}`;
  el.innerHTML = "";
  const dot = document.createElement("span");
  dot.className = "dot";
  el.appendChild(dot);
  const txt = document.createElement("span");
  txt.textContent = `${profile} · LLM ${provider} · emb ${embedder}`;
  el.appendChild(txt);
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
  $("#btn-race").disabled = true;
  clearResult();
  const stageMap = {};
  STAGES.forEach((s) => { stageMap[s.key] = "pending"; });
  renderStages(stageMap);

  // 결과 영역으로 자동 스크롤 — §2 가 한참 아래라 사용자가 못 보는 사고 방지.
  scrollToResult();

  const t0 = performance.now();
  let lastStageT = t0;

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
          }
        } else if (event === "partial") {
          // 임시 등급
          if (data && data.grade) {
            showPartial(data.grade, data.confidence);
          }
        } else if (event === "result") {
          STAGES.forEach((s) => { stageMap[s.key] = "done"; });
          renderStages(stageMap);
          state.lastResult = data;
          renderResult(data, Math.round(performance.now() - t0));
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
    $("#btn-race").disabled = false;
  }
}

function showPartial(grade, conf) {
  const head = $("#result-head");
  if (!head) return;
  head.innerHTML = `
    <span class="result-grade g-${grade}">${grade}</span>
    <div class="result-confidence">
      <div style="font-size:12px;color:var(--text-dim)">임시 신뢰도 ${(conf * 100).toFixed(0)}%</div>
      <div class="confidence-bar"><div style="width:${conf * 100}%"></div></div>
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
  const conf = data.confidence || 0;
  const elapsed = elapsedMs || data.elapsed_ms || 0;

  head.innerHTML = `
    <span class="result-grade g-${grade}" data-grade="${grade}" role="button" title="법령 근거 보기">${grade}</span>
    <div class="result-confidence">
      <div style="font-size:13px;color:var(--text-dim)">신뢰도 <b style="color:var(--text)">${(conf * 100).toFixed(0)}%</b></div>
      <div class="confidence-bar"><div style="width:${conf * 100}%"></div></div>
    </div>
    <div class="result-time">응답 <b>${elapsed} ms</b><br/>model_version ${data.model_version || "poc"}</div>
  `;

  // 등급 배지 클릭 → 법령 카드로 (와우 C-1)
  const badge = head.querySelector(".result-grade");
  if (badge) {
    badge.style.cursor = "pointer";
    badge.addEventListener("click", () => focusLegalCard(grade));
  }

  // 자연어 요약 3단 (양성·음성 근거)
  renderSummary(data);
  // 4 평가요소 stats
  renderFactors(data.evaluation_factors || {});
  // 키워드 칩 (weight 진하기)
  renderKeywordChips(data.evidence || []);
  // 본문 하이라이트
  renderBodyPreview($("#doc-body").value);
  // 결과 도착 시점에 한 번 더 스크롤 + 강조 펄스 + 토스트 (사용자 시선 확보).
  scrollToResult({ flash: true });
  showResultToast(grade, elapsed);
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
  const conf = data.confidence || 0;
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
  const factorTxt = topFactors.length > 0
    ? `3요건(S·V·M) 중 ${topFactors
        .map(([k, v]) => `<b>${factorLabels[k] || k}(${v.toFixed(2)})</b>`)
        .join("·")}가 가장 높게 측정되었습니다.`
    : "";

  const wrap = $("#result-summary");
  wrap.innerHTML = `
    <p class="pos">본 문서는 <b>${grade} (${gradeLabel(grade)})</b>로 판정되었습니다. (신뢰도 ${(conf * 100).toFixed(0)}%)</p>
    <p class="pos">${matchedTxt} ${factorTxt}</p>
    <p class="neg">${negEvidence}</p>
  `;
}

function gradeLabel(g) {
  return ({ TS: "특급기밀", S1: "1급 비밀", S2: "2급 대외비", S3: "3급 공개" })[g] || g;
}

function renderFactors(f) {
  const wrap = $("#result-factors");
  const labels = {
    secrecy: "비공지성(S)",
    value: "경제적 유용성(V)",
    management: "비밀관리성(M)",
  };
  // 서버 응답은 키워드 가중치 누적값이라 등급에 따라 0~5+ 범위.
  // 화면 표시는 4 요소 중 상대값을 0~1로 정규화해서 비교 가능하게 한다.
  wrap.innerHTML = "";
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
      <div style="margin-top:8px;height:4px;background:var(--bg);border-radius:999px;overflow:hidden;">
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
// 와우 B — BERT vs LLM 시간 경쟁
// ──────────────────────────────────────────────────────────────────────
async function runRace() {
  if (!ensureReady()) return;
  const body = $("#doc-body").value;
  if (!body || body.length < 5) {
    showError("문서 본문이 너무 짧습니다.");
    return;
  }
  $("#btn-classify").disabled = true;
  $("#btn-race").disabled = true;

  const bertFill = $(".race-fill.bert");
  const llmFill = $(".race-fill.llm");
  const bertTime = $("#race-bert-time");
  const llmTime = $("#race-llm-time");
  if (bertFill) { bertFill.style.width = "0%"; bertTime.textContent = "—"; }
  if (llmFill) { llmFill.style.width = "0%"; llmTime.textContent = "—"; }

  // BERT 실호출
  const t0 = performance.now();
  const r = await apiPost("/api/v1/classify", {
    doc_id: state.currentSampleId || "demo-input",
    title: $("#doc-title").value || "demo",
    content: body,
    use_rag: false,
    return_evidence: true,
  });
  const bertMs = Math.round(performance.now() - t0);
  if (bertFill) {
    // bert 시간을 4800ms 대비 비율로 채움 (LLM 4.8s 기준)
    const ratio = Math.min(1, bertMs / 4800);
    bertFill.style.width = (ratio * 100).toFixed(1) + "%";
    bertTime.textContent = bertMs + " ms";
  }
  if (r.ok) {
    state.lastResult = r.data;
    renderResult(r.data, bertMs);
  }

  // LLM — V2 §4.4 표 참고값으로 진행 막대 시뮬레이션.
  // 실 호출이 아니라 "비교 기준값" 시각화. lite-noapi 환경에서는 LLM 자체가
  // 호출되지 않으므로 실측 불가. 운영 시 LLM_PROVIDER=anthropic 등으로 실 호출하면
  // 별도 실측 라운드 필요(현재 코드는 표시 안 함).
  // Phase 1 실측 (2026-05-30) — Ollama Qwen3 14B Q4 /answer 호출 latency 25.8s.
  // 이전 V2 §4.4 "1.5~5s" 참고값(4,800ms)에서 실측치로 교체.
  const estimatedLlmMs = 25800;
  const llmIsReal = state.health && state.health.llm_provider &&
    !["noop", "", "hash"].includes(state.health.llm_provider);
  const startLlm = performance.now();
  const interval = 50;
  await new Promise((resolve) => {
    const tick = setInterval(() => {
      const elapsed = performance.now() - startLlm;
      const ratio = Math.min(1, elapsed / estimatedLlmMs);
      if (llmFill) llmFill.style.width = (ratio * 100).toFixed(1) + "%";
      if (llmTime) llmTime.textContent = Math.round(elapsed) + " ms (실측 25.8s)";
      if (ratio >= 1) {
        clearInterval(tick);
        if (llmTime) llmTime.textContent = estimatedLlmMs + " ms (Phase 1 실측)";
        resolve();
      }
    }, interval);
  });

  // 결론 — Phase 1 실측치(25.8s) vs Phase 3 학습 모델 실측치(1.18s) 비교.
  const note = $("#race-note");
  if (note) {
    const ratio = (estimatedLlmMs / Math.max(1, bertMs)).toFixed(1);
    // Phase 4 (2026-05-30): /classify/stream SSE 단계 분해로 BERT 추론 비중 99%
    // 입증. Qwen3 25.8s (Phase 1 /answer 실측) vs BERT 1.18s (Phase 3 5070 Ti
    // 학습 모델 실측) = 21.9× 빠름. V2 §4.4 표 정량 검증 완료.
    note.innerHTML = `
      BERT <b>${bertMs} ms</b> <span class="src-tag src-measured">실측</span>
      vs LLM <b>${estimatedLlmMs} ms</b>
      <span class="src-tag src-measured" style="margin-left:6px;">실측</span>
      — 약 <b>${ratio}× 빠름</b>.
      <br/><span style="font-size:11.5px;color:var(--text-dim);">
      ※ BERT 1.18s = Phase 3 5070 Ti KF-DeBERTa 학습 모델 실측.
      Qwen3 25.8s = Phase 1 /answer 실측 (Ollama qwen3:14b).
      P5 E2E RAG ON: 9.2s 평균, BERT 추론이 99% (V2 §14.2 ≤ 30s 합격선 3.3배 여유).
      </span>`;
  }

  $("#btn-classify").disabled = false;
  $("#btn-race").disabled = false;
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
// 사고 시뮬레이션 (와우 D)
// ──────────────────────────────────────────────────────────────────────
function renderIncident() {
  const fnrWrap = $("#fnr-sequence");
  const prevWrap = $("#prevention-sequence");
  if (fnrWrap) {
    fnrWrap.innerHTML = "";
    INCIDENT.fnr_sequence.forEach((s, i) => {
      const step = document.createElement("div");
      step.className = "seq-step danger";
      step.innerHTML = `
        <div class="seq-num">${s.step}</div>
        <div class="seq-body">
          <div class="seq-title">${escapeHtml(s.title)}</div>
          <div class="seq-detail">${escapeHtml(s.detail)}</div>
        </div>
        <div class="seq-sla">${escapeHtml(s.tag)}</div>
      `;
      fnrWrap.appendChild(step);
      if (i < INCIDENT.fnr_sequence.length - 1) {
        const a = document.createElement("div");
        a.className = "seq-arrow";
        fnrWrap.appendChild(a);
      }
    });
  }
  if (prevWrap) {
    prevWrap.innerHTML = "";
    INCIDENT.prevention_sequence.forEach((s, i) => {
      const step = document.createElement("div");
      step.className = "seq-step done";
      step.innerHTML = `
        <div class="seq-num">${s.step}</div>
        <div class="seq-body">
          <div class="seq-title">${escapeHtml(s.title)}</div>
          <div class="seq-detail">${escapeHtml(s.detail)}</div>
        </div>
        <div class="seq-sla">${escapeHtml(s.tag)}</div>
      `;
      prevWrap.appendChild(step);
      if (i < INCIDENT.prevention_sequence.length - 1) {
        const a = document.createElement("div");
        a.className = "seq-arrow";
        prevWrap.appendChild(a);
      }
    });
  }
  const stWrap = $("#incident-stats");
  if (stWrap) {
    stWrap.innerHTML = "";
    INCIDENT.estimates.forEach((e) => {
      const s = document.createElement("div");
      s.className = "stat";
      const flagClass = {
        "예시": "src-ref",
        "목표": "src-spec",
        "법령": "src-spec",
        "실측": "src-measured",
      }[e.flag] || "src-ref";
      const flag = e.flag
        ? `<span class="src-tag ${flagClass}">${escapeHtml(e.flag)}</span>`
        : "";
      s.innerHTML = `
        <div class="v">${escapeHtml(e.value)}</div>
        <div class="l">${escapeHtml(e.label)}</div>
        <div style="margin-top:6px;">${flag}</div>
        <div style="margin-top:6px;font-size:11px;color:var(--text-dim);">${escapeHtml(e.note)}</div>
      `;
      s.title = e.note;
      stWrap.appendChild(s);
    });
  }
}

// ──────────────────────────────────────────────────────────────────────
// 시스템 능력 stats (§5 — 와우 E)
// ──────────────────────────────────────────────────────────────────────
function renderCapabilityStats() {
  const wrap = $("#capability-stats");
  if (!wrap) return;
  // src: "measured" 실측 / "spec" 명세·합격선·코드 사실 / "ref" V2 통합본 참고값.
  const items = [
    // Phase 3 실측 (2026-05-30): KF-DeBERTa labeled_5k 1ep 학습 + RTX 5070 Ti
    { v: "1.18s", l: "BERT 추론 (KF-DeBERTa 학습 모델, 5070 Ti 실측)", src: "measured" },
    { v: "25.8s", l: "LLM zero-shot (Ollama Qwen3 14B, Phase 1 /answer 실측)", src: "measured" },
    { v: "33s", l: "BERT 학습 1 epoch (5070 Ti labeled 3500건, 실측)", src: "measured" },
    { v: "F1=1.0", l: "labeled_5k test 752건 평가 (실측, 학습 분포 한정)", src: "measured" },
    { v: "10/12", l: "데모 12 샘플 실호출 분류 정합 (학습 외 분포, 실측)", src: "measured" },
    { v: "9.2s", l: "P5 E2E RAG ON (5070 Ti 풀스택 실측, V2 §14.2 ≤30s)", src: "measured" },
    // Phase 5 실측 (2026-05-30 02:35): Qwen3 vs Solar 각 200건 합성 비교
    { v: "100%", l: "P3 Qwen3 라벨 일치도 (200건, V2 §14.2 ≥90% PASS)", src: "measured" },
    { v: "81%", l: "P3 Solar 라벨 일치도 (200건, FNR 27% JSON 76.5% 실패 — Qwen3 채택)", src: "measured" },
    // Phase 8 실측 (2026-05-30 야간 후속): 고도화 측정 일괄
    { v: "5,200", l: "ES docs 영구 인덱싱 (BGE-M3, /answer citations 0→3 실측)", src: "measured" },
    { v: "0.746", l: "Qwen3+5K 학습 모델 평균 confidence (Phase 3 0.632 → +0.114)", src: "measured" },
    { v: "F1=1.0", l: "KoBigBird-large 비교 학습 65초 (5070 Ti, KF-DeBERTa 동등)", src: "measured" },
    { v: "39%", l: "Qwen3 thinking OFF 속도 우위 (29 vs 39 tps, 구조화 출력 권장)", src: "measured" },
    { v: "0.6667", l: "P2 arctic-embed-l-ko (4-way 확장, BGE-M3 1순위 재확인)", src: "measured" },
    { v: "≤ 5%", l: "FNR 핵심 KPI 목표 (V2 §9, 합성 한계로 미달)", src: "spec" },
    { v: "480+", l: "시드 v4 키워드 (seeds.py 카운트)", src: "measured" },
    { v: "5,000", l: "합성 코퍼스 (datasets/synthetic_5k)", src: "measured" },
    // Phase 2 실측 (2026-05-30): BGE-M3 / KURE / dragonkue 3-way
    { v: "0.7222", l: "Recall@5 BGE-M3 dense ES (P2 3-way 실측, 합성 천장)", src: "measured" },
    { v: "111ms", l: "검색 latency p50 (BGE-M3 dense ES, 실측)", src: "measured" },
    { v: "540+", l: "단위 테스트 PASS (회귀 기준)", src: "measured" },
    { v: "4", l: "배포 프로파일 (lite-noapi 외 3)", src: "measured" },
    { v: "≤ 30s", l: "E2E 합격선 (V2 §14.2 목표)", src: "spec" },
    { v: "$0", l: "온프레미스 추론 비용 (자체 GPU 가정)", src: "spec" },
  ];
  wrap.innerHTML = "";
  items.forEach((it) => {
    const s = document.createElement("div");
    s.className = "stat";
    const tag = it.src === "measured" ? `<span class="src-tag src-measured">실측</span>`
      : it.src === "spec" ? `<span class="src-tag src-spec">명세</span>`
      : `<span class="src-tag src-ref">참고</span>`;
    s.innerHTML = `
      <div class="v">${it.v}</div>
      <div class="l">${it.l}</div>
      <div style="margin-top:6px;">${tag}</div>
    `;
    wrap.appendChild(s);
  });
}

// ──────────────────────────────────────────────────────────────────────
// 배포 프로파일 카드 (§6)
// ──────────────────────────────────────────────────────────────────────
function renderProfiles() {
  const wrap = $("#profile-grid");
  if (!wrap) return;
  const profiles = [
    {
      name: "lite-noapi", title: "외부 의존 0",
      detail: "GPU·LLM API·DB 없이 즉시 시연. 본 데모가 이 프로파일.",
      cmd: "DEPLOY_PROFILE=lite-noapi",
      current: true,
    },
    {
      name: "lite-cloud", title: "LLM API만 활성",
      detail: "Anthropic/OpenAI 상용 키 추가. RAG·답안 합성 정상.",
      cmd: "DEPLOY_PROFILE=lite-cloud",
    },
    {
      name: "onprem-local", title: "온프레미스 GPU + vLLM",
      detail: "Qwen3-14B 로컬 추론. 폐쇄망 운영 표준.",
      cmd: "DEPLOY_PROFILE=onprem-local",
    },
    {
      name: "full-train", title: "학습 라우터 활성",
      detail: "/train·/train/jobs 활성, FUN-004 풀 사이클.",
      cmd: "DEPLOY_PROFILE=full-train",
    },
  ];
  wrap.innerHTML = "";
  profiles.forEach((p) => {
    const c = document.createElement("div");
    c.className = "card" + (p.current ? " risk" : "");
    c.innerHTML = `
      <div class="eyebrow">${p.current ? "현재 데모" : "선택 가능"}</div>
      <div class="ctitle">${p.name}</div>
      <p>${p.title}</p>
      <p style="margin-top:8px;font-family:var(--font-mono);font-size:12px;color:var(--text-dim);"><code>${p.cmd}</code></p>
      <p style="margin-top:8px;font-size:13px;">${p.detail}</p>
    `;
    wrap.appendChild(c);
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
  if (ak) { ak.value = state.apiKey; ak.addEventListener("change", (e) => { state.apiKey = e.target.value; window.localStorage?.setItem("lloydk_api_key", state.apiKey); }); }
}

// ──────────────────────────────────────────────────────────────────────
// Init
// ──────────────────────────────────────────────────────────────────────
async function init() {
  bindConfig();
  populateSamples();
  renderLegal();
  renderIncident();
  renderCapabilityStats();
  renderProfiles();
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
  $("#btn-race").addEventListener("click", runRace);
  $("#btn-print").addEventListener("click", () => window.print());
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
