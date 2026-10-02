"""Probe: document width on every tab at 390px (non-zoomed emulation)."""
import json
from playwright.sync_api import sync_playwright, expect
st = json.load(open("/tmp/r2a3_state.json"))
with sync_playwright() as p:
    b = p.chromium.launch(); page = b.new_page(viewport={"width": 390, "height": 844}); page.set_default_timeout(10000)
    for url, ready in (("?tab=create", "create-job-tab"), ("?tab=queue", "queue-tab"), ("?tab=current", "current-empty"), ("?tab=history", "history-panel"),
                       ("?tab=settings", "settings-tab"), (f"?tab=history&job={st['jobs']['C']}", "history-detail"), (f"?tab=history&job={st['jobs']['E']}", "history-detail")):
        page.goto("http://localhost:8080/" + url); expect(page.get_by_test_id(ready)).to_be_visible()
        page.wait_for_load_state("networkidle")
        wide = page.evaluate("""() => [...document.querySelectorAll('body *')].filter(e => e.getBoundingClientRect().right > innerWidth + 1 && !e.closest('[class*=overflow-x-auto]') && !e.closest('[data-slot=scroll-area-viewport]')).slice(0,4).map(e => (e.getAttribute('data-testid') || e.getAttribute('data-slot') || e.tagName) + ':' + Math.round(e.getBoundingClientRect().right))""")
        print(url[:30], "scrollWidth", page.evaluate("document.documentElement.scrollWidth"), wide)
    b.close()
