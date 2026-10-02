"""Probe: what a ?tab=history&job=<unknown id> deep link shows (detail text + toasts)."""
from playwright.sync_api import sync_playwright, expect
with sync_playwright() as p:
    b = p.chromium.launch(); page = b.new_page(viewport={"width": 1280, "height": 800}); page.set_default_timeout(10000)
    page.goto("http://localhost:8080/?tab=history&job=00000000-0000-4000-8000-000000000000")
    expect(page.get_by_text("Could not load job")).to_be_visible()
    page.wait_for_timeout(1500)
    print("detail:", page.get_by_test_id("history-detail-loading").inner_text())
    print("toasts:", [t.inner_text().replace("\n", " | ") for t in page.locator("[data-sonner-toast]").all()])
    page.screenshot(path="/tmp/frontend-test/desktop_deep_link_invalid.png")
    b.close()
