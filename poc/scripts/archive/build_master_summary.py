# -*- coding: utf-8 -*-
"""20260623 폴더 9개 HTML을 한 장으로 통합한 마스터 요약 문서 생성.
V3의 <style>(디자인 시스템)·base64 로고를 재사용하고 본문만 새로 작성.
타원형 띠 전면 금지: 모든 띠/배지 border-radius:0 강제."""
import re
import pathlib

BASE = pathlib.Path(r"F:/antigravity/rag/doc/result/open/20260623")
src = (BASE / "시스템_완전이해_가이드_V3.html").read_text(encoding="utf-8")

# 1) <style> 블록 통째 추출 (디자인 시스템 재사용)
style = src[src.index("<style>"): src.index("</style>") + len("</style>")]

# 2) base64 로고 data URI 추출
logo = re.search(r'<img src="(data:image/png;base64,[^"]+)"', src).group(1)

# 3) 타원형 금지 오버라이드 (콜아웃·카드·표·배지·pill 모두 직선형)
override = (
    "\n<style>/* 통합본: 타원형 띠 금지 — 모든 띠/박스/배지 직선형 */\n"
    ".callout,.table-wrap,.kpi-card,.grade-card,.case-card,.decision,.tldr,"
    ".scenario,.flow-box,.why-card,.feature-card,.conf-guide,.reason-grid,"
    ".ba-grid,.oss-card,.gloss-card,pre,code,.badge,.badge.outline,.tag,"
    ".grade-badge,.feature-badge,.nav-badge,.brand-mark{border-radius:0!important;}\n"
    "</style>\n"
)

HEAD = (
    '<!DOCTYPE html>\n<html lang="ko">\n<head>\n'
    '<meta charset="UTF-8" />\n'
    '<meta name="viewport" content="width=device-width, initial-scale=1.0" />\n'
    '<title>통합 마스터 요약 — KOIPA AI 영업비밀 분류 시스템 | Koipa</title>\n'
    '<link rel="preconnect" href="https://fonts.googleapis.com" />\n'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />\n'
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Geist+Mono:wght@400;500&family=Geist:wght@300;400;500;600;700;800&display=swap" />\n'
    + style + override +
    "</head>\n<body>\n"
)

NAV = (
    '<nav class="nav"><div class="nav-inner">'
    '<a href="#top" class="brand">'
    '<div class="brand-mark"><img src="' + logo + '" alt="Koipa" /></div>'
    '<span class="brand-name">한국지식재산보호원</span>'
    '<span class="brand-sep">/</span>'
    '<span class="brand-sub">KOIPA AI 영업비밀 분류 — 통합 마스터 요약</span>'
    '</a>'
    '<div class="nav-right">'
    '<span class="nav-badge">MASTER · 2026-06-23</span>'
    '<button class="toc-toggle" id="tocToggle" aria-label="목차 열기">'
    '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">'
    '<line x1="2" y1="4" x2="14" y2="4"/><line x1="2" y1="8" x2="14" y2="8"/><line x1="2" y1="12" x2="10" y2="12"/>'
    '</svg></button></div></div></nav>\n'
)

TOC_LINKS = [
    ("overview", "01. 사업·시스템 개요", False),
    ("scoring", "02. 분류 채점 (S×V×M)", False),
    ("pipeline", "03. 처리 파이프라인", False),
    ("model", "04. 모델·성능 수치", False),
    ("version-note", "모델 버전 정합 주의", True),
    ("gates", "05. 안전장치·검수 게이트", False),
    ("golden", "06. 골든셋 검증", False),
    ("data", "07. 학습 데이터·정제", False),
    ("db", "08. DB 스키마 (20테이블)", False),
    ("api", "09. API (31 라우트)", False),
    ("kl", "10. KL 연동 ICD", False),
    ("rtm", "11. 요구사항 추적 (RTM)", False),
    ("wbs", "12. 일정 (WBS)", False),
    ("ops", "13. 운영·능동학습 폐곡선", False),
    ("numbers", "부록. 핵심 수치 한눈에", False),
]

drawer = ['<div class="toc-overlay" id="tocOverlay"></div>',
          '<div class="toc-drawer" id="tocDrawer">',
          '<div class="toc-drawer-header"><span class="toc-drawer-title">목차</span>',
          '<button class="toc-close" id="tocClose"><svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.8"><line x1="1" y1="1" x2="13" y2="13"/><line x1="13" y1="1" x2="1" y2="13"/></svg></button></div>']
for _id, label, sub in TOC_LINKS:
    cls = ' class="toc-sub"' if sub else ''
    drawer.append('<a href="#%s"%s>%s</a>' % (_id, cls, label))
drawer.append('</div>\n')
DRAWER = "".join(drawer)

aside = ['<aside class="toc-aside"><nav class="toc"><div class="toc-title">목차</div>']
for _id, label, sub in TOC_LINKS:
    cls = ' class="toc-sub"' if sub else ''
    aside.append('<a href="#%s"%s>%s</a>' % (_id, cls, label))
aside.append('</nav></aside>\n')
ASIDE = "".join(aside)

ARTICLE = pathlib.Path(r"F:/antigravity/rag/poc/scripts/_master_article.html").read_text(encoding="utf-8")

FOOTER = (
    '<footer><strong>KOIPA AI 영업비밀 분류 시스템 — 통합 마스터 요약</strong> · '
    'Koipa AI Engine · 9개 정본 문서 병합본 · 2026-06-23<br>'
    '수치는 코드·리포트 대조 기준. 모델 버전 정합(§version-note)은 미해결 항목.</footer>\n'
)

SCRIPT = (
    "<script>\n"
    "var toggle=document.getElementById('tocToggle'),drawer=document.getElementById('tocDrawer'),"
    "ov=document.getElementById('tocOverlay'),cl=document.getElementById('tocClose');\n"
    "function openD(){drawer.classList.add('open');ov.style.display='block';}\n"
    "function closeD(){drawer.classList.remove('open');ov.style.display='none';}\n"
    "if(toggle)toggle.addEventListener('click',openD);if(cl)cl.addEventListener('click',closeD);if(ov)ov.addEventListener('click',closeD);\n"
    "drawer&&drawer.querySelectorAll('a').forEach(function(a){a.addEventListener('click',closeD);});\n"
    "var links=document.querySelectorAll('.toc a, .toc-drawer a');\n"
    "var secs=document.querySelectorAll('section[id]');\n"
    "if('IntersectionObserver' in window){var obs=new IntersectionObserver(function(es){es.forEach(function(e){if(e.isIntersecting){links.forEach(function(l){l.classList.remove('active');});document.querySelectorAll('.toc a[href=\"#'+e.target.id+'\"], .toc-drawer a[href=\"#'+e.target.id+'\"]').forEach(function(l){l.classList.add('active');});}});},{rootMargin:'-15% 0px -75% 0px'});secs.forEach(function(s){obs.observe(s);});}\n"
    "</script>\n"
)

html = (HEAD + NAV + DRAWER +
        '<div class="article-layout">\n<article id="top">\n' +
        ARTICLE +
        '\n</article>\n' + ASIDE + '</div>\n' +
        FOOTER + SCRIPT + "</body>\n</html>\n")

out = BASE / "통합_마스터_요약.html"
out.write_text(html, encoding="utf-8")
print("WROTE", out, len(html), "bytes")
