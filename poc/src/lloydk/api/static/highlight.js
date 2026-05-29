// 와우 C — 본문 ↔ 결과 키워드 ↔ 법령 카드 양방향 하이라이트.
// XSS 차단을 위해 textContent + DOM 노드 조립만 사용 (innerHTML 미사용).

const escRegex = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/**
 * 본문 텍스트에 키워드들을 <mark data-kw="..."> 로 감싼 DOM 을 빌드.
 * - container: 비워지고 새 노드들로 채워짐
 * - text: 원본 본문 문자열
 * - keywords: [{ keyword, weight, factor }] — weight 내림차순 매칭
 */
export function renderBodyWithHighlights(container, text, keywords) {
  while (container.firstChild) container.removeChild(container.firstChild);
  if (!text) return;
  if (!keywords || keywords.length === 0) {
    container.appendChild(document.createTextNode(text));
    return;
  }
  // 긴 키워드부터 매칭하여 부분 문자열 충돌 방지
  const sorted = [...keywords].sort((a, b) => b.keyword.length - a.keyword.length);
  const pattern = sorted.map((k) => escRegex(k.keyword)).join("|");
  const re = new RegExp(`(${pattern})`, "g");
  let lastIdx = 0;
  let m;
  while ((m = re.exec(text)) !== null) {
    if (m.index > lastIdx) {
      container.appendChild(document.createTextNode(text.slice(lastIdx, m.index)));
    }
    const mark = document.createElement("mark");
    mark.textContent = m[0];
    mark.setAttribute("data-kw", m[0]);
    const meta = sorted.find((k) => k.keyword === m[0]);
    if (meta) {
      if (meta.factor) mark.setAttribute("data-factor", meta.factor);
      if (meta.weight != null) mark.setAttribute("data-weight", String(meta.weight));
    }
    container.appendChild(mark);
    lastIdx = m.index + m[0].length;
  }
  if (lastIdx < text.length) {
    container.appendChild(document.createTextNode(text.slice(lastIdx)));
  }
}

/** 칩 클릭 → 본문의 같은 키워드를 노랗게 깜빡임. */
export function flashKeywordInBody(bodyEl, keyword) {
  if (!bodyEl || !keyword) return;
  const marks = bodyEl.querySelectorAll(`mark[data-kw="${cssEscape(keyword)}"]`);
  marks.forEach((m) => {
    m.classList.remove("flash");
    // forced reflow 로 애니메이션 재시작
    void m.offsetWidth;
    m.classList.add("flash");
  });
  if (marks.length > 0) {
    marks[0].scrollIntoView({ behavior: "smooth", block: "center" });
  }
}

/** 등급 배지 클릭 → 해당 법령 카드로 스크롤 + 강조. */
export function focusLegalCard(grade) {
  document.querySelectorAll(".legal-card").forEach((c) => c.classList.remove("highlight"));
  const card = document.querySelector(`.legal-card[data-grade="${cssEscape(grade)}"]`);
  if (!card) return;
  card.classList.add("highlight");
  card.scrollIntoView({ behavior: "smooth", block: "center" });
  setTimeout(() => card.classList.remove("highlight"), 2400);
}

function cssEscape(s) {
  if (window.CSS && window.CSS.escape) return CSS.escape(s);
  return String(s).replace(/["\\]/g, "\\$&");
}
