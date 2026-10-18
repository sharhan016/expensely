import time
import os
import re
from pathlib import Path

from playwright.sync_api import sync_playwright

base_url = os.environ.get("UI_BASE_URL", "http://127.0.0.1:3000")
artifacts = Path("/private/tmp/expense-analyzer-ui")
artifacts.mkdir(parents=True, exist_ok=True)

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    errors = []
    page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
    page.goto(f"{base_url}/login")
    page.wait_for_load_state("networkidle")
    page.screenshot(path=artifacts / "login-before.png", full_page=True)
    page.locator(".auth-switch").click()
    page.wait_for_timeout(1000)
    page.screenshot(path=artifacts / "login-after.png", full_page=True)
    page.locator('input[name="name"]').wait_for(timeout=3000)
    page.locator('input[name="name"]').fill("Jasmine Shah")
    page.locator('input[name="email"]').fill(f"ui-{time.time_ns()}@example.com")
    page.locator('input[name="password"]').fill("local-test-password")
    page.get_by_role("button", name="Create workspace").click()
    page.wait_for_url(f"{base_url}/")
    page.wait_for_load_state("networkidle")
    page.get_by_role("heading", name=re.compile(r"Good morning")).wait_for()
    page.screenshot(path=artifacts / "dashboard.png", full_page=True)

    page.get_by_role("link", name="Accounts & Import").click()
    page.wait_for_url(f"{base_url}/imports")
    page.wait_for_load_state("networkidle")
    assert page.get_by_text("Drop bank statements here").is_visible()
    page.screenshot(path=artifacts / "imports.png", full_page=True)

    for route, heading in (
        ("transactions", "Every movement, in order."),
        ("people", "Money has an owner and a reason."),
        ("reimbursements", "Paid personally. Owed professionally."),
        ("trips", "Group travel. Never duplicate spending."),
        ("reconciliation", "Prove each statement period."),
        ("settings", "Make the workspace yours."),
    ):
        page.goto(f"{base_url}/{route}")
        page.wait_for_load_state("networkidle")
        assert page.get_by_role("heading", name=heading).is_visible()

    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{base_url}/imports")
    page.wait_for_load_state("networkidle")
    assert page.get_by_text("Drop bank statements here").is_visible()
    page.screenshot(path=artifacts / "imports-mobile.png", full_page=True)
    assert not errors, errors
    browser.close()

print(f"UI smoke passed; screenshots: {artifacts}")
