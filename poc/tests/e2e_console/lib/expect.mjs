/* 시나리오 안에서 쓰는 확인 도구.
 *
 * 실패해도 그 자리에서 던지지 않고 모아 둔다 — 한 번 돌려 "무엇 무엇이 안 되는가"를
 * 한꺼번에 보기 위해서다. 하니스 자체가 터진 경우(요소 없음 등)만 예외로 중단된다.
 */
export function makeCheck({ live = false } = {}) {
  const failures = [];
  const passed = [];
  const dataSkipped = [];

  const record = (ok, label, detail) => {
    if (ok) passed.push(label);
    else failures.push({ label, detail: detail === undefined ? '' : String(detail).slice(0, 600) });
    return ok;
  };

  const check = {
    ok: (cond, label, detail) => record(!!cond, label, detail),
    eq: (got, want, label) => record(got === want, label, `기대 ${JSON.stringify(want)} · 실제 ${JSON.stringify(got)}`),
    ne: (got, bad, label) => record(got !== bad, label, `이 값이면 안 된다: ${JSON.stringify(bad)}`),
    includes: (hay, needle, label) =>
      record(String(hay).includes(needle), label, `"${needle}" 이 없다 · 실제=${String(hay).slice(0, 300)}`),
    excludes: (hay, needle, label) =>
      record(!String(hay).includes(needle), label, `"${needle}" 이 있으면 안 된다 · 실제=${String(hay).slice(0, 300)}`),
    matches: (hay, re, label) =>
      record(re.test(String(hay)), label, `${re} 에 안 맞는다 · 실제=${String(hay).slice(0, 300)}`),
    gte: (got, min, label) => record(Number(got) >= min, label, `${got} < ${min}`),
    failures,
    passed,
    dataSkipped,
  };

  /* check.data.* — **본보기 데이터에 기대는 확인**.
   * mock 모드에서는 보통 확인과 똑같이 동작한다. 실서버 모드에서는 건너뛴다 —
   * 실서버 DB 에 무엇이 들어 있는지는 시험이 정할 수 없기 때문이다(예: "검수 대기 3건").
   * 배선·동작을 보는 확인은 여기 넣지 말 것. 그것은 실서버에서도 참이어야 한다. */
  const noop = (label) => { dataSkipped.push(label); return true; };
  check.data = live
    ? {
        ok: (_c, label) => noop(label),
        eq: (_g, _w, label) => noop(label),
        ne: (_g, _b, label) => noop(label),
        includes: (_h, _n, label) => noop(label),
        excludes: (_h, _n, label) => noop(label),
        matches: (_h, _r, label) => noop(label),
        gte: (_g, _m, label) => noop(label),
      }
    : {
        ok: check.ok, eq: check.eq, ne: check.ne,
        includes: check.includes, excludes: check.excludes,
        matches: check.matches, gte: check.gte,
      };

  return check;
}

/** 스크립트 실행 오류가 하나라도 있으면 그 화면은 실패다. */
export function assertNoScriptErrors(check, page, label = '스크립트 오류 0') {
  const msgs = page.errors.map((e) => `[${e.where}] ${e.message}`);
  return check.ok(msgs.length === 0, label, msgs.join(' || '));
}
