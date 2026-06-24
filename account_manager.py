"""
Scribd Account Manager
Handles multi-account login, cookie management, and round-robin rotation.
"""

import asyncio
import json
import logging
import os
import time
from typing import Optional

import database as db

logger = logging.getLogger(__name__)


async def login_scribd_account(email: str, password: str) -> dict:
    """
    Login to Scribd and extract cookies using Playwright.

    Returns:
        dict with keys: success, cookies (list), error (str)
    """
    from playwright.async_api import async_playwright

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-setuid-sandbox']
            )
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
            )
            page = await context.new_page()

            # Go to login page
            await page.goto("https://www.scribd.com/login", wait_until="networkidle", timeout=30000)
            await asyncio.sleep(2)

            # Fill email
            try:
                email_input = page.locator('input[name="username"], input[type="email"], input[name="email"]')
                if await email_input.count() > 0:
                    await email_input.first.fill(email)
                else:
                    await page.fill('input[autocomplete="email"]', email)
            except Exception:
                await browser.close()
                return {"success": False, "cookies": [], "error": "Could not find email input"}

            # Fill password
            try:
                pass_input = page.locator('input[name="password"], input[type="password"]')
                if await pass_input.count() > 0:
                    await pass_input.first.fill(password)
            except Exception:
                await browser.close()
                return {"success": False, "cookies": [], "error": "Could not find password input"}

            # Click login button
            try:
                submit = page.locator('button[type="submit"], input[type="submit"]')
                if await submit.count() > 0:
                    await submit.first.click()
                else:
                    await page.keyboard.press("Enter")
            except Exception:
                await page.keyboard.press("Enter")

            # Wait for login to complete
            await asyncio.sleep(5)

            # Check if we need MFA or captcha
            current_url = page.url
            if "login" in current_url.lower():
                # Still on login page - might need MFA or login failed
                error_el = page.locator('[class*="error"], [class*="alert"]')
                if await error_el.count() > 0:
                    error_text = await error_el.first.text_content()
                    await browser.close()
                    return {"success": False, "cookies": [], "error": f"Login failed: {error_text}"}
                await browser.close()
                return {"success": False, "cookies": [], "error": "Login may require MFA or captcha"}

            # Extract cookies
            cookies = await context.cookies()
            # Convert to serializable format
            cookie_list = []
            for c in cookies:
                if "scribd" in c.get("domain", ""):
                    cookie_list.append({
                        "name": c["name"],
                        "value": c["value"],
                        "domain": c["domain"],
                        "path": c.get("path", "/"),
                        "secure": c.get("secure", False),
                        "httpOnly": c.get("httpOnly", False),
                    })

            await browser.close()

            if not cookie_list:
                return {"success": False, "cookies": [], "error": "No cookies extracted"}

            logger.info(f"✅ Logged in {email}: {len(cookie_list)} cookies")
            return {"success": True, "cookies": cookie_list, "error": ""}

    except Exception as e:
        logger.error(f"Login error for {email}: {e}")
        return {"success": False, "cookies": [], "error": str(e)}


async def add_account_with_login(email: str, password: str, label: str = "") -> dict:
    """
    Add a Scribd account: login, extract cookies, save to database.

    Returns:
        dict with keys: success, account_id, message
    """
    # Check if account already exists
    existing = db.get_account_by_email(email)

    # Try to login
    result = await login_scribd_account(email, password)

    if result["success"]:
        account_id = db.add_account(
            email=email,
            password=password,
            cookies=result["cookies"],
            label=label
        )
        return {
            "success": True,
            "account_id": account_id,
            "cookies_count": len(result["cookies"]),
            "message": f"Đã thêm tài khoản {email} ({len(result['cookies'])} cookies)"
        }
    else:
        # Save account without cookies (can login manually later)
        account_id = db.add_account(
            email=email,
            password=password,
            cookies=[],
            label=label
        )
        db.mark_account_error(account_id, result["error"])
        return {
            "success": False,
            "account_id": account_id,
            "message": f"Đã lưu tài khoản nhưng login thất bại: {result['error']}"
        }


def add_account_with_cookies(email: str, cookies: list, password: str = "",
                              label: str = "") -> int:
    """Add an account with pre-extracted cookies (no login needed)."""
    return db.add_account(email=email, password=password, cookies=cookies, label=label)


def get_cookies_for_download() -> tuple[Optional[list], int]:
    """
    Get cookies for the next download using round-robin rotation.

    Returns:
        (cookies_list, account_id) or (None, 0) if no accounts available
    """
    # Get next account (least recently used)
    account = db.get_next_account()
    if not account:
        return None, 0

    cookies = []
    if account["cookies_json"]:
        try:
            cookies = json.loads(account["cookies_json"])
        except json.JSONDecodeError:
            cookies = []

    if not cookies:
        db.mark_account_error(account["id"], "No cookies available")
        # Try next account
        account2 = db.get_next_account()
        if account2 and account2["id"] != account["id"]:
            try:
                cookies = json.loads(account2["cookies_json"])
                return cookies, account2["id"]
            except Exception:
                pass
        return None, 0

    return cookies, account["id"]


async def refresh_account_cookies(account_id: int) -> dict:
    """Re-login an account and refresh cookies."""
    account = db.get_account(account_id)
    if not account:
        return {"success": False, "message": "Account not found"}

    if not account["password"]:
        return {"success": False, "message": "No password stored for this account"}

    result = await login_scribd_account(account["email"], account["password"])
    if result["success"]:
        db.update_account_cookies(account_id, result["cookies"])
        return {"success": True, "message": f"Refreshed {len(result['cookies'])} cookies"}
    else:
        db.mark_account_error(account_id, result["error"])
        return {"success": False, "message": result["error"]}


async def refresh_all_accounts():
    """Refresh cookies for all active accounts."""
    accounts = db.get_all_accounts(include_disabled=False)
    results = []
    for acct in accounts:
        if acct["password"]:
            r = await refresh_account_cookies(acct["id"])
            results.append({"email": acct["email"], **r})
            await asyncio.sleep(5)  # Be gentle with Scribd
    return results


def get_accounts_summary() -> dict:
    """Get a summary of all accounts."""
    all_accounts = db.get_all_accounts(include_disabled=True)
    active = sum(1 for a in all_accounts if a["status"] == "active")
    errored = sum(1 for a in all_accounts if a["status"] == "error")
    disabled = sum(1 for a in all_accounts if a["status"] == "disabled")
    total_downloads = sum(a["download_count"] for a in all_accounts)

    return {
        "total": len(all_accounts),
        "active": active,
        "error": errored,
        "disabled": disabled,
        "total_downloads": total_downloads,
        "accounts": [
            {
                "id": a["id"],
                "email": a["email"],
                "status": a["status"],
                "label": a["label"],
                "download_count": a["download_count"],
                "last_used": a["last_used_at"],
                "error": a["error_message"],
                "has_cookies": bool(a["cookies_json"] and a["cookies_json"] != "[]"),
            }
            for a in all_accounts
        ]
    }
