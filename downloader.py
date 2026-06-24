"""
Scribd Document Downloader
Downloads documents from Scribd as PDF using the embed URL approach.
Uses Playwright headless browser to render pages and capture as images,
then combines them into a PDF.
"""

import asyncio
import os
import re
import shutil
import time
import logging
from pathlib import Path
from PIL import Image

logger = logging.getLogger(__name__)


def extract_doc_id(url: str) -> str | None:
    """Extract document ID from various Scribd URL formats."""
    patterns = [
        r'scribd\.com/doc(?:ument)?/(\d+)',
        r'scribd\.com/presentation/(\d+)',
        r'scribd\.com/embeds/(\d+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def extract_doc_title(url: str) -> str:
    """Extract document title from URL slug."""
    match = re.search(r'/(\d+)/([^/?#]+)', url)
    if match:
        return match.group(2).replace('-', ' ')
    return "document"


async def download_scribd_document(
    url: str,
    output_dir: str = "/tmp/scribd_downloads",
    cookies_json: str | None = None,
    quality: int = 90,
    timeout: int = 120,
) -> dict:
    """
    Download a Scribd document as PDF.
    
    Args:
        url: Scribd document URL
        output_dir: Directory to save the PDF
        cookies_json: Optional path to cookies.json for authenticated downloads
        quality: Screenshot quality (1-100)
        timeout: Max seconds to wait for page load
    
    Returns:
        dict with keys: success, pdf_path, title, pages, error
    """
    from playwright.async_api import async_playwright
    
    doc_id = extract_doc_id(url)
    if not doc_id:
        return {"success": False, "error": "Invalid Scribd URL. Could not extract document ID."}
    
    title = extract_doc_title(url)
    embed_url = f"https://www.scribd.com/embeds/{doc_id}/content"
    
    os.makedirs(output_dir, exist_ok=True)
    temp_dir = os.path.join(output_dir, f"temp_{doc_id}_{int(time.time())}")
    os.makedirs(temp_dir, exist_ok=True)
    
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-gpu',
                ]
            )
            
            context = await browser.new_context(
                viewport={"width": 1200, "height": 900},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            
            # Load cookies if provided
            if cookies_json and os.path.exists(cookies_json):
                import json
                with open(cookies_json) as f:
                    cookies = json.load(f)
                # Convert cookie format for Playwright
                pw_cookies = []
                for c in cookies:
                    pw_cookie = {
                        "name": c["name"],
                        "value": c["value"],
                        "domain": c.get("domain", ".scribd.com"),
                        "path": c.get("path", "/"),
                    }
                    if c.get("secure"):
                        pw_cookie["secure"] = True
                    if c.get("httpOnly"):
                        pw_cookie["httpOnly"] = True
                    if c.get("sameSite"):
                        ss = c["sameSite"]
                        if ss in ("Strict", "Lax", "None"):
                            pw_cookie["sameSite"] = ss
                    pw_cookies.append(pw_cookie)
                await context.add_cookies(pw_cookies)
            
            page = await context.new_page()
            
            logger.info(f"Loading embed URL: {embed_url}")
            try:
                await page.goto(embed_url, wait_until="networkidle", timeout=timeout * 1000)
            except Exception:
                # Fallback: try with domcontentloaded
                await page.goto(embed_url, wait_until="domcontentloaded", timeout=timeout * 1000)
            
            await asyncio.sleep(2)
            
            # Get total pages
            page_count = await page.evaluate('document.querySelectorAll(".outer_page").length')
            
            if page_count == 0:
                # Try alternative selectors
                page_count = await page.evaluate('''() => {
                    const pages = document.querySelectorAll('[class*="page"]');
                    return pages.length;
                }''')
            
            if page_count == 0:
                await browser.close()
                return {"success": False, "error": "Could not find document pages. The document may be restricted or URL invalid."}
            
            logger.info(f"Found {page_count} pages")
            
            # Get actual document title from the page if possible
            try:
                page_title = await page.evaluate('''() => {
                    const titleEl = document.querySelector('title');
                    return titleEl ? titleEl.textContent.trim() : null;
                }''')
                if page_title and page_title != "Scribd":
                    title = page_title.replace(" | PDF", "").strip()
            except Exception:
                pass
            
            # Scroll through all pages to force lazy-loading
            for i in range(1, page_count + 1):
                await page.evaluate(f'''() => {{
                    const el = document.getElementById("outer_page_{i}");
                    if (el) el.scrollIntoView();
                }}''')
                await asyncio.sleep(0.4)
            
            # Small wait for final renders
            await asyncio.sleep(1)
            
            # Remove toolbars and overlays
            await page.evaluate('''() => {
                const selectors = [
                    '.toolbar_top', '.toolbar_bottom', '.osano-cm-window',
                    '.promo_div', '.between_page_module', '.blurred_page',
                    '[class*="cookie"]', '[class*="banner"]', '[class*="overlay"]'
                ];
                selectors.forEach(sel => {
                    document.querySelectorAll(sel).forEach(el => el.remove());
                });
                // Remove blur from any blurred pages
                document.querySelectorAll('[style*="blur"]').forEach(el => {
                    el.style.filter = 'none';
                });
            }''')
            
            # Screenshot each page
            images_paths = []
            for i in range(1, page_count + 1):
                try:
                    element = page.locator(f"#outer_page_{i}")
                    if await element.count() > 0:
                        screenshot_path = os.path.join(temp_dir, f"page_{i:04d}.png")
                        await element.screenshot(path=screenshot_path)
                        images_paths.append(screenshot_path)
                        logger.info(f"Captured page {i}/{page_count}")
                except Exception as e:
                    logger.warning(f"Failed to capture page {i}: {e}")
            
            await browser.close()
            
            if not images_paths:
                return {"success": False, "error": "Failed to capture any pages."}
            
            # Combine images into PDF
            safe_title = re.sub(r'[^\w\s\-]', '', title)[:100].strip() or "document"
            pdf_filename = f"{safe_title}_{doc_id}.pdf"
            pdf_path = os.path.join(output_dir, pdf_filename)
            
            images = []
            for img_path in images_paths:
                img = Image.open(img_path)
                if img.mode != 'RGB':
                    bg = Image.new('RGB', img.size, (255, 255, 255))
                    if img.mode == 'RGBA':
                        bg.paste(img, mask=img.split()[-1])
                    else:
                        bg.paste(img)
                    img = bg
                images.append(img)
            
            if images:
                images[0].save(
                    pdf_path, 'PDF',
                    save_all=True,
                    append_images=images[1:],
                    resolution=150
                )
            
            # Clean up temp files
            shutil.rmtree(temp_dir, ignore_errors=True)
            
            return {
                "success": True,
                "pdf_path": pdf_path,
                "title": title,
                "pages": len(images),
                "doc_id": doc_id,
            }
    
    except Exception as e:
        # Clean up on error
        shutil.rmtree(temp_dir, ignore_errors=True)
        logger.error(f"Download failed: {e}")
        return {"success": False, "error": str(e)}


# CLI usage
if __name__ == "__main__":
    import sys
    
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    
    if len(sys.argv) < 2:
        print("Usage: python downloader.py <scribd_url> [output_dir]")
        sys.exit(1)
    
    url = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else "/tmp/scribd_downloads"
    
    result = asyncio.run(download_scribd_document(url, output_dir))
    
    if result["success"]:
        print(f"✅ Downloaded: {result['pdf_path']}")
        print(f"   Title: {result['title']}")
        print(f"   Pages: {result['pages']}")
    else:
        print(f"❌ Error: {result['error']}")
        sys.exit(1)
