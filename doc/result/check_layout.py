from playwright.sync_api import sync_playwright

URL = "file:///D:/antigravity/rag/doc/result/%EA%B8%B0%EC%88%A0%EA%B5%AC%ED%98%84_%EB%B0%B1%EC%84%9C_v1.html"

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page()
    pg.set_viewport_size({"width": 1440, "height": 900})
    pg.goto(URL)
    pg.wait_for_timeout(1500)

    art = pg.evaluate("() => { const r=document.querySelector('article').getBoundingClientRect(); return {x:Math.round(r.x), w:Math.round(r.width)} }")
    print("article:", art)

    rects = pg.evaluate("() => [...document.querySelectorAll('article h3')].map(el=>{ const r=el.getBoundingClientRect(); return {t:el.textContent.trim().slice(0,30), x:Math.round(r.x), w:Math.round(r.width)} })")
    for r in rects:
        print(r)

    b.close()
