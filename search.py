"""
Scribd Search & Category Browser
Search documents by keyword and browse categories on Scribd.
"""

import asyncio
import json
import logging
import re
from typing import Optional
from urllib.parse import quote_plus

logger = logging.getLogger(__name__)

# Scribd categories (curated list)
CATEGORIES = {
    "business": {"name": "Kinh doanh", "icon": "💼", "path": "Business"},
    "education": {"name": "Giáo dục", "icon": "📚", "path": "Education-Teaching"},
    "technology": {"name": "Công nghệ", "icon": "💻", "path": "Technology-Computing"},
    "science": {"name": "Khoa học", "icon": "🔬", "path": "Science"},
    "law": {"name": "Pháp luật", "icon": "⚖️", "path": "Law"},
    "engineering": {"name": "Kỹ thuật", "icon": "🔧", "path": "Engineering"},
    "health": {"name": "Sức khỏe", "icon": "🏥", "path": "Health-Medicine"},
    "finance": {"name": "Tài chính", "icon": "💰", "path": "Finance"},
    "art": {"name": "Nghệ thuật", "icon": "🎨", "path": "Art-Design"},
    "cooking": {"name": "Ẩm thực", "icon": "🍳", "path": "Cooking-Food-Wine"},
    "self_help": {"name": "Phát triển bản thân", "icon": "🧠", "path": "Self-Help"},
    "travel": {"name": "Du lịch", "icon": "✈️", "path": "Travel"},
    "religion": {"name": "Tôn giáo", "icon": "🙏", "path": "Religion-Spirituality"},
    "history": {"name": "Lịch sử", "icon": "📜", "path": "History"},
    "math": {"name": "Toán học", "icon": "📐", "path": "Mathematics"},
    "programming": {"name": "Lập trình", "icon": "👨‍💻", "path": "Programming"},
    "marketing": {"name": "Marketing", "icon": "📢", "path": "Marketing"},
    "psychology": {"name": "Tâm lý học", "icon": "🧩", "path": "Psychology"},
    "music": {"name": "Âm nhạc", "icon": "🎵", "path": "Music"},
    "environment": {"name": "Môi trường", "icon": "🌍", "path": "Environment"},
}


async def search_scribd(query: str, page: int = 1, content_type: str = "documents",
                        language: str = "") -> dict:
    """
    Search Scribd for documents by keyword.
    
    Args:
        query: Search keyword
        page: Page number (1-based)
        content_type: 'documents', 'presentations', 'all'
        language: Filter by language code (e.g., 'vi', 'en')
    
    Returns: {results: [{doc_id, title, author, pages, url, description, thumbnail}], 
              total, page, has_more}
    """
    from sdk.utils.browser import get_browser, close_browser

    encoded_query = quote_plus(query)
    search_url = f"https://www.scribd.com/search?query={encoded_query}&page={page}"
    if content_type == "documents":
        search_url += "&content_type=documents"

    browser_id = f"search-{hash(query) % 10000}"
    results = []

    try:
        browser = await get_browser(browser_id, viewport_width=1280, viewport_height=900)
        page_obj = browser.page

        await page_obj.goto(search_url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)

        # Extract search results from page
        results = await page_obj.evaluate("""() => {
            const items = [];
            // Try multiple selectors for Scribd search results
            const cards = document.querySelectorAll('.SearchResult, [class*="SearchResult"], .document_cell, [data-e2e="search-result"]');
            
            if (cards.length === 0) {
                // Fallback: find all links to documents
                const links = document.querySelectorAll('a[href*="/document/"], a[href*="/doc/"], a[href*="/presentation/"]');
                const seen = new Set();
                links.forEach(link => {
                    const href = link.href;
                    const match = href.match(/\\/(document|doc|presentation)\\/(\\d+)/);
                    if (match && !seen.has(match[2])) {
                        seen.add(match[2]);
                        const title = link.textContent.trim().substring(0, 200) || '';
                        if (title && title.length > 3) {
                            items.push({
                                doc_id: match[2],
                                title: title,
                                url: href.split('?')[0],
                                author: '',
                                pages: 0,
                                description: '',
                                thumbnail: '',
                            });
                        }
                    }
                });
            } else {
                cards.forEach(card => {
                    const link = card.querySelector('a[href*="/document/"], a[href*="/doc/"]');
                    if (!link) return;
                    const href = link.href;
                    const match = href.match(/\\/(document|doc|presentation)\\/(\\d+)/);
                    if (!match) return;
                    
                    const titleEl = card.querySelector('h2, h3, .title, [class*="title"]');
                    const authorEl = card.querySelector('.author, [class*="author"]');
                    const descEl = card.querySelector('.description, [class*="description"], p');
                    const imgEl = card.querySelector('img');
                    const pagesEl = card.querySelector('[class*="pages"]');
                    
                    items.push({
                        doc_id: match[2],
                        title: titleEl ? titleEl.textContent.trim() : link.textContent.trim(),
                        url: href.split('?')[0],
                        author: authorEl ? authorEl.textContent.trim() : '',
                        pages: pagesEl ? parseInt(pagesEl.textContent) || 0 : 0,
                        description: descEl ? descEl.textContent.trim().substring(0, 300) : '',
                        thumbnail: imgEl ? imgEl.src : '',
                    });
                });
            }
            return items;
        }""")

        # Deduplicate
        seen = set()
        unique = []
        for r in results:
            if r["doc_id"] not in seen:
                seen.add(r["doc_id"])
                unique.append(r)
        results = unique[:20]

        await close_browser(browser_id)

    except Exception as e:
        logger.error(f"Search failed: {e}")
        try:
            await close_browser(browser_id)
        except:
            pass

    return {
        "results": results,
        "total": len(results),
        "page": page,
        "query": query,
        "has_more": len(results) >= 10,
    }


async def browse_category(category_key: str, page: int = 1) -> dict:
    """
    Browse documents in a Scribd category.
    
    Args:
        category_key: Key from CATEGORIES dict
        page: Page number
    
    Returns: {results, category_name, total, page}
    """
    if category_key not in CATEGORIES:
        return {"results": [], "category_name": "Unknown", "total": 0, "page": page}

    cat = CATEGORIES[category_key]
    cat_url = f"https://www.scribd.com/explore/{cat['path']}"

    from sdk.utils.browser import get_browser, close_browser

    browser_id = f"cat-{category_key}"
    results = []

    try:
        browser = await get_browser(browser_id, viewport_width=1280, viewport_height=900)
        page_obj = browser.page

        await page_obj.goto(cat_url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)

        # Scroll to load more
        for _ in range(min(page, 3)):
            await page_obj.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(1.5)

        results = await page_obj.evaluate("""() => {
            const items = [];
            const seen = new Set();
            const links = document.querySelectorAll('a[href*="/document/"], a[href*="/doc/"], a[href*="/presentation/"]');
            links.forEach(link => {
                const href = link.href;
                const match = href.match(/\\/(document|doc|presentation)\\/(\\d+)/);
                if (match && !seen.has(match[2])) {
                    seen.add(match[2]);
                    const title = link.textContent.trim();
                    if (title && title.length > 3 && title.length < 300) {
                        const parent = link.closest('[class*="card"], [class*="Cell"], [class*="item"], li, article') || link.parentElement;
                        const imgEl = parent ? parent.querySelector('img') : null;
                        const authorEl = parent ? parent.querySelector('[class*="author"], [class*="Author"]') : null;
                        items.push({
                            doc_id: match[2],
                            title: title,
                            url: href.split('?')[0],
                            author: authorEl ? authorEl.textContent.trim() : '',
                            pages: 0,
                            description: '',
                            thumbnail: imgEl ? imgEl.src : '',
                        });
                    }
                }
            });
            return items;
        }""")

        results = results[:20]
        await close_browser(browser_id)

    except Exception as e:
        logger.error(f"Category browse failed: {e}")
        try:
            await close_browser(browser_id)
        except:
            pass

    return {
        "results": results,
        "category_name": cat["name"],
        "category_icon": cat["icon"],
        "total": len(results),
        "page": page,
    }


def get_categories() -> list[dict]:
    """Get all available categories."""
    return [
        {"key": k, "name": v["name"], "icon": v["icon"], "path": v["path"]}
        for k, v in CATEGORIES.items()
    ]
