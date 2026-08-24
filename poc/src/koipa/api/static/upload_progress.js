/* 문서 업로드·분석 진행 오버레이 — 세 화면(index · admin · manage)이 같은 것을 쓴다.
 *
 * 서버가 진행률을 주지 않으므로 경과 시간과 예상 시간만 보여 준다. 예상 계수는 223 실측
 * (2026-08-21) 14청크 6.2초 → 고정비 0.5초 + 0.4초/청크. 청크 수는 파일 크기로 어림한다
 * (1청크 ≈ 700바이트). 어림이라는 것을 화면에 적는다.
 *
 * 막대는 95%에서 멈춘다 — 100%로 채워 놓고 안 끝나면 거짓말이 된다.
 * 반드시 finally 에서 stop() 할 것. 오버레이가 남으면 화면 전체가 잠긴다.
 *
 * 클래식 스크립트다(ES 모듈 아님) — admin.html 의 인라인 script 와 index.html 의
 * type=module 양쪽에서 window.UploadProgress 로 같이 쓰기 위해서다.
 */
(function () {
  var box = null, t0 = 0, timer = null, est = 0;

  function ensure() {
    if (box) return box;
    box = document.createElement('div');
    box.id = 'analyze-progress';
    box.setAttribute('role', 'status');
    box.setAttribute('aria-live', 'polite');
    box.style.cssText = 'position:fixed;inset:0;background:rgba(17,17,17,.72);display:flex;'
      + 'align-items:center;justify-content:center;z-index:300;padding:20px';
    box.innerHTML = '<div style="background:#fff;max-width:420px;width:100%;padding:26px 28px;'
      + 'box-shadow:0 12px 40px rgba(0,0,0,.28)">'
      + '<div style="display:flex;align-items:center;gap:10px">'
      + '<span id="ap-spin" style="display:inline-block;width:15px;height:15px;border:2px solid #d9d9d6;'
      + 'border-top-color:#111;border-radius:50%;animation:ap-rot .8s linear infinite"></span>'
      + '<b id="ap-title" style="font-size:15px">문서를 분석하고 있습니다</b></div>'
      + '<div id="ap-file" style="margin-top:10px;font-size:12.5px;color:#70757a;word-break:break-all"></div>'
      + '<div style="display:flex;gap:22px;margin-top:16px">'
      + '<div><div style="font-size:11px;color:#8f9498">경과</div>'
      + '<b id="ap-el" style="font:700 22px/1.2 Arial,sans-serif">0초</b></div>'
      + '<div><div style="font-size:11px;color:#8f9498">예상(어림)</div>'
      + '<b id="ap-est" style="font:700 22px/1.2 Arial,sans-serif;color:#70757a">–</b></div></div>'
      + '<div style="height:4px;background:#eceae7;margin-top:14px;overflow:hidden">'
      + '<div id="ap-bar" style="height:100%;width:0;background:#111;transition:width .4s linear"></div></div>'
      + '<div id="ap-note" style="margin-top:12px;font-size:12px;color:#70757a;line-height:1.6"></div></div>';
    var st = document.createElement('style');
    st.textContent = '@keyframes ap-rot{to{transform:rotate(360deg)}}';
    document.body.appendChild(st);
    document.body.appendChild(box);
    return box;
  }

  function el(id) { return document.getElementById(id); }
  function fmtSec(s) {
    return s < 60 ? s + '초'
      : Math.floor(s / 60) + '분 ' + String(s % 60).padStart(2, '0') + '초';
  }

  window.UploadProgress = {
    /* file 은 File 이어도 되고 {name,size} 여도 된다. title 로 화면별 문구를 바꾼다. */
    start: function (file, title) {
      ensure().style.display = 'flex';
      if (title) el('ap-title').textContent = title;
      var bytes = (file && file.size) || 0;
      var chunks = Math.max(1, Math.round(bytes / 700));
      est = Math.max(3, Math.round(0.5 + chunks * 0.4));
      el('ap-file').textContent = (file && file.name ? file.name : '')
        + (bytes ? ('  ·  ' + bytes.toLocaleString() + 'B  ·  청크 약 ' + chunks.toLocaleString() + '개') : '');
      el('ap-est').textContent = fmtSec(est);
      el('ap-note').textContent = chunks > 60
        ? '큰 문서라 몇 분 걸릴 수 있습니다. 창을 닫지 마십시오 — 끝나면 결과가 바로 나옵니다.'
        : '첫 요청은 모델을 올리느라 조금 더 걸립니다.';
      el('ap-bar').style.width = '0';
      el('ap-el').textContent = '0초';
      t0 = Date.now();
      clearInterval(timer);
      timer = setInterval(function () {
        var e = Math.round((Date.now() - t0) / 1000);
        el('ap-el').textContent = fmtSec(e);
        el('ap-bar').style.width = Math.min(95, Math.round(e / Math.max(est, 1) * 100)) + '%';
        if (e > est) el('ap-est').textContent = fmtSec(est) + ' 초과';
      }, 1000);
    },
    note: function (msg) {
      var e = box && el('ap-note');
      if (e) e.textContent = msg;
    },
    stop: function () {
      clearInterval(timer);
      timer = null;
      if (box) box.style.display = 'none';
    },
    /* 시험용 — 떠 있는지 */
    isOpen: function () { return !!(box && box.style.display === 'flex'); },
  };
})();
