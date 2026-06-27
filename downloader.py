"""
Scribd Document Downloader
Downloads documents from Scribd as PDF using the embed URL approach.
Uses Playwright headless browser to render pages and capture as images,
then combines them into a PDF.

Key technique: Chunked download with fresh browser per chunk for stability.
Cookies are NOT used (they cause embed to return 0 pages).
"""

import asyncio
import os
import re
import shutil
import time
import logging
from pathlib import Path
from PIL import Image
from typing import Callable

logger = logging.getLogger(__name__)

CHUNK_SIZE = 50  # Pages per browser session
SCROLL_DELAY = 0.4  # Seconds between page scrolls
FAST_SCROLL_DELAY = 0.02  # Fast scroll for skipping pages
RETRY_CHUNK_SIZE = 25  # Smaller chunks for retry


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


async def get_document_info(
    url: str,
    cookies_json: str | None = None,
    cookies_list: list | None = None,
    timeout: int = 30,
) -> dict:
    """
    Get document info from Scribd WITHOUT downloading.
    Fast probe: loads embed page, counts pages, extracts title.
    """
    from playwright.async_api import async_playwright

    doc_id = extract_doc_id(url)
    if not doc_id:
        return {"success": False, "error": "Invalid Scribd URL"}

    title = extract_doc_title(url)
    embed_url = f"https://www.scribd.com/embeds/{doc_id}/content"

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-setuid-sandbox',
                      '--disable-dev-shm-usage', '--disable-gpu']
            )
            context = await browser.new_context(
                viewport={"width": 1200, "height": 900},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            )
            # NOTE: No cookies added - cookies cause embed to return 0 pages
            page = await context.new_page()

            try:
                await page.goto(embed_url, wait_until="networkidle", timeout=timeout * 1000)
            except Exception:
                await browser.close()
                return {"success": False, "error": "Timeout loading document page"}

            await asyncio.sleep(2)

            page_count = await page.evaluate('document.querySelectorAll(".outer_page").length')
            if page_count == 0:
                page_count = await page.evaluate('document.querySelectorAll("[class*=\\"page\\"]").length')

            try:
                page_title = await page.evaluate('''() => {
                    const t = document.querySelector('title');
                    return t ? t.textContent.trim() : null;
                }''')
                if page_title and page_title != "Scribd":
                    title = page_title.replace(" | PDF", "").strip()
            except Exception:
                pass

            thumbnail = await page.evaluate('''() => {
                const img = document.querySelector('.outer_page img, [class*="page"] img');
                return img ? img.src : null;
            }''')

            await browser.close()

            if page_count == 0:
                return {"success": False, "error": "Could not find document pages. May be restricted."}

            return {
                "success": True,
                "doc_id": doc_id,
                "title": title,
                "pages": page_count,
                "embed_url": embed_url,
                "url": url,
                "thumbnail": thumbnail,
            }
    except Exception as e:
        logger.error(f"Info probe failed: {e}")
        return {"success": False, "error": str(e)}


async def _download_chunk(
    doc_id: str,
    start_page: int,
    end_page: int,
    total_pages: int,
    temp_dir: str,
    scroll_delay: float = SCROLL_DELAY,
) -> list[int]:
    """
    Download a chunk of pages using a fresh browser session.
    Returns list of successfully captured page numbers.
    """
    from playwright.async_api import async_playwright

    embed_url = f"https://www.scribd.com/embeds/{doc_id}/content"

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    '--no-sandbox', '--disable-setuid-sandbox',
                    '--disable-dev-shm-usage', '--disable-gpu',
                    '--disable-extensions',
                ]
            )
            context = await browser.new_context(
                viewport={"width": 1200, "height": 900},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            # No cookies - they break embed access
            page = await context.new_page()

            await page.goto(embed_url, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(3)

            page_count = await page.evaluate('document.querySelectorAll(".outer_page").length')
            if page_count == 0:
                logger.warning(f"Chunk {start_page}-{end_page}: 0 pages in DOM")
                await browser.close()
                return []

            # Fast scroll to reach target area
            for i in range(1, start_page):
                await page.evaluate(f'''() => {{
                    const el = document.getElementById("outer_page_{i}");
                    if (el) el.scrollIntoView();
                }}''')
                if i % 10 == 0:
                    await asyncio.sleep(0.1)
                else:
                    await asyncio.sleep(FAST_SCROLL_DELAY)

            await asyncio.sleep(1)

            # Slow scroll through target pages to trigger lazy loading
            for i in range(start_page, min(end_page + 1, total_pages + 1)):
                await page.evaluate(f'''() => {{
                    const el = document.getElementById("outer_page_{i}");
                    if (el) el.scrollIntoView();
                }}''')
                await asyncio.sleep(scroll_delay)

            await asyncio.sleep(2)

            # Remove overlays and toolbars
            await page.evaluate('''() => {
                const selectors = [
                    '.toolbar_top', '.toolbar_bottom', '.osano-cm-window',
                    '.promo_div', '.between_page_module', '.blurred_page',
                    '[class*="cookie"]', '[class*="banner"]', '[class*="overlay"]'
                ];
                selectors.forEach(sel => {
                    document.querySelectorAll(sel).forEach(el => el.remove());
                });
                document.querySelectorAll('[style*="blur"]').forEach(el => {
                    el.style.filter = 'none';
                });
            }''')

            # Capture pages
            captured = []
            for i in range(start_page, min(end_page + 1, total_pages + 1)):
                try:
                    element = page.locator(f"#outer_page_{i}")
                    if await element.count() > 0:
                        screenshot_path = os.path.join(temp_dir, f"page_{i:04d}.png")
                        await element.screenshot(path=screenshot_path)
                        captured.append(i)
                except Exception as e:
                    logger.warning(f"Failed to capture page {i}: {e}")

            await browser.close()
            return captured

    except Exception as e:
        logger.error(f"Chunk {start_page}-{end_page} failed: {e}")
        return []


async def download_scribd_document(
    url: str,
    output_dir: str = "/tmp/scribd_downloads",
    cookies_json: str | None = None,
    cookies_list: list | None = None,
    quality: int = 90,
    timeout: int = 600,
    chunk_size: int = CHUNK_SIZE,
    progress_callback: Callable | None = None,
) -> dict:
    """
    Download a Scribd document as PDF using chunked approach.

    Uses fresh browser sessions per chunk of pages to avoid memory issues
    and browser crashes on large documents.

    Args:
        url: Scribd document URL
        output_dir: Directory to save the PDF
        cookies_json: Deprecated - cookies break embed access, ignored
        cookies_list: Deprecated - cookies break embed access, ignored
        quality: Screenshot quality (1-100)
        timeout: Max seconds for entire download
        chunk_size: Pages per browser session (default 50)
        progress_callback: Optional async callback(captured, total) for progress

    Returns:
        dict with keys: success, pdf_path, title, pages, error
    """
    doc_id = extract_doc_id(url)
    if not doc_id:
        return {"success": False, "error": "Invalid Scribd URL. Could not extract document ID."}

    title = extract_doc_title(url)

    # Step 1: Get document info
    info = await get_document_info(url)
    if not info["success"]:
        return {"success": False, "error": info["error"]}

    total_pages = info["pages"]
    if info.get("title"):
        title = info["title"]

    logger.info(f"Document: {title} ({total_pages} pages)")

    os.makedirs(output_dir, exist_ok=True)
    temp_dir = os.path.join(output_dir, f"temp_{doc_id}_{int(time.time())}")
    os.makedirs(temp_dir, exist_ok=True)

    start_time = time.time()
    total_captured = 0

    try:
        # Step 2: Download in chunks
        for chunk_start in range(1, total_pages + 1, chunk_size):
            chunk_end = min(chunk_start + chunk_size - 1, total_pages)

            if time.time() - start_time > timeout:
                logger.warning("Timeout reached, stopping download")
                break

            logger.info(f"Downloading chunk: pages {chunk_start}-{chunk_end}")
            captured = await _download_chunk(
                doc_id, chunk_start, chunk_end, total_pages, temp_dir
            )
            total_captured += len(captured)
            logger.info(f"Chunk captured {len(captured)} pages (total: {total_captured}/{total_pages})")

            if progress_callback:
                try:
                    await progress_callback(total_captured, total_pages)
                except Exception:
                    pass

            # Brief pause between chunks
            await asyncio.sleep(1)

        # Step 3: Find and fill missing pages
        existing_pages = set()
        for f in os.listdir(temp_dir):
            if f.startswith("page_") and f.endswith(".png"):
                try:
                    num = int(f.replace("page_", "").replace(".png", ""))
                    existing_pages.add(num)
                except ValueError:
                    pass

        missing = sorted(set(range(1, total_pages + 1)) - existing_pages)

        if missing and time.time() - start_time < timeout:
            logger.info(f"Retrying {len(missing)} missing pages: {missing[:10]}...")

            # Group missing pages into ranges
            ranges = []
            range_start = missing[0]
            prev = missing[0]
            for p in missing[1:]:
                if p > prev + 1:
                    ranges.append((range_start, prev))
                    range_start = p
                prev = p
            ranges.append((range_start, prev))

            # Retry in small sub-chunks with slower scrolling
            for r_start, r_end in ranges:
                for s in range(r_start, r_end + 1, RETRY_CHUNK_SIZE):
                    e = min(s + RETRY_CHUNK_SIZE - 1, r_end)
                    if time.time() - start_time > timeout:
                        break
                    logger.info(f"Retrying pages {s}-{e}")
                    captured = await _download_chunk(
                        doc_id, s, e, total_pages, temp_dir,
                        scroll_delay=0.6  # Slower for reliability
                    )
                    total_captured += len(captured)
                    await asyncio.sleep(1)

        # Step 4: Build PDF from captured pages
        page_files = sorted([
            f for f in os.listdir(temp_dir)
            if f.startswith("page_") and f.endswith(".png")
        ])

        if not page_files:
            return {"success": False, "error": "Failed to capture any pages."}

        images = []
        for pf in page_files:
            img = Image.open(os.path.join(temp_dir, pf))
            if img.mode != 'RGB':
                bg = Image.new('RGB', img.size, (255, 255, 255))
                if img.mode == 'RGBA':
                    bg.paste(img, mask=img.split()[-1])
                else:
                    bg.paste(img)
                img = bg
            images.append(img)

        safe_title = re.sub(r'[^\w\s\-]', '', title)[:100].strip() or "document"
        pdf_filename = f"{safe_title}_{doc_id}.pdf"
        pdf_path = os.path.join(output_dir, pdf_filename)

        images[0].save(
            pdf_path, 'PDF',
            save_all=True,
            append_images=images[1:],
            resolution=150
        )

        # Clean up temp files
        shutil.rmtree(temp_dir, ignore_errors=True)

        final_missing = total_pages - len(images)
        result = {
            "success": True,
            "pdf_path": pdf_path,
            "title": title,
            "pages": len(images),
            "total_pages": total_pages,
            "doc_id": doc_id,
        }
        if final_missing > 0:
            result["warning"] = f"{final_missing} pages could not be captured"

        return result

    except Exception as e:
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
        print(f"   Pages: {result['pages']}/{result['total_pages']}")
        if result.get("warning"):
            print(f"   ⚠️ {result['warning']}")
    else:
        print(f"❌ Error: {result['error']}")
        sys.exit(1)
