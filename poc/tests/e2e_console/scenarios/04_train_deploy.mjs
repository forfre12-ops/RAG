/* 4. 학습 · 배포 — 재학습 제출, 작업 목록, 모델 활성화·롤백, 성적 조회.
 *
 * 되돌릴 수 없는 동작(학습 시작·서빙 모델 교체)이 모여 있는 탭이다. 그래서 확인 절차가
 * 실제로 걸리는지, 게이트가 막았을 때 그 사실이 화면에 남는지를 함께 본다.
 */

import { openPage } from '../lib/page.mjs';
import { assertNoScriptErrors } from '../lib/expect.mjs';

async function trainTab(server, opts = {}) {
  const page = await openPage(server, '/console/admin.html', opts);
  await page.settle();
  page.click(page.q('.tab[data-tab="train"]'));
  return page;
}

export const scenarios = [
  {
    id: 'train.submit.asks-then-sends',
    writes: true,
    title: '재학습은 한 번 되묻고, 승낙하면 제출되며 작업 목록이 갱신된다',
    async run({ server, check }) {
      const page = await trainTab(server);
      check.ok(page.visible('tr-preset'), '학습 카드가 학습·배포 탭에서 보인다');

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
    id: 'train.submit.cancel-sends-nothing',
    title: '되묻는 창에서 취소하면 학습이 시작되지 않는다',
    async run({ server, check }) {
      const page = await trainTab(server, { confirmAnswer: false });
      page.click(page.q('button[onclick="submitTrain()"]'));
      await page.settle();
      check.eq(server.countCalls('POST', '/train'), 0, '취소하면 제출하지 않는다');
      return page;
    },
  },

  {
    id: 'train.jobs.failure-reason-visible',
    title: '실패한 학습 잡은 상태만이 아니라 사유까지 같은 줄에 보인다',
    why: '"failed" 만으로는 학습셋 경로 문제인지 자원 문제인지 알 수 없다',
    async run({ server, check }) {
      const page = await trainTab(server);
      page.click(page.q('button[onclick="loadJobs()"]'));
      await page.settle();
      const body = page.html('jobs-body');
      check.data.includes(body, 'sample_weight 경로 미배선', '실패 사유 원문이 보인다');
      check.data.includes(body, 'pill err', '실패는 빨간 배지로 구분된다');
      check.data.includes(body, 'running', '진행 중 잡도 함께 보인다');
      return page;
    },
  },

  {
    id: 'deploy.activate.gate-blocked-visible',
    writes: true,
    title: '배포 게이트가 막으면 「게이트 차단」과 그 사유가 화면에 남는다',
    why: '차단을 조용히 넘기면 "활성화했다"고 오해한 채 옛 모델이 계속 돈다',
    async run({ server, check }) {
      const page = await trainTab(server);
      page.set('act-ver', 'v-cccccccc');
      page.click(page.q('button[onclick="activateModel()"]'));
      await page.settle();

      const call = server.lastCall('POST', '/admin/model/activate');
      check.eq(call?.body?.version_label, 'v-cccccccc', '어느 버전을 올릴지 실렸다');
      check.eq(call?.body?.force, false, 'force 는 기본 꺼짐으로 나간다');
      const out = page.html('activate-result');
      check.includes(out, '게이트 차단', '차단됐다고 화면이 말한다');
      check.includes(out, 'fnr_high', '무엇 때문에 막혔는지 사유가 보인다');
      check.includes(page.logLines('err').join(' '), '활성 차단', '로그에도 남는다');
      check.eq(server.countCalls('GET', '/metrics/latest'), 0, '활성화가 안 됐으므로 메트릭을 새로 읽지 않는다');
      return page;
    },
  },

  {
    id: 'deploy.activate.empty-version',
    title: '버전을 비운 채 활성화를 누르면 안내만 뜨고 요청은 안 나간다',
    async run({ server, check }) {
      const page = await trainTab(server);
      page.set('act-ver', '   ');
      page.click(page.q('button[onclick="activateModel()"]'));
      await page.settle();
      check.ok(page.dialogs.some((d) => d.message.includes('version_label')), '무엇을 입력해야 하는지 알려준다');
      check.eq(server.countCalls('POST', '/admin/model/activate'), 0, '요청이 나가지 않는다');
      return page;
    },
  },

  {
    id: 'deploy.activate.force-double-confirm',
    writes: true,
    title: 'force 우회는 게이트를 건너뛴다고 따로 경고한다',
    async run({ server, check }) {
      const page = await trainTab(server);
      page.set('act-ver', 'v-cccccccc');
      page.check('act-force', true);
      page.click(page.q('button[onclick="activateModel()"]'));
      await page.settle();
      const msgs = page.dialogs.map((d) => d.message).join(' ');
      check.includes(msgs, 'deploy gate', '게이트를 우회한다고 말한다');
      check.includes(msgs, '감사에 기록', '감사에 남는다고 말한다');
      check.eq(server.lastCall('POST', '/admin/model/activate')?.body?.force, true, 'force=true 로 나갔다');
      return page;
    },
  },

  {
    id: 'deploy.rollback.requires-reason',
    writes: true,
    title: '롤백은 사유를 받고, 결과를 화면에 남긴다',
    async run({ server, check }) {
      const page = await trainTab(server, { promptAnswer: '운영 회귀 확인 — 직전 버전으로 복귀' });
      page.click(page.q('button[onclick="rollbackModel()"]'));
      await page.settle();

      check.ok(page.dialogs.some((d) => d.kind === 'prompt'), '사유를 물어본다');
      const call = server.lastCall('POST', '/admin/model/rollback');
      check.includes(call?.body?.reason || '', '운영 회귀 확인', '사유가 그대로 실려 나갔다');
      const note = page.html('rollback-note');
      check.includes(note, '롤백됨', '롤백됐다고 화면이 말한다');
      check.includes(note, 'v-cccccccc', '어디서');
      check.includes(note, 'v-fe4b386b', '어디로 되돌아갔는지 보인다');
      check.ok(page.visible('rollback-note'), '그 결과가 실제로 보인다');
      return page;
    },
  },

  {
    id: 'deploy.rollback.empty-reason-blocked',
    title: '사유를 비우면 롤백하지 않는다',
    async run({ server, check }) {
      const page = await trainTab(server, { promptAnswer: '   ' });
      page.click(page.q('button[onclick="rollbackModel()"]'));
      await page.settle();
      check.ok(page.dialogs.some((d) => d.kind === 'alert' && d.message.includes('사유는 필수')), '사유가 필수라고 알린다');
      check.eq(server.countCalls('POST', '/admin/model/rollback'), 0, '요청이 나가지 않는다');
      return page;
    },
  },

  {
    id: 'deploy.reload.hot-swap',
    writes: true,
    title: '핫리로드는 되묻고, 결과와 함께 최신 메트릭을 다시 읽는다',
    async run({ server, check }) {
      const page = await trainTab(server);
      page.click(page.q('button[onclick="reloadModel()"]'));
      await page.settle();
      check.ok(page.dialogs.some((d) => d.kind === 'confirm'), '되묻는다');
      check.ok(server.lastCall('POST', '/admin/model/reload'), 'POST /admin/model/reload 가 나갔다');
      check.includes(page.html('reload-result'), 'v-fe4b386b', '로드된 버전이 보인다');
      check.includes(page.html('reload-result'), '로드됨', '로드 여부가 보인다');
      check.gte(server.countCalls('GET', '/metrics/latest'), 1, '리로드 후 성적을 다시 읽는다');
      return page;
    },
  },

  {
    id: 'metrics.latest-then-confusion',
    title: '성적 조회 → 이력 → 혼동행렬이 순서대로 열리고 출처를 밝힌다',
    why: '수치만 크게 뜨고 어느 평가셋인지 없으면 감리 질의에 그 자리에서 답할 수 없다',
    async run({ server, check }) {
      const page = await trainTab(server);
      page.click(page.q('button[onclick="loadMetrics()"]'));
      await page.settle();

      const cards = page.html('metrics-cards');
      check.data.includes(cards, '91.7%', '정확도가 보인다');
      check.includes(cards, 'F1', 'F1 카드가 있다');
      check.data.includes(page.bodyText(), 'hardened42', '이 수치가 어느 셋에서 나왔는지 밝힌다');
      check.includes(page.bodyText(), '섞어 인용하지', '다른 셋과 섞지 말라는 경고가 있다');
      check.includes(page.html('metrics-extra'), '등급별 FNR', '등급별 FNR 이 보인다');

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
    id: 'metrics.confusion-needs-version-first',
    title: '성적을 안 본 상태에서 혼동행렬을 누르면 순서를 알려준다',
    async run({ server, check }) {
      const page = await trainTab(server);
      page.click(page.q('button[onclick="loadConfusionMatrix()"]'));
      await page.settle();
      check.ok(page.dialogs.some((d) => d.message.includes('최신 메트릭')), '먼저 무엇을 하라고 알려준다');
      check.eq(server.countCalls('GET', '/metrics/confusion-matrix/'), 0, '헛된 요청이 나가지 않는다');
      return page;
    },
  },

  {
    id: 'metrics.no-history-message',
    title: '평가 이력이 없으면 그 사유를 화면에 적는다',
    needsMock: true,
    async run({ server, check }) {
      const page = await trainTab(server);
      server.faults.push({ path: '/metrics/latest', status: 404, body: { detail: '활성 모델의 평가 이력이 없습니다' } });
      page.click(page.q('button[onclick="loadMetrics()"]'));
      await page.settle();
      check.includes(page.text('metrics-extra'), '메트릭 조회 실패', '실패했다고 말한다');
      check.includes(page.text('metrics-extra'), '평가 실행 이력', '왜 그런지 짚어 준다');
      check.eq(page.html('metrics-cards'), '', '옛 수치가 남아 오해를 만들지 않는다');
      return page;
    },
  },
];
