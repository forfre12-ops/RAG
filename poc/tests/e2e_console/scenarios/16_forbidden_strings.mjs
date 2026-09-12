/* 16. 실행된 화면에 구현 정보가 새어 나오지 않는가 — 금지 문자열(런타임).
 *
 * 왜(2026-08-24, 같은 사고 두 번). tests/test_console_forbidden_strings.py 를 만들어 잠갔는데,
 * 그 시험은 **정적 마크업만** 읽는다(주석·style·script 를 걷어낸 뒤 남은 텍스트). 그래서
 * JS 가 실행될 때 만드는 문구를 못 본다. 실제로 배포한 **뒤에** 두 차례 더 나왔다:
 *   1차  헬스체크·정적 자산·Prometheus 스크랩 / 학습행 / 비활성(소프트 삭제) / 하드 삭제
 *   2차  (예상) insufficient_per_grade · locked_gold_eval · artifacts/… 같은 서버 응답 문구
 *
 * 이 하니스는 화면을 **실제로 렌더**하므로 그 자리를 볼 수 있다. 정적 시험과 같은 판정 기준을
 * 쓴다 — 접힌 <details> 와 [data-tech] 안은 허용(기술 담당자가 펼쳐서 본다), 기본 화면만 본다.
 */

import { openPage } from '../lib/page.mjs';
import { assertNoScriptErrors } from '../lib/expect.mjs';

/** 기본 화면에 나오면 안 되는 것 — (문자열, 왜) */
const FORBIDDEN = [
  ['demo-secret-key', '실제 키와 달라 사용자를 401 로 유도한다'],
  ['insufficient_per_grade', '서버 내부 사유 코드'],
  ['locked_gold_eval', 'DB/코드 식별자'],
  ['label_source', 'DB 컬럼명'],
  ['build_training_rows', '함수명'],
  ['noop_fallback', '내부 상태값'],
  ['version_label', '코드 변수명'],
  ['training_type', '코드 변수명'],
  ['base_model', '코드 변수명'],
  ['use_rag', '코드 변수명'],
  ['artifacts/', '서버 파일 경로'],
  ['.jsonl', '서버 파일 경로'],
  ['scripts/', '서버 파일 경로'],
  ['모델 공장', '우리가 만든 비유'],
  ['학습행', '우리가 만든 말'],
  ['소프트 삭제', '우리가 만든 말'],
  ['하드 삭제', '우리가 만든 말'],
  ['헬스체크', '우리가 만든 말'],
  ['메트릭 스크랩', '우리가 만든 말'],
];

/** 접힌 곳·기술 상세를 제거한 뒤 남은 **기본 화면 글자**.
 *
 * ⚠ script·style 을 반드시 뺀다. body.textContent 는 그 안의 글자까지 포함하는데,
 *   인라인 스크립트에는 코드 주석이 들어 있다 — 우리가 "왜 이 말을 뺐는지" 적어 둔 주석에
 *   그 말이 그대로 들어 있어서, 걷지 않으면 **주석을 노출로 오인**한다(첫 실행에서 그렇게 됐다).
 * ⚠ 화면에서 감춘 것(pf-hidden·display:none)도 뺀다 — 프로파일·탭으로 숨긴 카드는 지금
 *   화면에 없는 글자다.
 */
function visibleText(page) {
  const doc = page.win.document;
  const clone = doc.body.cloneNode(true);
  clone.querySelectorAll('script, style, details, [data-tech]').forEach((el) => el.remove());
  // 숨겨진 요소 제거 — 원본에서 계산해 같은 위치의 복제 노드를 지운다.
  const originals = Array.from(doc.body.querySelectorAll('*'));
  const clones = Array.from(clone.querySelectorAll('*'));
  originals.forEach((el, i) => {
    const c = clones[i];
    if (!c || !c.parentNode) return;
    const st = page.win.getComputedStyle(el);
    if (st && (st.display === 'none' || st.visibility === 'hidden')) c.remove();
  });
  return (clone.textContent || '').replace(/\s+/g, ' ');
}

function scan(page, check, where) {
  const t = visibleText(page);
  const hits = FORBIDDEN.filter(([s]) => t.includes(s));
  check.ok(
    hits.length === 0,
    `${where}: 구현 정보 노출 0건`,
    hits.map(([s, why]) => `${s} (${why})`).join(' · '),
  );
}

export const scenarios = [
  {
    id: 'forbidden.admin.after-every-panel-rendered',
    needsData: true,
    title: '관리자 콘솔을 다 그려도 구현 정보가 화면에 없다',
    why: '정적 시험이 못 보는 실행 중 문구 — 배포 후에 두 번 발견됐다',
    async run({ server, check }) {
      const page = await openPage(server, '/console/admin.html');
      await page.settle();

      // 각 탭을 열어 카드가 실제로 그려지게 한다 — 안 그리면 문구가 생기지도 않는다.
      for (const tab of ['ops', 'review', 'train', 'config']) {
        const el = page.q(`.tab[data-tab="${tab}"]`);
        if (el) { page.click(el); await page.settle(); }
        scan(page, check, `${tab} 탭`);
      }

      // 조회 버튼을 눌러 서버 응답으로 만들어지는 문구까지 그린다.
      // 조회 버튼은 탭 전환 뒤에만 보인다 — 보이지 않으면 건너뛴다(누르는 것이 목적이 아니다).
      const rq = page.$('btn-review-queue');
      if (rq && page.visible(rq)) { page.click(rq); await page.settle(); }
      scan(page, check, '검토 목록 조회 후');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'forbidden.demo.index',
    needsData: true,
    title: '등급 시연 화면에 구현 정보가 없다',
    why: 'API 키 기본값이 화면 맨 위에 떠 있던 자리다',
    async run({ server, check }) {
      const page = await openPage(server, '/console/index.html', { bundleModules: true });
      await page.settle();
      scan(page, check, '시연 화면');
      assertNoScriptErrors(check, page);
      return page;
    },
  },
];
