"""Probe: mobile layout of the history detail tab bar (scroll width, hit-test of each detail tab)."""
import json, sys
from playwright.sync_api import sync_playwright, expect
st = json.load(open("/tmp/r2a3_state.json")); jid = st["jobs"]["C"]
with sync_playwright() as p:
    b = p.chromium.launch()
    for mobile in (True, False):
        ctx = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=mobile, has_touch=mobile)
        page = ctx.new_page(); page.set_default_timeout(10000)
        page.goto(f"http://localhost:8080/?tab=history&job={jid}")
        expect(page.get_by_test_id("history-detail-title")).to_be_visible()
        print("is_mobile", mobile, "scrollWidth", page.evaluate("document.documentElement.scrollWidth"), "innerWidth", page.evaluate("innerWidth"))
        for t in ("transcript", "files", "steps", "log"):
            loc = page.get_by_test_id(f"detail-tab-{t}"); loc.scroll_into_view_if_needed()
            r = loc.evaluate("""el => { const b = el.getBoundingClientRect(); const x = b.left + b.width/2, y = b.top + b.height/2;
                const hit = document.elementFromPoint(x, y); return {left: Math.round(b.left), right: Math.round(b.right), vw: innerWidth,
                hitOk: !!hit && (hit === el || el.contains(hit)), hit: hit ? (hit.getAttribute('data-testid') || hit.className.slice(0,60)) : null} }""")
            print("  ", t, r)
        try:
            page.get_by_test_id("detail-tab-log").click(timeout=4000); print("   log click ok")
        except Exception as e: print("   log click FAILED")
        page.screenshot(path=f"/tmp/frontend-test/mobile_detail_tabs_{'mobile' if mobile else 'narrow'}.png")
        ctx.close()
    b.close()
