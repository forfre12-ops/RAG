// 한국어 에러 매핑 — 데모 콘솔이 422/4xx/5xx 영문 detail 을 한국어로 표시.
// Phase 후속 B3-3 (2026-05-30): 25 → 50 항목 확장.
// 시연 시 발주처에 영문 에러가 그대로 노출되는 사고 차단.

export const ERROR_KO = {
  // ---- 422 Pydantic validation ----
  "field required": "필수 필드가 누락되었습니다.",
  "value is not a valid uuid": "올바른 UUID 형식이 아닙니다.",
  "value is not a valid integer": "정수 형식이어야 합니다.",
  "value is not a valid float": "숫자 형식이어야 합니다.",
  "value is not a valid string": "문자열 형식이어야 합니다.",
  "value is not a valid boolean": "참/거짓 형식이어야 합니다.",
  "string too short": "문자열이 너무 짧습니다.",
  "string too long": "문자열이 너무 깁니다.",
  "string_too_short": "문자열이 너무 짧습니다.",
  "string_too_long": "문자열이 너무 깁니다.",
  "ensure this value has at least": "값이 최소 길이/개수에 못 미칩니다.",
  "ensure this value has at most": "값이 최대 길이/개수를 초과합니다.",
  "less_than_equal": "최대 허용값을 초과했습니다.",
  "greater_than_equal": "최소 허용값에 미달합니다.",
  "input_value_error": "입력값이 유효하지 않습니다.",
  "missing": "필수 필드 누락",
  "extra_forbidden": "허용되지 않는 추가 필드입니다.",
  "string_pattern_mismatch": "허용된 패턴과 일치하지 않습니다.",
  "enum": "허용된 값 중 하나여야 합니다.",
  "json_invalid": "올바른 JSON 형식이 아닙니다.",
  "json_type": "JSON 타입이 올바르지 않습니다.",

  // ---- 인증·인가 ----
  "invalid api key": "API 키가 유효하지 않습니다.",
  "Missing X-API-Key": "X-API-Key 헤더가 누락되었습니다.",
  "Could not validate credentials": "자격증명을 확인할 수 없습니다.",
  "Not authenticated": "인증되지 않았습니다.",
  "Forbidden": "권한이 없습니다.",

  // ---- 비즈니스 ----
  "documents must not be empty": "documents 배열이 비어 있습니다.",
  "batch size > 1000": "배치 크기가 1000을 초과합니다.",
  "doc_id must be a UUID": "doc_id 는 UUID 형식이어야 합니다.",
  "no classification for doc_id": "해당 doc_id 의 분류 이력이 없습니다.",
  "job not found": "잡을 찾을 수 없습니다.",
  "train job not found": "학습 잡을 찾을 수 없습니다.",
  "synth_id not found": "합성 ID 를 찾을 수 없습니다.",
  "guide_id not found": "가이드 ID 를 찾을 수 없습니다.",
  "no active model": "활성 모델이 없습니다.",
  "model_version not found": "해당 모델 버전을 찾을 수 없습니다.",
  "service temporarily unavailable": "서비스가 일시적으로 사용 불가합니다.",

  // ---- 업로드·크기 ----
  "file too large": "파일 크기가 한도를 초과합니다.",
  "invalid actor json": "actor JSON 형식이 잘못되었습니다.",

  // ---- Rate-limit ----
  "rate limit exceeded": "호출 한도를 초과했습니다.",

  // ---- 내부 오류 ----
  "internal server error": "서버 내부 오류가 발생했습니다.",
  "LLOYDK_INTERNAL": "서버 내부 오류 (요청 ID 를 확인하세요).",
  "LLOYDK_RATE_LIMIT": "요청 한도 초과 (Retry-After 헤더 참조).",
};

/**
 * 영문 에러 메시지를 한국어로 매핑. 정확 일치 → 부분 일치 → 원문 폴백.
 */
export function translateError(msg) {
  if (!msg) return "(에러 메시지 없음)";
  const m = String(msg).trim();
  if (ERROR_KO[m]) return ERROR_KO[m];
  // 부분 일치 (영문 키워드가 포함된 경우)
  for (const key of Object.keys(ERROR_KO)) {
    if (m.toLowerCase().includes(key.toLowerCase())) return ERROR_KO[key];
  }
  return m;  // 원문 폴백
}
