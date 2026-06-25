"""
AI Helper for Scribd Downloader
Provides smart URL extraction, link fixing, error diagnosis,
and alternative download strategies.
"""

import re
import logging
import os
from urllib.parse import urlparse, unquote

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════
# Smart URL Extraction & Fixing
# ═══════════════════════════════════════════

def extract_scribd_urls(text: str) -> list[dict]:
    """
    Extract all Scribd URLs from any text input.
    Handles: full URLs, short URLs, mobile URLs, embed URLs, messy text.
    Returns list of {url, doc_id, type, title_hint}.
    """
    results = []
    seen_ids = set()

    # Pattern 1: Full URLs (scribd.com/document/123/title, scribd.com/doc/123/title)
    full_patterns = [
        r'https?://(?:www\.)?scribd\.com/(?:document|doc|presentation|audiobook|book|read)/(\d+)(?:/([^?\s#<>\'"]+))?',
        r'https?://(?:www\.)?scribd\.com/embeds/(\d+)(?:/([^?\s#<>\'"]+))?',
        r'https?://(?:www\.)?scribd\.com/mobile/document/(\d+)(?:/([^?\s#<>\'"]+))?',
        r'https?://(?:www\.)?(?:de|fr|es|pt|it|ru|ja|ko|zh)\.scribd\.com/(?:document|doc|presentation)/(\d+)(?:/([^?\s#<>\'"]+))?',
    ]

    for pattern in full_patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            doc_id = match.group(1)
            if doc_id not in seen_ids:
                seen_ids.add(doc_id)
                title_slug = match.group(2) if match.lastindex >= 2 and match.group(2) else None
                url_type = "document"
                if "/presentation/" in match.group(0):
                    url_type = "presentation"
                elif "/audiobook/" in match.group(0) or "/book/" in match.group(0):
                    url_type = "book"
                elif "/embeds/" in match.group(0):
                    url_type = "embed"
                results.append({
                    "url": f"https://www.scribd.com/document/{doc_id}" + (f"/{title_slug}" if title_slug else ""),
                    "doc_id": doc_id,
                    "type": url_type,
                    "title_hint": unquote(title_slug.replace("-", " ")) if title_slug else None,
                    "original": match.group(0),
                })

    # Pattern 2: Just doc ID (user might paste just "633336102")
    if not results:
        id_matches = re.findall(r'\b(\d{6,12})\b', text)
        for doc_id in id_matches:
            if doc_id not in seen_ids:
                seen_ids.add(doc_id)
                results.append({
                    "url": f"https://www.scribd.com/document/{doc_id}",
                    "doc_id": doc_id,
                    "type": "document",
                    "title_hint": None,
                    "original": doc_id,
                })

    return results


def fix_scribd_url(url: str) -> dict:
    """
    Fix/normalize a Scribd URL. Handles common issues:
    - Missing protocol
    - Mobile URLs
    - Localized domains (de.scribd.com, etc.)
    - Extra tracking params
    - Embed URLs → document URLs
    - URL-encoded characters
    - Trailing garbage
    
    Returns: {success, fixed_url, doc_id, issues_found, suggestions}
    """
    original = url.strip()
    issues = []
    suggestions = []

    # Decode URL entities
    url = unquote(url.strip())

    # Remove common surrounding chars
    url = url.strip('<>"\'()[] \t\n\r')

    # Remove markdown link format [text](url)
    md_match = re.search(r'\[.*?\]\((https?://[^\s)]+)\)', url)
    if md_match:
        url = md_match.group(1)
        issues.append("Trích xuất URL từ markdown link")

    # Remove HTML tag wrapping
    html_match = re.search(r'href=["\']?(https?://[^\s"\'<>]+)', url)
    if html_match:
        url = html_match.group(1)
        issues.append("Trích xuất URL từ HTML href")

    # Add protocol if missing
    if url.startswith('scribd.com') or url.startswith('www.scribd.com'):
        url = 'https://' + url
        issues.append("Thêm https://")

    # Fix double protocol
    url = re.sub(r'^https?://https?://', 'https://', url)

    # Normalize domain
    localized = re.match(r'https?://(?:www\.)?(de|fr|es|pt|it|ru|ja|ko|zh)\.scribd\.com', url)
    if localized:
        url = re.sub(r'(de|fr|es|pt|it|ru|ja|ko|zh)\.scribd\.com', 'www.scribd.com', url)
        issues.append(f"Chuyển từ {localized.group(1)}.scribd.com sang www.scribd.com")

    # Fix mobile URL
    if '/mobile/document/' in url:
        url = url.replace('/mobile/document/', '/document/')
        issues.append("Chuyển từ URL mobile sang desktop")

    # Convert embed URL to document URL
    if '/embeds/' in url:
        embed_match = re.search(r'/embeds/(\d+)', url)
        if embed_match:
            doc_id = embed_match.group(1)
            url = f"https://www.scribd.com/document/{doc_id}"
            issues.append("Chuyển từ embed URL sang document URL")

    # Remove tracking params (utm_, _gl, _ga, etc.)
    url = re.sub(r'[?&](_gl|_ga|_up|utm_\w+|ref|fbclid|gclid|mc_cid|mc_eid)=[^&#]*', '', url)
    # Clean up ? if no params left
    url = re.sub(r'\?$', '', url)
    if url != original and '?' not in url and '&' in original:
        issues.append("Loại bỏ tracking params")

    # Extract doc_id
    doc_id = None
    id_match = re.search(r'scribd\.com/(?:document|doc|presentation|read|book)/(\d+)', url)
    if id_match:
        doc_id = id_match.group(1)
    else:
        # Try embed format
        id_match = re.search(r'scribd\.com/embeds/(\d+)', url)
        if id_match:
            doc_id = id_match.group(1)

    if not doc_id:
        # Try extracting from original text
        extracted = extract_scribd_urls(original)
        if extracted:
            doc_id = extracted[0]["doc_id"]
            url = extracted[0]["url"]
            issues.append("Trích xuất doc ID từ nội dung")
        else:
            # Check if it's just a number
            just_id = re.match(r'^\d{6,12}$', original.strip())
            if just_id:
                doc_id = original.strip()
                url = f"https://www.scribd.com/document/{doc_id}"
                issues.append("Nhận dạng doc ID thuần")
            else:
                return {
                    "success": False,
                    "fixed_url": None,
                    "doc_id": None,
                    "issues_found": issues,
                    "suggestions": [
                        "URL không chứa link Scribd hợp lệ",
                        "Định dạng đúng: https://www.scribd.com/document/123456789/title",
                        "Hoặc dán doc ID (ví dụ: 633336102)",
                    ],
                    "error": "Không tìm thấy Scribd URL hoặc Document ID",
                }

    # Normalize to /doc/ → /document/
    url = re.sub(r'scribd\.com/doc/(\d+)', r'scribd.com/document/\1', url)

    # Build clean URL
    title_match = re.search(r'/document/\d+/([^?\s#]+)', url)
    title_slug = title_match.group(1) if title_match else None
    clean_url = f"https://www.scribd.com/document/{doc_id}" + (f"/{title_slug}" if title_slug else "")

    if clean_url != original:
        if not issues:
            issues.append("URL đã được chuẩn hóa")

    return {
        "success": True,
        "fixed_url": clean_url,
        "doc_id": doc_id,
        "issues_found": issues,
        "suggestions": suggestions,
        "original_url": original,
    }


# ═══════════════════════════════════════════
# Error Diagnosis & Recovery
# ═══════════════════════════════════════════

def diagnose_download_error(error_message: str, url: str = "", doc_id: str = "") -> dict:
    """
    Analyze a download error and provide AI-powered diagnosis with recovery suggestions.
    
    Returns: {
        error_type, diagnosis, suggestions, auto_fix_action, severity
    }
    """
    error_lower = error_message.lower()
    diagnosis = {
        "error_type": "unknown",
        "diagnosis": "",
        "suggestions": [],
        "auto_fix_action": None,  # None or action string for auto-retry
        "severity": "medium",     # low, medium, high
        "can_retry": True,
    }

    # ── Timeout errors ──
    if any(w in error_lower for w in ["timeout", "timed out", "time out"]):
        diagnosis["error_type"] = "timeout"
        diagnosis["diagnosis"] = "Tài liệu mất quá lâu để tải. Có thể do tài liệu rất nhiều trang hoặc server Scribd chậm."
        diagnosis["suggestions"] = [
            "🔄 Thử tải lại — có thể server Scribd đang bận",
            "⏰ Tải vào giờ ít người dùng (sáng sớm hoặc đêm)",
            "📄 Nếu tài liệu >200 trang, có thể mất 3-5 phút",
        ]
        diagnosis["auto_fix_action"] = "retry_with_longer_timeout"
        diagnosis["severity"] = "low"

    # ── Page not found / Invalid URL ──
    elif any(w in error_lower for w in ["not found", "404", "invalid", "could not extract"]):
        diagnosis["error_type"] = "invalid_url"
        fix_result = fix_scribd_url(url) if url else None
        if fix_result and fix_result["success"] and fix_result["issues_found"]:
            diagnosis["diagnosis"] = f"URL có vấn đề. Đã tự sửa: {', '.join(fix_result['issues_found'])}"
            diagnosis["auto_fix_action"] = f"retry_with_url:{fix_result['fixed_url']}"
        else:
            diagnosis["diagnosis"] = "URL không hợp lệ hoặc tài liệu đã bị xóa khỏi Scribd."
            diagnosis["suggestions"] = [
                "🔍 Kiểm tra lại URL (copy trực tiếp từ trình duyệt)",
                "📋 Đảm bảo URL có dạng: scribd.com/document/123456789/title",
                "🗑️ Tài liệu có thể đã bị tác giả xóa",
            ]
        diagnosis["severity"] = "high"
        diagnosis["can_retry"] = bool(fix_result and fix_result["success"])

    # ── No pages found / Restricted ──
    elif any(w in error_lower for w in ["could not find", "no pages", "restricted", "0 pages"]):
        diagnosis["error_type"] = "restricted"
        diagnosis["diagnosis"] = "Không tìm thấy trang nào trong tài liệu. Có thể tài liệu bị hạn chế hoặc là sách/tạp chí (không hỗ trợ)."
        diagnosis["suggestions"] = [
            "📚 Sách (Books/Everand) và Tạp chí (Magazines) KHÔNG được hỗ trợ",
            "🔑 Thêm tài khoản Scribd Premium có thể giúp truy cập tài liệu bị khóa",
            "📄 Chỉ hỗ trợ: Documents, Presentations, Spreadsheets, Sheet Music",
        ]
        diagnosis["auto_fix_action"] = "retry_with_cookies"
        diagnosis["severity"] = "high"

    # ── Auth / Cookie errors ──
    elif any(w in error_lower for w in ["unauthorized", "403", "forbidden", "cookie", "login"]):
        diagnosis["error_type"] = "auth"
        diagnosis["diagnosis"] = "Cần đăng nhập để tải tài liệu này. Cookies có thể đã hết hạn."
        diagnosis["suggestions"] = [
            "🔄 Refresh cookies tài khoản (Tab Tài khoản → Refresh)",
            "👤 Thêm tài khoản Scribd mới nếu tất cả TK đều lỗi",
            "🔑 Đảm bảo tài khoản có quyền truy cập tài liệu này",
        ]
        diagnosis["auto_fix_action"] = "refresh_cookies_and_retry"
        diagnosis["severity"] = "medium"

    # ── Network errors ──
    elif any(w in error_lower for w in ["connection", "network", "dns", "refused", "reset"]):
        diagnosis["error_type"] = "network"
        diagnosis["diagnosis"] = "Lỗi kết nối mạng. Server có thể đang bận hoặc bị chặn."
        diagnosis["suggestions"] = [
            "🔄 Thử tải lại sau vài phút",
            "🌐 Kiểm tra kết nối internet của server",
            "🛡️ Scribd có thể đang rate-limit IP này — đợi 5 phút",
        ]
        diagnosis["auto_fix_action"] = "retry_with_delay"
        diagnosis["severity"] = "medium"

    # ── Browser/Playwright errors ──
    elif any(w in error_lower for w in ["playwright", "browser", "chromium", "crash"]):
        diagnosis["error_type"] = "browser"
        diagnosis["diagnosis"] = "Trình duyệt headless gặp lỗi khi render tài liệu."
        diagnosis["suggestions"] = [
            "🔄 Thử tải lại — lỗi browser thường là tạm thời",
            "💾 Kiểm tra RAM server (cần ít nhất 1GB trống)",
            "📄 Tài liệu quá lớn có thể gây lỗi browser",
        ]
        diagnosis["auto_fix_action"] = "retry"
        diagnosis["severity"] = "medium"

    # ── Screenshot/capture errors ──
    elif any(w in error_lower for w in ["screenshot", "capture", "failed to capture"]):
        diagnosis["error_type"] = "capture"
        diagnosis["diagnosis"] = "Không thể chụp trang tài liệu. Có thể do tải quá chậm hoặc format lạ."
        diagnosis["suggestions"] = [
            "🔄 Thử tải lại — trang có thể chưa render xong",
            "🔍 Tài liệu có thể dùng format đặc biệt (Flash/SVG)",
        ]
        diagnosis["auto_fix_action"] = "retry_with_longer_timeout"
        diagnosis["severity"] = "medium"

    # ── PDF/Image errors ──
    elif any(w in error_lower for w in ["pdf", "image", "pil", "pillow"]):
        diagnosis["error_type"] = "pdf_creation"
        diagnosis["diagnosis"] = "Lỗi khi tạo file PDF từ ảnh chụp trang."
        diagnosis["suggestions"] = [
            "🔄 Thử tải lại — có thể một số trang bị hỏng",
            "💾 Kiểm tra dung lượng ổ đĩa còn trống",
        ]
        diagnosis["auto_fix_action"] = "retry"
        diagnosis["severity"] = "low"

    # ── Generic / Unknown ──
    else:
        diagnosis["error_type"] = "unknown"
        diagnosis["diagnosis"] = f"Lỗi không xác định: {error_message[:200]}"
        diagnosis["suggestions"] = [
            "🔄 Thử tải lại",
            "📋 Kiểm tra URL có đúng format không",
            "🔍 Nếu lỗi lặp lại, thử tài liệu khác để test",
        ]
        diagnosis["auto_fix_action"] = "retry"
        diagnosis["severity"] = "medium"

    return diagnosis


def get_alternative_urls(doc_id: str) -> list[dict]:
    """
    Generate alternative URLs/approaches to try when a download fails.
    """
    return [
        {
            "url": f"https://www.scribd.com/embeds/{doc_id}/content",
            "method": "embed_direct",
            "description": "Embed URL trực tiếp (không cần auth)",
        },
        {
            "url": f"https://www.scribd.com/document/{doc_id}",
            "method": "document_page",
            "description": "Trang tài liệu gốc",
        },
        {
            "url": f"https://www.scribd.com/doc/{doc_id}",
            "method": "short_url",
            "description": "Short URL format (legacy)",
        },
        {
            "url": f"https://www.scribd.com/read/{doc_id}",
            "method": "reader_url",
            "description": "Reader URL (cho sách)",
        },
    ]


# ═══════════════════════════════════════════
# Smart Input Parser
# ═══════════════════════════════════════════

def smart_parse_input(user_input: str) -> dict:
    """
    Parse any user input and determine what to do.
    Handles: URLs, doc IDs, text with URLs, descriptions, etc.
    
    Returns: {
        type: "url" | "doc_id" | "search_query" | "unknown",
        urls: [...],
        fixed_url: str or None,
        suggestions: [...],
        confidence: float
    }
    """
    text = user_input.strip()

    # 1. Try extracting Scribd URLs
    urls = extract_scribd_urls(text)
    if urls:
        best = urls[0]
        fix = fix_scribd_url(best["original"])
        return {
            "type": "url",
            "urls": urls,
            "fixed_url": fix["fixed_url"] if fix["success"] else best["url"],
            "doc_id": best["doc_id"],
            "issues": fix.get("issues_found", []),
            "suggestions": [],
            "confidence": 0.95,
        }

    # 2. Try URL fixing (might be a broken URL)
    if any(c in text for c in ['scribd', 'scrib', '.com', 'http', '/document/']):
        fix = fix_scribd_url(text)
        if fix["success"]:
            return {
                "type": "url",
                "urls": [{"url": fix["fixed_url"], "doc_id": fix["doc_id"]}],
                "fixed_url": fix["fixed_url"],
                "doc_id": fix["doc_id"],
                "issues": fix["issues_found"],
                "suggestions": [],
                "confidence": 0.8,
            }

    # 3. Just a number? Could be doc ID
    if re.match(r'^\d{6,12}$', text):
        return {
            "type": "doc_id",
            "urls": [{"url": f"https://www.scribd.com/document/{text}", "doc_id": text}],
            "fixed_url": f"https://www.scribd.com/document/{text}",
            "doc_id": text,
            "issues": ["Nhận dạng như Document ID"],
            "suggestions": ["Đảm bảo đây là doc ID của Scribd"],
            "confidence": 0.6,
        }

    # 4. Text that might be a search query
    return {
        "type": "search_query",
        "urls": [],
        "fixed_url": None,
        "doc_id": None,
        "issues": [],
        "suggestions": [
            f"Không nhận dạng được URL Scribd trong: \"{text[:80]}\"",
            "Vui lòng dán link trực tiếp từ Scribd",
            "Ví dụ: https://www.scribd.com/document/633336102/title",
            f"🔍 Tìm trên Scribd: https://www.scribd.com/search?query={text.replace(' ', '+')}",
        ],
        "confidence": 0.2,
    }


# CLI test
if __name__ == "__main__":
    test_inputs = [
        "https://www.scribd.com/document/633336102/Ekaterina-Lukasheva-Kusudama-Origami",
        "scribd.com/doc/633336102/test",
        "633336102",
        "https://de.scribd.com/document/633336102/test?_gl=1*abc*_ga*MTg0",
        "https://www.scribd.com/mobile/document/633336102/test",
        "check out this doc https://scribd.com/document/633336102/test and let me know",
        "tôi muốn tải tài liệu origami",
        "[link](https://www.scribd.com/document/633336102/test)",
        "https://www.scribd.com/embeds/633336102/content",
    ]

    for inp in test_inputs:
        print(f"\n{'='*60}")
        print(f"INPUT: {inp}")
        result = smart_parse_input(inp)
        print(f"TYPE: {result['type']} (confidence: {result['confidence']})")
        if result['fixed_url']:
            print(f"URL: {result['fixed_url']}")
        if result['issues']:
            print(f"ISSUES: {result['issues']}")
        if result['suggestions']:
            print(f"SUGGESTIONS: {result['suggestions']}")
