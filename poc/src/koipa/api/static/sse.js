// POST + SSE 수동 파서 (EventSource 는 GET만 지원하므로 fetch + ReadableStream).
// /classify/stream 의 event:progress / event:partial / event:result / event:done
// 형식을 라인 단위로 파싱.

export async function postSSE(url, { headers = {}, body, onEvent, signal } = {}) {
  const resp = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...headers },
    body: JSON.stringify(body),
    signal,
  });
  if (!resp.ok || !resp.body) {
    throw new Error(`SSE HTTP ${resp.status}`);
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    // SSE 는 빈 줄(\n\n) 로 메시지 구분
    let idx;
    while ((idx = buffer.indexOf("\n\n")) !== -1) {
      const raw = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      const ev = parseEvent(raw);
      if (ev && onEvent) onEvent(ev);
    }
  }
  if (buffer.trim()) {
    const ev = parseEvent(buffer);
    if (ev && onEvent) onEvent(ev);
  }
}

function parseEvent(raw) {
  const lines = raw.split("\n");
  let event = "message";
  const dataLines = [];
  for (const line of lines) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  if (dataLines.length === 0) return null;
  const dataStr = dataLines.join("\n");
  let data;
  try {
    data = JSON.parse(dataStr);
  } catch {
    data = dataStr;
  }
  return { event, data };
}
