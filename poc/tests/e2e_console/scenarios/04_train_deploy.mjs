/* 4. 모델 학습 및 평가 — 학습 제출, 성능 근거, Active Learning 재학습 상태.
 *
 * 요구사항(FUN-004·024)은 학습·평가·이력이다. 모델 활성화, 게이트 우회, 롤백은
 * 이 콘솔의 요구사항 범위가 아니므로 여기서 검증하거나 노출하지 않는다.
 */

import { openPage, goToPane } from '../lib/page.mjs';
import { assertNoScriptErrors } from '../lib/expect.mjs';

async function modelTab(server, opts = {}) {
  const page = await openPage(server, '/console/admin.html', opts);
  await page.settle();
  await goToPane(page, 'train');
  return page;
}

export const scenarios = [
  {
    id: 'model.submit.asks-then-sends',
    writes: true,
    title: '재학습은 한 번 되묻고, 승낙하면 제출되며 작업 목록이 갱신된다',
    async run({ server, check }) {
      const page = await modelTab(server);
      check.ok(page.visible('tr-preset'), '모델 학습 화면에 학습 카드가 보인다');
      check.includes(page.bodyText(), '데이터 분할', '학습·검증·평가 분할을 안내한다');

      page.set('tr-preset', 'demo');
      check.includes(page.text('tr-preset-note'), '시연용', 'preset 설명이 바뀐다');

      page.click(page.q('button[onclick="submitTrain()"]'));
      await page.settle();

      check.ok(page.dialogs.some((d) => d.kind === 'confirm' && d.message.includes('재학습을 제출')), '되묻는다');
      const call = server.lastCall('POST', '/train');
      check.ok(call, 'POST /train 이 나갔다');
      check.eq(call?.body?.hyperparams?.epochs, 1, '시연 preset 의 하이퍼파라미터가 실렸다');
      check.ok(call?.body?.actor?.user_id, '행위자가 실렸다');
      check.ok(server.lastCall('GET', '/train/jobs'), '제출 후 작업 목록을 다시 읽는다');
      check.includes(page.html('jobs-body'), 'aaaaaaaa', '작업 목록에 잡이 그려졌다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'model.submit.cancel-sends-nothing',
    title: '되묻는 창에서 취소하면 학습이 시작되지 않는다',
    async run({ server, check }) {
      const page = await modelTab(server, { confirmAnswer: false });
      page.click(page.q('button[onclick="submitTrain()"]'));
      await page.settle();
      check.eq(server.countCalls('POST', '/train'), 0, '취소하면 제출하지 않는다');
      return page;
    },
  },

  {
    id: 'model.jobs.failure-reason-visible',
    title: '실패한 학습 작업은 상태와 사유를 함께 보여 준다',
    async run({ server, check }) {
      const page = await modelTab(server);
      page.click(page.q('button[onclick="loadJobs()"]'));
      await page.settle();
      const body = page.html('jobs-body');
      check.data.includes(body, 'sample_weight 경로 미배선', '실패 사유 원문이 보인다');
      check.data.includes(body, 'pill err', '실패는 빨간 배지로 구분된다');
      check.data.includes(body, 'running', '진행 중 작업도 함께 보인다');
      return page;
    },
  },

  {
    id: 'model.metrics-history-and-confusion',
    needsData: true,
    title: '성능 지표, 이력, 혼동행렬을 모델 화면에서 확인한다',
    async run({ server, check }) {
      const page = await modelTab(server);
      page.click(page.q('button[onclick="loadMetrics()"]'));
      await page.settle();

      const cards = page.html('metrics-cards');
      check.data.includes(cards, '91.7%', '정확도가 보인다');
      check.includes(cards, 'F1', 'F1 카드가 있다');
      check.data.includes(page.bodyText(), 'hardened42', '평가 데이터 출처가 보인다');
      check.includes(page.html('metrics-context'), '평가 표본 수', '평가 표본 수가 근거와 함께 보인다');
      check.includes(cards, '고등급 미탐률', '고등급 미탐률이 보인다');

      page.click(page.q('button[onclick="loadHistory()"]'));
      await page.settle();
      check.data.includes(page.html('metrics-extra'), 'v-d2b4d2e1', '이력에 이전 모델이 있다');

      page.click(page.q('button[onclick="loadConfusionMatrix()"]'));
      await page.settle();
      const cm = server.lastCall('GET', '/metrics/confusion-matrix/');
      check.ok(cm, '혼동행렬을 요청했다');
      check.data.includes(cm?.path || '', 'v-fe4b386b', '최신 메트릭에서 얻은 모델 버전으로 요청한다');
      check.includes(page.html('metrics-extra'), '혼동행렬', '혼동행렬이 그려졌다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'model.active-learning-status-visible',
    title: '사람이 확정한 교정의 학습 반영 대기 상태를 모델 화면에서 확인한다',
    async run({ server, check }) {
      const page = await modelTab(server);
      page.click(page.q('button[onclick="loadActiveLearning()"]'));
      await page.settle();
      const panel = page.html('active-learning-status');
      check.includes(panel, 'Active Learning', '학습 반영 대기 영역이 보인다');
      check.includes(panel, '12건', '대기 중인 교정 수가 보인다');
      check.ok(server.lastCall('GET', '/admin/dashboard'), '기존 Active Learning 상태를 조회한다');
      check.eq(server.countCalls('POST', '/admin/model/activate'), 0, '모델 활성화 요청은 화면에서 만들지 않는다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },
];
