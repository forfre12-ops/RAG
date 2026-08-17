"""문서 본문을 읽기 좋게 그리는 공용 렌더러.

왜(2026-08-18). 후보 관리 화면에만 '읽기 좋게 보기' 가 있었고 검수·서명 화면은 원문을
그대로 흘려 놓아 읽기 어려웠다. 같은 문서를 보는 두 화면인데 한쪽만 읽을 만했다.

이 렌더러는 한국어 실문서를 전제로 만들어졌다 — 실측 근거가 JS 주석에 그대로 있다.
    · 실문서는 마크다운이 없고 본문이 한 줄로 몇천 자다(272건 중 58건)
    · 그중 대부분에 【주문】 같은 구획 표시가 있어 거기서 문단을 끊는다
    · 문장 분리는 **한글 종결어미 뒤에서만** — 마침표 전부에서 끊으면 법령 인용
      `1983.11.5. 대통령령` 이 세 줄로 깨진다(마침표 3,208개 중 68%가 숫자 뒤)

⚠ 원문을 바꾸지 않는다. 읽기 위한 줄바꿈일 뿐이고, 판단 근거는 '원문 그대로' 가 정본이다.
⚠ 두 화면이 같이 쓴다. 한쪽에서만 고치면 같은 문서가 다르게 보인다.
"""

DOC_CSS = """.docbody{white-space:pre-wrap;word-break:break-word;max-height:410px;overflow:auto;padding:20px 0 0;font-size:14px;color:#333}
.docbody.md{white-space:normal;line-height:1.62}
.docbody.md h1,.docbody.md h2,.docbody.md h3{letter-spacing:-.5px;margin:22px 0 8px;line-height:1.3}
.docbody.md h1{font-size:22px;border-bottom:2px solid #111;padding-bottom:6px}
.docbody.md h2{font-size:18px;border-bottom:1px solid var(--line);padding-bottom:5px}
.docbody.md h3{font-size:15px;color:#3c454c}
.docbody.md p{margin:9px 0}
.docbody.md ul,.docbody.md ol{margin:9px 0;padding-left:22px}
.docbody.md li{margin:3px 0}
.docbody.md table{border-collapse:collapse;margin:12px 0;font-size:13px;width:100%}
.docbody.md th,.docbody.md td{border:1px solid var(--line);padding:7px 9px;text-align:left;vertical-align:top}
.docbody.md th{background:#f6f6f4;font-weight:800}
.docbody.md code{background:#f4f4f2;border:1px solid #e6e6e3;padding:1px 4px;font:12.5px ui-monospace,monospace}
.docbody.md hr{border:0;border-top:1px solid var(--line);margin:18px 0}
.docbody.md strong{font-weight:800;color:#111}
.docbody.md .lbl{display:inline-block;color:#1b4ea8;font-weight:800;margin-right:5px}"""

DOC_RENDER_JS = r"""function mdToHtml(src){
  const lines=String(src||'').replace(/\r\n/g,'\n').split('\n');
  let out=[],i=0,para=[];
  /* 실문서(판례·공시)는 마크다운이 없고 본문이 한 줄로 몇천 자다 — 실측 272건 중 58건이
     그렇고 그중 56건에 【주문】【이유】 같은 구획 표시가 있다. 그 표시에서 문단을 끊으면
     내용을 바꾸지 않고도 읽을 수 있게 된다. 표시가 없으면 종전대로 한 문단이다. */
  /* 구획 표시에서 문단을 끊는다. 실측 272건 중 실문서 70건에는 마크다운이 없지만
     68건이 아래 표시 중 하나를 갖고 있다 — 【…】 57 · 번호 항목 51 · 가나다 26 · □○※▶ 13.
     내용은 건드리지 않고 끊기만 하며, 라벨만 눈에 띄게 한다. */
  /* 원문자는 ①-⑳(U+2460~) · ❶-❿(U+2776~) · ➀-➉ · ➊-➓(~U+2793) 네 블록에 흩어져 있다.
     한 블록만 잡으면 ➌ 같은 문자를 놓친다(실측). */
  const CIRC='\\u2460-\\u2473\\u2776-\\u2793';
  const SECT=new RegExp('(?=【|<['+CIRC+']|[□○※▶◆■]\\s)');
  /* 라벨 강조는 **이스케이프된 뒤** 적용된다 — `<` 는 이미 `&lt;` 다.
     원문 형태로 매칭하면 각괄호 제목이 통째로 안 잡힌다(실측: 라벨 1개만 붙었다). */
  const LBL=new RegExp('^(【[^】]{1,24}】|&lt;['+CIRC+'][^&]{0,60}&gt;|[□○※▶◆■])');
  /* 표시가 아예 없는 문서(실측 2건)와 표시 사이 구간이 너무 길 때는 문장 단위로 나눈다.
     원문을 바꾸는 게 아니라 읽기 위한 줄바꿈이고, 판단 근거는 '원문 그대로' 가 정본이다. */
  /* 문장마다 줄을 바꾼다. 단, **마침표 전부에서 끊으면 안 된다** — 실측 실문서 70건의
     마침표 3,208개 중 한글 종결어미(다/요/함/임/음) 뒤는 32%뿐이고 68%는 숫자 뒤다.
     `구 특허법시행령(1983.11.5. 대통령령 제11254호)` 같은 법령 인용이 세 줄로 깨진다.
     그래서 종결어미 뒤에서만 끊고, 영문 문장은 `. ` 처럼 뒤에 공백이 있을 때만 끊는다. */
  const sentenceLines=(s)=>{
    /* 앞이 한글인 종결어미 뒤 + 공백에서만 끊는다. 두 가지를 동시에 피하기 위해서다:
         · 숫자 뒤 마침표 — `1983.11.5. 대통령령` 이 두 줄로 깨진다(앞 글자가 숫자라 제외됨)
         · 항목 표시 `가.` `나.` `다.` — 앞이 공백이라 제외됨. `다.` 는 종결어미와 글자가 같아
           앞 글자를 안 보면 항목 표시가 문장 끝으로 오인된다(실측). */
    const parts=s.split(/(?<=[가-힣][다요함임음]\.)\s+/).filter(x=>x&&x.trim());
    const out=[];
    parts.forEach(g=>{
      g=g.trim();
      /* 인용·법조문이 이어져 종결어미가 없는 구간이 있다(실측 최장 3,381자).
         그런 덩어리는 마지막 수단으로 공백에서 끊는다 — 벽으로 남기는 것보다 낫다. */
      while(g.length>900){
        let cut=g.lastIndexOf(' ',700);
        if(cut<300) cut=700;
        out.push(g.slice(0,cut)); g=g.slice(cut).trim();
      }
      if(g) out.push(g);
    });
    return out.length?out:[s];
  };
  const flush=()=>{
    if(!para.length) return;
    const joined=para.join(' ');
    joined.split(SECT).forEach(seg=>{
      seg=seg.trim(); if(!seg) return;
      let html=sentenceLines(seg).map(mdInline).join('<br>');
      html=html.replace(LBL,'<b class="lbl">$1</b>');
      out.push('<p>'+html+'</p>');
    });
    para=[];
  };
  while(i<lines.length){
    const ln=lines[i];
    if(!ln.trim()){flush();i++;continue}
    let m=ln.match(/^(#{1,3})\s+(.*)$/);
    if(m){flush();const lv=m[1].length;out.push(`<h${lv}>`+mdInline(m[2])+`</h${lv}>`);i++;continue}
    if(/^\s*(-{3,}|\*{3,})\s*$/.test(ln)){flush();out.push('<hr>');i++;continue}
    if(/^\s*\|.*\|\s*$/.test(ln)&&i+1<lines.length&&/^\s*\|[\s:|-]+\|\s*$/.test(lines[i+1])){
      flush();
      const cells=r=>r.trim().replace(/^\||\|$/g,'').split('|').map(c=>mdInline(c.trim()));
      let html='<table><thead><tr>'+cells(ln).map(c=>'<th>'+c+'</th>').join('')+'</tr></thead><tbody>';
      i+=2;
      while(i<lines.length&&/^\s*\|.*\|\s*$/.test(lines[i])){
        html+='<tr>'+cells(lines[i]).map(c=>'<td>'+c+'</td>').join('')+'</tr>';i++;
      }
      out.push(html+'</tbody></table>');continue;
    }
    m=ln.match(/^\s*(?:[-*+]|\d+\.)\s+/);
    if(m){
      flush();
      const ordered=/^\s*\d+\./.test(ln);
      let html=ordered?'<ol>':'<ul>';
      while(i<lines.length&&/^\s*(?:[-*+]|\d+\.)\s+/.test(lines[i])){
        html+='<li>'+mdInline(lines[i].replace(/^\s*(?:[-*+]|\d+\.)\s+/,''))+'</li>';i++;
      }
      out.push(html+(ordered?'</ol>':'</ul>'));continue;
    }
    para.push(ln.trim());i++;
  }
  flush();
  return out.join('')||'<p style="color:#8a9299">본문이 없습니다.</p>';
}"""
