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
    page.locator('input[type="file"]').set_input_files(
        {
            "name": "hdfc-smoke.csv",
            "mimeType": "text/csv",
            "buffer": (
                b"HDFC Bank Statement,,,,\n"
                b"Date,Narration,Withdrawal,Deposit,Closing Balance\n"
                b"01/04/2026,UPI WATER,160.00,,99840.00\n"
                b"02/04/2026,SALARY,,50000.00,149840.00\n"
            ),
        }
    )
    page.get_by_role("button", name="Extract transactions").click()
    page.get_by_text("Extraction result").wait_for()
    assert page.get_by_text("Import into account").is_visible()
    page.get_by_label("Select all").check()
    page.locator(".review-toolbar select").select_option("personal")
    page.get_by_role("button", name=re.compile("Confirm to ledger")).click()
    page.get_by_text(re.compile("2 transactions added")).wait_for()

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
        if route == "transactions":
            assert page.get_by_text("UPI WATER").is_visible()

    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{base_url}/imports")
    page.wait_for_load_state("networkidle")
    assert page.get_by_text("Drop bank statements here").is_visible()
    page.screenshot(path=artifacts / "imports-mobile.png", full_page=True)
    assert not errors, errors
    browser.close()

print(f"UI smoke passed; screenshots: {artifacts}")
