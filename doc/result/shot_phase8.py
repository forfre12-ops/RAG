from playwright.sync_api import sync_playwright

URL = "file:///D:/antigravity/rag/doc/result/%EA%B8%B0%EC%88%A0%EA%B5%AC%ED%98%84_%EB%B0%B1%EC%84%9C_v1.html"

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page()
    pg.set_viewport_size({"width": 1440, "height": 900})
    pg.goto(URL)
    pg.wait_for_timeout(2000)
    pg.evaluate("() => document.querySelectorAll('h3')[26].scrollIntoView({block:'start'})")
    pg.wait_for_timeout(500)
    pg.evaluate("() => window.scrollBy(0, -60)")
    pg.wait_for_timeout(300)
    pg.screenshot(path="D:/antigravity/rag/doc/result/phase8_final.png")
    b.close()
    print("done")
