"""
Scribd Downloader — Public-facing SEO website
Lightweight, fast, SEO-optimized with blog/articles system.
Connects to the existing download API backend.
"""

import os
import json
import time
import logging
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

import database as db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Config
SITE_NAME = os.environ.get("SITE_NAME", "ScribdGet")
SITE_DOMAIN = os.environ.get("SITE_DOMAIN", "scribdget.com")
SITE_DESCRIPTION = "Tải tài liệu Scribd miễn phí — Chỉ cần dán link, nhận PDF ngay lập tức"
API_BACKEND = os.environ.get("API_BACKEND", "http://localhost:8000")
PUBLIC_PORT = int(os.environ.get("PUBLIC_PORT", "80"))

# Blog articles stored as dicts (can later move to DB/files)
ARTICLES_DIR = os.path.join(os.path.dirname(__file__), "articles")
os.makedirs(ARTICLES_DIR, exist_ok=True)

app = FastAPI(title=SITE_NAME, version="1.0.0", docs_url=None, redoc_url=None)


# ═══════════════════════════════════════════
# Blog articles data (seed)
# ═══════════════════════════════════════════

DEFAULT_ARTICLES = [
    {
        "slug": "huong-dan-tai-tai-lieu-scribd-mien-phi",
        "title": "Hướng dẫn tải tài liệu Scribd miễn phí 2026 — Nhanh, đơn giản",
        "description": "Cách tải tài liệu từ Scribd về PDF miễn phí chỉ với 1 click. Hỗ trợ documents, presentations, spreadsheets.",
        "keywords": "tải scribd miễn phí, download scribd, scribd downloader, tải tài liệu scribd",
        "category": "Hướng dẫn",
        "date": "2026-06-20",
        "content": """
<p>Bạn cần tải tài liệu từ <strong>Scribd</strong> nhưng không có tài khoản Premium? <strong>{site}</strong> giúp bạn tải hoàn toàn miễn phí, chỉ với vài bước đơn giản.</p>

<h2>📋 Các bước tải tài liệu</h2>
<ol>
    <li>Truy cập <a href="/">trang chủ {site}</a></li>
    <li>Dán link Scribd vào ô tải (ví dụ: <code>https://www.scribd.com/document/123456/Ten-tai-lieu</code>)</li>
    <li>Nhấn nút <strong>"Tải PDF"</strong></li>
    <li>Đợi 30 giây — 2 phút để hệ thống xử lý</li>
    <li>Tải file PDF về máy!</li>
</ol>

<h2>📄 Loại tài liệu hỗ trợ</h2>
<ul>
    <li>✅ Documents (PDF, Word, Text)</li>
    <li>✅ Presentations (PowerPoint)</li>
    <li>✅ Spreadsheets (Excel)</li>
    <li>✅ Sheet Music (Nhạc)</li>
    <li>❌ Books / Ebooks (Everand) — không hỗ trợ</li>
    <li>❌ Magazines — không hỗ trợ</li>
</ul>

<h2>🔒 An toàn & miễn phí</h2>
<p>{site} không yêu cầu đăng nhập, không thu phí, không lưu thông tin cá nhân. Tài liệu được xử lý tự động và xóa sau 24 giờ.</p>

<h2>💡 Mẹo</h2>
<ul>
    <li>Nếu tải chậm, thử lại sau vài phút — có thể hệ thống đang bận</li>
    <li>File PDF giữ nguyên chất lượng gốc từ Scribd</li>
    <li>Bookmark trang để tải nhanh lần sau!</li>
</ul>
"""
    },
    {
        "slug": "scribd-la-gi-tai-sao-nen-dung",
        "title": "Scribd là gì? Tại sao hàng triệu người dùng Scribd?",
        "description": "Tìm hiểu về Scribd — thư viện số lớn nhất thế giới với hàng triệu tài liệu, sách, audiobooks.",
        "keywords": "scribd là gì, scribd review, thư viện số scribd, scribd vietnam",
        "category": "Kiến thức",
        "date": "2026-06-18",
        "content": """
<p><strong>Scribd</strong> là nền tảng thư viện số lớn nhất thế giới, được thành lập năm 2007. Với hơn <strong>100 triệu tài liệu</strong>, Scribd cho phép người dùng đọc sách, tài liệu học thuật, báo cáo, và nhiều nội dung khác.</p>

<h2>📚 Scribd có gì?</h2>
<ul>
    <li><strong>Documents:</strong> Tài liệu học tập, nghiên cứu, báo cáo từ người dùng upload</li>
    <li><strong>Books:</strong> Hàng trăm nghìn sách điện tử (qua Everand)</li>
    <li><strong>Audiobooks:</strong> Sách nói chất lượng cao</li>
    <li><strong>Magazines:</strong> Tạp chí nổi tiếng thế giới</li>
    <li><strong>Sheet Music:</strong> Bản nhạc, nhạc phẩm</li>
</ul>

<h2>💰 Chi phí</h2>
<p>Scribd có gói Premium <strong>$11.99/tháng</strong>. Tuy nhiên, nhiều tài liệu từ cộng đồng vẫn có thể xem miễn phí (giới hạn). {site} giúp bạn tải các tài liệu này mà không cần đăng ký.</p>

<h2>🌏 Scribd tại Việt Nam</h2>
<p>Scribd rất phổ biến với sinh viên và nghiên cứu sinh Việt Nam. Nhiều tài liệu học thuật, luận văn, và giáo trình được chia sẻ trên nền tảng này.</p>
"""
    },
    {
        "slug": "top-10-tai-lieu-scribd-hay-nhat",
        "title": "Top 10 tài liệu Scribd phổ biến nhất 2026",
        "description": "Tổng hợp 10 tài liệu Scribd được tải nhiều nhất, từ sách lập trình đến tài liệu kinh doanh.",
        "keywords": "tài liệu scribd hay, scribd popular documents, top scribd docs, tải scribd",
        "category": "Tổng hợp",
        "date": "2026-06-15",
        "content": """
<p>Dưới đây là danh sách <strong>10 tài liệu Scribd được tải nhiều nhất</strong> trên {site}, giúp bạn khám phá nội dung chất lượng.</p>

<h2>📊 Top tài liệu</h2>
<ol>
    <li><strong>Python Cookbook — Advanced Recipes</strong> — Sách nấu ăn Python cho lập trình viên</li>
    <li><strong>Data Science from Scratch</strong> — Nhập môn khoa học dữ liệu</li>
    <li><strong>Machine Learning Guide</strong> — Hướng dẫn toàn diện về ML</li>
    <li><strong>Business Model Canvas Template</strong> — Mẫu lập kế hoạch kinh doanh</li>
    <li><strong>IELTS Writing Task 2 Samples</strong> — Bài mẫu IELTS Writing</li>
    <li><strong>Financial Modeling Best Practices</strong> — Mô hình tài chính</li>
    <li><strong>UI/UX Design Principles</strong> — Nguyên tắc thiết kế giao diện</li>
    <li><strong>Kusudama Origami Patterns</strong> — Mẫu gấp giấy nghệ thuật</li>
    <li><strong>Project Management Handbook</strong> — Sổ tay quản lý dự án</li>
    <li><strong>Digital Marketing Strategy 2026</strong> — Chiến lược marketing số</li>
</ol>

<h2>📥 Cách tải</h2>
<p>Tìm tài liệu trên Scribd → Copy link → Dán vào <a href="/">{site}</a> → Nhận PDF miễn phí!</p>
"""
    },
    {
        "slug": "cach-su-dung-scribd-downloader-telegram",
        "title": "Cách dùng Scribd Downloader qua Telegram Bot",
        "description": "Hướng dẫn tải tài liệu Scribd qua Telegram Bot — tiện lợi, nhanh chóng, dùng trên điện thoại.",
        "keywords": "scribd telegram bot, tải scribd qua telegram, scribd downloader bot",
        "category": "Hướng dẫn",
        "date": "2026-06-12",
        "content": """
<p>Ngoài website, bạn còn có thể tải tài liệu Scribd qua <strong>Telegram Bot</strong> — siêu tiện lợi khi dùng điện thoại!</p>

<h2>📱 Các bước sử dụng</h2>
<ol>
    <li>Mở Telegram, tìm bot <strong>@ScribdGetBot</strong></li>
    <li>Nhấn <strong>Start</strong> để bắt đầu</li>
    <li>Gửi link Scribd cho bot</li>
    <li>Đợi bot xử lý và gửi file PDF cho bạn</li>
</ol>

<h2>📝 Lệnh bot</h2>
<ul>
    <li><code>/start</code> — Bắt đầu sử dụng</li>
    <li><code>/help</code> — Xem hướng dẫn</li>
    <li><code>/history</code> — Xem lịch sử tải</li>
    <li><code>/stats</code> — Xem thống kê</li>
</ul>

<h2>✨ Ưu điểm dùng Telegram Bot</h2>
<ul>
    <li>Tải ngay trên điện thoại, không cần mở trình duyệt</li>
    <li>File PDF gửi thẳng vào Telegram, dễ lưu</li>
    <li>Nhanh hơn vì không cần tải trang web</li>
</ul>
"""
    },
    {
        "slug": "so-sanh-cac-cong-cu-tai-scribd",
        "title": "So sánh các công cụ tải Scribd 2026 — Đâu là tốt nhất?",
        "description": "So sánh chi tiết các công cụ và website tải tài liệu Scribd miễn phí: ScribdGet, DLSCRIB, Scribd2PDF...",
        "keywords": "so sánh scribd downloader, scribd download tools, tải scribd miễn phí 2026",
        "category": "So sánh",
        "date": "2026-06-10",
        "content": """
<p>Có nhiều công cụ giúp tải tài liệu Scribd. Dưới đây là so sánh chi tiết để bạn chọn công cụ phù hợp nhất.</p>

<h2>📊 Bảng so sánh</h2>
<div class="table-wrap">
<table>
    <thead>
        <tr><th>Công cụ</th><th>Chất lượng</th><th>Tốc độ</th><th>Miễn phí</th><th>Telegram</th></tr>
    </thead>
    <tbody>
        <tr><td><strong>{site}</strong></td><td>⭐⭐⭐⭐⭐</td><td>Nhanh</td><td>✅</td><td>✅</td></tr>
        <tr><td>DLSCRIB</td><td>⭐⭐⭐</td><td>Trung bình</td><td>✅</td><td>❌</td></tr>
        <tr><td>Scribd2PDF</td><td>⭐⭐</td><td>Chậm</td><td>Giới hạn</td><td>❌</td></tr>
        <tr><td>ScrDownloader</td><td>⭐⭐⭐</td><td>Trung bình</td><td>✅</td><td>❌</td></tr>
    </tbody>
</table>
</div>

<h2>🏆 Tại sao chọn {site}?</h2>
<ul>
    <li><strong>Chất lượng cao nhất</strong> — Render trực tiếp từ Scribd, giữ nguyên layout</li>
    <li><strong>Nhanh</strong> — Hệ thống multi-account, xử lý đồng thời</li>
    <li><strong>Đa nền tảng</strong> — Web + Telegram Bot</li>
    <li><strong>Hoàn toàn miễn phí</strong> — Không giới hạn số lần tải</li>
</ul>
"""
    },
]


def _load_articles():
    """Load articles from JSON file or use defaults."""
    articles_file = os.path.join(ARTICLES_DIR, "articles.json")
    if os.path.exists(articles_file):
        with open(articles_file) as f:
            return json.load(f)
    # Save defaults
    with open(articles_file, "w") as f:
        json.dump(DEFAULT_ARTICLES, f, ensure_ascii=False, indent=2)
    return DEFAULT_ARTICLES


def _get_articles():
    articles = _load_articles()
    for a in articles:
        a["content"] = a["content"].replace("{site}", SITE_NAME)
    return articles


# ═══════════════════════════════════════════
# Helper: stats for homepage
# ═══════════════════════════════════════════

def _get_public_stats():
    try:
        summary = db.get_stats_summary()
        return {
            "total": summary.get("total", 0) or 0,
            "successful": summary.get("successful", 0) or 0,
            "total_pages": summary.get("total_pages", 0) or 0,
        }
    except Exception:
        return {"total": 0, "successful": 0, "total_pages": 0}


# ═══════════════════════════════════════════
# SEO Routes
# ═══════════════════════════════════════════

@app.get("/robots.txt")
async def robots():
    return HTMLResponse(
        f"User-agent: *\nAllow: /\nSitemap: https://{SITE_DOMAIN}/sitemap.xml\n",
        media_type="text/plain"
    )


@app.get("/sitemap.xml")
async def sitemap():
    articles = _get_articles()
    urls = [f"https://{SITE_DOMAIN}/"]
    urls += [f"https://{SITE_DOMAIN}/blog" ]
    urls += [f"https://{SITE_DOMAIN}/blog/{a['slug']}" for a in articles]

    xml = '<?xml version="1.0" encoding="UTF-8"?>\n'
    xml += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    for url in urls:
        xml += f"  <url><loc>{url}</loc><changefreq>weekly</changefreq></url>\n"
    xml += '</urlset>'
    return HTMLResponse(xml, media_type="application/xml")


# ═══════════════════════════════════════════
# Page Routes
# ═══════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
async def homepage():
    stats = _get_public_stats()
    articles = _get_articles()[:3]  # Latest 3 for homepage
    return _render_page(
        title=f"{SITE_NAME} — Tải tài liệu Scribd miễn phí",
        description=SITE_DESCRIPTION,
        keywords="tải scribd, download scribd, scribd downloader, tải tài liệu scribd miễn phí, scribd to pdf",
        body=_home_body(stats, articles),
        canonical="/",
        schema_type="WebApplication"
    )


@app.get("/blog", response_class=HTMLResponse)
async def blog_index():
    articles = _get_articles()
    return _render_page(
        title=f"Blog — {SITE_NAME}",
        description=f"Bài viết hướng dẫn, mẹo và tin tức về tải tài liệu Scribd từ {SITE_NAME}",
        keywords="blog scribd, hướng dẫn scribd, tải tài liệu",
        body=_blog_index_body(articles),
        canonical="/blog"
    )


@app.get("/blog/{slug}", response_class=HTMLResponse)
async def blog_article(slug: str):
    articles = _get_articles()
    article = next((a for a in articles if a["slug"] == slug), None)
    if not article:
        raise HTTPException(404, "Bài viết không tìm thấy")
    return _render_page(
        title=f"{article['title']} — {SITE_NAME}",
        description=article["description"],
        keywords=article.get("keywords", ""),
        body=_article_body(article),
        canonical=f"/blog/{slug}",
        schema_type="Article"
    )


# ═══════════════════════════════════════════
# HTML Templates
# ═══════════════════════════════════════════

def _render_page(title, description, keywords, body, canonical="/", schema_type=None):
    schema_json = ""
    if schema_type == "WebApplication":
        schema_json = f'''<script type="application/ld+json">{{
  "@context": "https://schema.org",
  "@type": "WebApplication",
  "name": "{SITE_NAME}",
  "url": "https://{SITE_DOMAIN}",
  "description": "{description}",
  "applicationCategory": "UtilityApplication",
  "operatingSystem": "Any",
  "offers": {{"@type": "Offer", "price": "0", "priceCurrency": "VND"}}
}}</script>'''
    elif schema_type == "Article":
        schema_json = f'''<script type="application/ld+json">{{
  "@context": "https://schema.org",
  "@type": "Article",
  "headline": "{title}",
  "description": "{description}",
  "publisher": {{"@type": "Organization", "name": "{SITE_NAME}"}}
}}</script>'''

    return f"""<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <meta name="description" content="{description}">
    <meta name="keywords" content="{keywords}">
    <meta name="robots" content="index, follow">
    <link rel="canonical" href="https://{SITE_DOMAIN}{canonical}">

    <!-- Open Graph -->
    <meta property="og:type" content="website">
    <meta property="og:title" content="{title}">
    <meta property="og:description" content="{description}">
    <meta property="og:url" content="https://{SITE_DOMAIN}{canonical}">
    <meta property="og:site_name" content="{SITE_NAME}">

    <!-- Twitter -->
    <meta name="twitter:card" content="summary_large_image">
    <meta name="twitter:title" content="{title}">
    <meta name="twitter:description" content="{description}">

    {schema_json}

    <!-- Ad placeholder -->
    <!-- <script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=YOUR_AD_CLIENT" crossorigin="anonymous"></script> -->

    <style>{_get_css()}</style>
</head>
<body>
    <nav class="nav">
        <div class="nav-inner">
            <a href="/" class="logo">📥 {SITE_NAME}</a>
            <div class="nav-links">
                <a href="/">Trang chủ</a>
                <a href="/blog">Blog</a>
            </div>
        </div>
    </nav>

    <main class="main">
        {body}
    </main>

    <footer class="footer">
        <div class="footer-inner">
            <div class="footer-col">
                <h3>📥 {SITE_NAME}</h3>
                <p>Tải tài liệu Scribd miễn phí, nhanh chóng, chất lượng cao.</p>
            </div>
            <div class="footer-col">
                <h4>Liên kết</h4>
                <a href="/">Trang chủ</a>
                <a href="/blog">Blog</a>
                <a href="/sitemap.xml">Sitemap</a>
            </div>
            <div class="footer-col">
                <h4>Hỗ trợ</h4>
                <a href="/blog/huong-dan-tai-tai-lieu-scribd-mien-phi">Hướng dẫn sử dụng</a>
                <a href="/blog/cach-su-dung-scribd-downloader-telegram">Telegram Bot</a>
            </div>
            <p class="copyright">© 2026 {SITE_NAME}. All rights reserved.</p>
        </div>
    </footer>

    <script>{_get_js()}</script>
</body>
</html>"""


def _home_body(stats, recent_articles):
    stats_total = stats['total']
    stats_pages = stats['total_pages']

    articles_html = ""
    for a in recent_articles:
        articles_html += f'''
        <a href="/blog/{a['slug']}" class="article-card">
            <span class="article-cat">{a.get('category', 'Blog')}</span>
            <h3>{a['title']}</h3>
            <p>{a['description']}</p>
        </a>'''

    return f"""
    <!-- Hero Section -->
    <section class="hero">
        <h1>Tải tài liệu <span class="gradient-text">Scribd</span> miễn phí</h1>
        <p class="hero-sub">Dán link Scribd — nhận file PDF ngay lập tức. Không cần đăng ký.</p>

        <div class="download-box" id="downloadBox">
            <div class="input-row">
                <input type="text" id="urlInput" placeholder="https://www.scribd.com/document/..." autocomplete="off">
                <button id="downloadBtn" onclick="startDownload()">
                    <span id="btnText">📥 Tải PDF</span>
                    <span id="btnSpinner" class="spinner hidden"></span>
                </button>
            </div>
            <div id="statusArea" class="status-area hidden"></div>
        </div>

        <div class="trust-badges">
            <div class="badge">✅ Miễn phí 100%</div>
            <div class="badge">⚡ Tải nhanh 30s</div>
            <div class="badge">🔒 An toàn & bảo mật</div>
            <div class="badge">📄 Giữ nguyên chất lượng</div>
        </div>
    </section>

    <!-- Stats -->
    <section class="stats-bar">
        <div class="stat-item">
            <strong>{stats_total:,}</strong>
            <span>Tài liệu đã tải</span>
        </div>
        <div class="stat-item">
            <strong>{stats_pages:,}</strong>
            <span>Trang đã xử lý</span>
        </div>
        <div class="stat-item">
            <strong>24/7</strong>
            <span>Hoạt động liên tục</span>
        </div>
    </section>

    <!-- Ad slot 1 -->
    <div class="ad-slot" id="ad-slot-1">
        <!-- Insert ad code here -->
        <div class="ad-placeholder">Quảng cáo</div>
    </div>

    <!-- How it works -->
    <section class="how-section">
        <h2>Cách sử dụng</h2>
        <div class="steps">
            <div class="step">
                <div class="step-num">1</div>
                <h3>Tìm tài liệu trên Scribd</h3>
                <p>Mở scribd.com, tìm tài liệu bạn cần và copy URL từ trình duyệt.</p>
            </div>
            <div class="step">
                <div class="step-num">2</div>
                <h3>Dán link vào {SITE_NAME}</h3>
                <p>Paste URL vào ô tải phía trên và nhấn nút "Tải PDF".</p>
            </div>
            <div class="step">
                <div class="step-num">3</div>
                <h3>Tải file PDF</h3>
                <p>Chờ 30 giây — 2 phút. Khi hoàn tất, nhấn nút tải để lưu PDF về máy.</p>
            </div>
        </div>
    </section>

    <!-- Supported formats -->
    <section class="formats-section">
        <h2>Định dạng hỗ trợ</h2>
        <div class="formats-grid">
            <div class="format-item yes">
                <span class="format-icon">📄</span>
                <strong>Documents</strong>
                <small>PDF, Word, Text</small>
            </div>
            <div class="format-item yes">
                <span class="format-icon">📊</span>
                <strong>Presentations</strong>
                <small>PowerPoint, Slides</small>
            </div>
            <div class="format-item yes">
                <span class="format-icon">📈</span>
                <strong>Spreadsheets</strong>
                <small>Excel, CSV</small>
            </div>
            <div class="format-item yes">
                <span class="format-icon">🎵</span>
                <strong>Sheet Music</strong>
                <small>Nhạc, bản nhạc</small>
            </div>
            <div class="format-item no">
                <span class="format-icon">📚</span>
                <strong>Books</strong>
                <small>Không hỗ trợ</small>
            </div>
            <div class="format-item no">
                <span class="format-icon">📰</span>
                <strong>Magazines</strong>
                <small>Không hỗ trợ</small>
            </div>
        </div>
    </section>

    <!-- Ad slot 2 -->
    <div class="ad-slot" id="ad-slot-2">
        <div class="ad-placeholder">Quảng cáo</div>
    </div>

    <!-- FAQ -->
    <section class="faq-section">
        <h2>Câu hỏi thường gặp</h2>
        <div class="faq-list">
            <details class="faq-item">
                <summary>{SITE_NAME} có miễn phí không?</summary>
                <p>Có! {SITE_NAME} hoàn toàn miễn phí, không giới hạn số lần tải.</p>
            </details>
            <details class="faq-item">
                <summary>Mất bao lâu để tải một tài liệu?</summary>
                <p>Thường từ 30 giây đến 2 phút, tùy vào số trang và kích thước tài liệu.</p>
            </details>
            <details class="faq-item">
                <summary>Tải được những loại tài liệu nào?</summary>
                <p>Hỗ trợ Documents, Presentations, Spreadsheets, và Sheet Music. Không hỗ trợ Books (Everand) và Magazines.</p>
            </details>
            <details class="faq-item">
                <summary>Có cần đăng ký tài khoản không?</summary>
                <p>Không cần! Chỉ cần dán link Scribd và tải.</p>
            </details>
            <details class="faq-item">
                <summary>Chất lượng PDF có tốt không?</summary>
                <p>PDF được render trực tiếp từ Scribd nên giữ nguyên chất lượng, layout, và font chữ gốc.</p>
            </details>
        </div>
    </section>

    <!-- Blog preview -->
    <section class="blog-preview">
        <h2>Bài viết mới nhất</h2>
        <div class="articles-grid">
            {articles_html}
        </div>
        <a href="/blog" class="view-all-btn">Xem tất cả bài viết →</a>
    </section>

    <!-- Ad slot 3 -->
    <div class="ad-slot" id="ad-slot-3">
        <div class="ad-placeholder">Quảng cáo</div>
    </div>
"""


def _blog_index_body(articles):
    cards = ""
    for a in articles:
        cards += f'''
        <a href="/blog/{a['slug']}" class="article-card">
            <span class="article-cat">{a.get('category', 'Blog')}</span>
            <h3>{a['title']}</h3>
            <p>{a['description']}</p>
            <time>{a.get('date', '')}</time>
        </a>'''

    return f"""
    <section class="page-section">
        <h1>Blog</h1>
        <p class="page-desc">Hướng dẫn, mẹo và tin tức về tải tài liệu Scribd</p>

        <div class="ad-slot"><div class="ad-placeholder">Quảng cáo</div></div>

        <div class="articles-grid blog-grid">
            {cards}
        </div>

        <div class="ad-slot"><div class="ad-placeholder">Quảng cáo</div></div>
    </section>
"""


def _article_body(article):
    return f"""
    <article class="article-page">
        <div class="article-header">
            <span class="article-cat">{article.get('category', 'Blog')}</span>
            <h1>{article['title']}</h1>
            <time>{article.get('date', '')}</time>
        </div>

        <div class="ad-slot"><div class="ad-placeholder">Quảng cáo</div></div>

        <div class="article-content">
            {article['content']}
        </div>

        <div class="ad-slot"><div class="ad-placeholder">Quảng cáo</div></div>

        <div class="cta-box">
            <h3>📥 Tải tài liệu Scribd ngay!</h3>
            <p>Dán link Scribd vào ô bên dưới để tải PDF miễn phí.</p>
            <div class="input-row mini-dl">
                <input type="text" id="urlInputCTA" placeholder="https://www.scribd.com/document/...">
                <button onclick="startDownloadCTA()">Tải PDF</button>
            </div>
            <div id="statusAreaCTA" class="status-area hidden"></div>
        </div>

        <a href="/blog" class="back-link">← Quay lại Blog</a>
    </article>
"""


# ═══════════════════════════════════════════
# CSS
# ═══════════════════════════════════════════

def _get_css():
    return """
* { margin: 0; padding: 0; box-sizing: border-box; }
:root {
    --bg: #fafbfc; --surface: #ffffff; --surface2: #f3f4f6;
    --border: #e5e7eb; --text: #1f2937; --text2: #6b7280;
    --accent: #6c5ce7; --accent2: #a29bfe; --accent-bg: #f0eeff;
    --success: #10b981; --danger: #ef4444; --warning: #f59e0b;
    --radius: 12px; --shadow: 0 1px 3px rgba(0,0,0,0.08);
}
body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Inter, sans-serif;
    background: var(--bg); color: var(--text);
    line-height: 1.6; -webkit-font-smoothing: antialiased;
}
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }

/* Nav */
.nav {
    background: var(--surface); border-bottom: 1px solid var(--border);
    position: sticky; top: 0; z-index: 100;
    backdrop-filter: blur(10px); background: rgba(255,255,255,0.95);
}
.nav-inner {
    max-width: 1000px; margin: 0 auto; padding: 0 20px;
    display: flex; align-items: center; justify-content: space-between; height: 60px;
}
.logo {
    font-size: 1.3rem; font-weight: 800; color: var(--accent);
    text-decoration: none;
}
.nav-links a {
    color: var(--text2); margin-left: 24px; font-size: 0.95rem; font-weight: 500;
    text-decoration: none;
}
.nav-links a:hover { color: var(--accent); }

/* Main */
.main { max-width: 1000px; margin: 0 auto; padding: 0 20px 40px; }

/* Hero */
.hero { text-align: center; padding: 60px 0 40px; }
.hero h1 { font-size: 2.5rem; font-weight: 800; line-height: 1.2; margin-bottom: 12px; }
.gradient-text {
    background: linear-gradient(135deg, var(--accent), #e17055);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
}
.hero-sub { color: var(--text2); font-size: 1.15rem; margin-bottom: 36px; }

/* Download box */
.download-box {
    background: var(--surface); border: 2px solid var(--border);
    border-radius: var(--radius); padding: 24px; max-width: 640px; margin: 0 auto;
    box-shadow: 0 4px 20px rgba(108,92,231,0.08);
}
.input-row { display: flex; gap: 10px; }
.input-row input {
    flex: 1; padding: 14px 18px; border: 1px solid var(--border);
    border-radius: 8px; font-size: 1rem; background: var(--surface2);
    color: var(--text); outline: none; transition: border 0.2s;
}
.input-row input:focus { border-color: var(--accent); }
.input-row button {
    padding: 14px 28px; background: var(--accent); color: #fff;
    border: none; border-radius: 8px; font-size: 1rem; font-weight: 700;
    cursor: pointer; white-space: nowrap; transition: all 0.2s;
}
.input-row button:hover { background: #5a4bd1; transform: translateY(-1px); }
.input-row button:disabled { opacity: 0.6; cursor: not-allowed; transform: none; }

.status-area {
    margin-top: 16px; padding: 14px; border-radius: 8px;
    font-size: 0.95rem; text-align: center;
}
.status-area.downloading { background: #fef3c7; color: #92400e; }
.status-area.success { background: #d1fae5; color: #065f46; }
.status-area.error { background: #fee2e2; color: #991b1b; }
.hidden { display: none; }

/* File card */
.file-card {
    display: flex; align-items: center; gap: 14px; text-align: left;
    margin-bottom: 10px;
}
.file-card-icon { font-size: 2rem; }
.file-card-info { flex: 1; min-width: 0; }
.file-card-info strong {
    display: block; font-size: 1rem; font-weight: 700;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.file-card-info span { font-size: 0.85rem; opacity: 0.8; }

/* Step indicator */
.step-indicator {
    display: flex; align-items: center; gap: 6px; justify-content: center;
    font-size: 0.82rem; margin: 8px 0; opacity: 0.9;
}
.step-badge {
    display: inline-flex; align-items: center; justify-content: center;
    width: 22px; height: 22px; border-radius: 50%;
    background: rgba(0,0,0,0.1); font-size: 0.7rem; font-weight: 700;
}
.step-badge.active { background: var(--accent); color: #fff; }
.step-badge.done { background: #10b981; color: #fff; }
.step-msg { font-size: 0.9rem; margin-top: 6px; }
.spinner-sm {
    display: inline-block; width: 14px; height: 14px;
    border: 2px solid rgba(0,0,0,0.15); border-top-color: var(--accent);
    border-radius: 50%; animation: spin 0.6s linear infinite;
    vertical-align: middle; margin-right: 4px;
}

/* Progress bar */
.progress-track {
    width: 100%; height: 6px; background: rgba(0,0,0,0.08);
    border-radius: 3px; margin-top: 10px; overflow: hidden;
}
.progress-bar-fill {
    height: 100%; width: 0%; background: var(--accent);
    border-radius: 3px; transition: width 2s ease;
}

/* Download button in status */
.dl-btn {
    display: inline-block; margin-top: 12px; padding: 12px 28px;
    background: var(--accent); color: #fff !important; border-radius: 8px;
    font-weight: 700; text-decoration: none; transition: all 0.2s;
}
.dl-btn:hover { background: #5a4bd1; transform: translateY(-1px); text-decoration: none; }

/* Spinner */
.spinner {
    display: inline-block; width: 18px; height: 18px;
    border: 2px solid rgba(255,255,255,0.3); border-top-color: #fff;
    border-radius: 50%; animation: spin 0.6s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

/* Trust badges */
.trust-badges {
    display: flex; flex-wrap: wrap; justify-content: center;
    gap: 12px; margin-top: 28px;
}
.badge {
    background: var(--surface); border: 1px solid var(--border);
    padding: 8px 16px; border-radius: 20px; font-size: 0.85rem;
    color: var(--text2); font-weight: 500;
}

/* Stats bar */
.stats-bar {
    display: flex; justify-content: center; gap: 40px;
    padding: 32px 0; margin: 20px 0;
    border-top: 1px solid var(--border); border-bottom: 1px solid var(--border);
}
.stat-item { text-align: center; }
.stat-item strong {
    display: block; font-size: 1.8rem; font-weight: 800;
    color: var(--accent);
}
.stat-item span { font-size: 0.85rem; color: var(--text2); }

/* How section */
.how-section { padding: 48px 0; }
.how-section h2, .formats-section h2, .faq-section h2, .blog-preview h2 {
    text-align: center; font-size: 1.7rem; font-weight: 700; margin-bottom: 32px;
}
.steps { display: grid; grid-template-columns: repeat(3, 1fr); gap: 24px; }
.step {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 28px; text-align: center;
}
.step-num {
    width: 40px; height: 40px; border-radius: 50%; background: var(--accent);
    color: #fff; font-weight: 700; font-size: 1.1rem;
    display: inline-flex; align-items: center; justify-content: center;
    margin-bottom: 12px;
}
.step h3 { font-size: 1.05rem; margin-bottom: 8px; }
.step p { color: var(--text2); font-size: 0.9rem; }

/* Formats */
.formats-section { padding: 32px 0; }
.formats-grid {
    display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px;
    max-width: 640px; margin: 0 auto;
}
.format-item {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 20px; text-align: center;
}
.format-item.yes { border-left: 3px solid var(--success); }
.format-item.no { border-left: 3px solid var(--danger); opacity: 0.6; }
.format-icon { font-size: 1.8rem; display: block; margin-bottom: 6px; }
.format-item strong { display: block; font-size: 0.95rem; }
.format-item small { color: var(--text2); font-size: 0.8rem; }

/* FAQ */
.faq-section { padding: 48px 0; max-width: 700px; margin: 0 auto; }
.faq-list { display: flex; flex-direction: column; gap: 10px; }
.faq-item {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: var(--radius); overflow: hidden;
}
.faq-item summary {
    padding: 16px 20px; cursor: pointer; font-weight: 600;
    font-size: 0.95rem; list-style: none;
}
.faq-item summary::before { content: '▸ '; color: var(--accent); }
.faq-item[open] summary::before { content: '▾ '; }
.faq-item p { padding: 0 20px 16px; color: var(--text2); font-size: 0.9rem; }

/* Blog preview */
.blog-preview { padding: 48px 0; }
.articles-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 20px; }
.blog-grid { grid-template-columns: repeat(2, 1fr); }
.article-card {
    display: block; background: var(--surface); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 24px; text-decoration: none;
    color: var(--text); transition: all 0.2s;
}
.article-card:hover {
    border-color: var(--accent); box-shadow: 0 4px 12px rgba(108,92,231,0.12);
    transform: translateY(-2px); text-decoration: none;
}
.article-cat {
    display: inline-block; padding: 3px 10px; border-radius: 12px;
    font-size: 0.75rem; font-weight: 600; background: var(--accent-bg);
    color: var(--accent); margin-bottom: 10px;
}
.article-card h3 { font-size: 1rem; margin-bottom: 8px; line-height: 1.4; }
.article-card p { font-size: 0.85rem; color: var(--text2); line-height: 1.5; }
.article-card time { font-size: 0.8rem; color: var(--text2); margin-top: 8px; display: block; }
.view-all-btn {
    display: inline-block; margin-top: 20px; padding: 10px 24px;
    background: var(--accent-bg); color: var(--accent); border-radius: 8px;
    font-weight: 600; text-align: center; text-decoration: none;
}
.view-all-btn:hover { background: var(--accent); color: #fff; text-decoration: none; }

/* Article page */
.article-page { max-width: 720px; margin: 0 auto; padding: 40px 0; }
.article-header { margin-bottom: 32px; }
.article-header h1 { font-size: 2rem; margin: 12px 0 8px; line-height: 1.3; }
.article-header time { color: var(--text2); font-size: 0.9rem; }
.article-content h2 { margin: 28px 0 14px; font-size: 1.3rem; }
.article-content p { margin-bottom: 14px; color: var(--text); }
.article-content ul, .article-content ol { margin: 12px 0 18px 24px; }
.article-content li { margin-bottom: 6px; }
.article-content code {
    background: var(--surface2); padding: 2px 6px; border-radius: 4px; font-size: 0.9em;
}
.article-content table {
    width: 100%; border-collapse: collapse; margin: 16px 0;
}
.article-content th, .article-content td {
    padding: 10px 14px; border: 1px solid var(--border); text-align: left; font-size: 0.9rem;
}
.article-content th { background: var(--surface2); font-weight: 600; }
.table-wrap { overflow-x: auto; }

/* CTA box */
.cta-box {
    background: var(--accent-bg); border: 2px solid var(--accent);
    border-radius: var(--radius); padding: 28px; margin: 36px 0; text-align: center;
}
.cta-box h3 { font-size: 1.2rem; margin-bottom: 8px; }
.cta-box p { color: var(--text2); margin-bottom: 16px; font-size: 0.95rem; }
.mini-dl { max-width: 500px; margin: 0 auto; }
.mini-dl input { padding: 10px 14px; font-size: 0.9rem; }
.mini-dl button { padding: 10px 20px; font-size: 0.9rem; }

.back-link {
    display: inline-block; margin-top: 28px; color: var(--accent); font-weight: 500;
}

/* Page section */
.page-section { padding: 40px 0; }
.page-section h1 { font-size: 2rem; font-weight: 800; margin-bottom: 8px; }
.page-desc { color: var(--text2); margin-bottom: 32px; }

/* Ad slots */
.ad-slot { margin: 24px 0; text-align: center; min-height: 90px; }
.ad-placeholder {
    background: var(--surface2); border: 1px dashed var(--border);
    border-radius: 8px; padding: 30px; color: var(--text2);
    font-size: 0.85rem;
}

/* Footer */
.footer {
    background: var(--text); color: #d1d5db; padding: 48px 20px 20px; margin-top: 40px;
}
.footer-inner {
    max-width: 1000px; margin: 0 auto;
    display: grid; grid-template-columns: 2fr 1fr 1fr; gap: 32px;
}
.footer h3 { color: #fff; font-size: 1.1rem; margin-bottom: 12px; }
.footer h4 { color: #fff; font-size: 0.95rem; margin-bottom: 10px; }
.footer p { font-size: 0.85rem; line-height: 1.6; }
.footer-col a {
    display: block; color: #9ca3af; font-size: 0.85rem; margin-bottom: 6px;
    text-decoration: none;
}
.footer-col a:hover { color: #fff; }
.copyright {
    grid-column: 1 / -1; text-align: center; padding-top: 24px;
    margin-top: 24px; border-top: 1px solid #374151; font-size: 0.8rem; color: #6b7280;
}

/* Responsive */
@media (max-width: 768px) {
    .hero h1 { font-size: 1.8rem; }
    .input-row { flex-direction: column; }
    .input-row button { width: 100%; }
    .steps { grid-template-columns: 1fr; }
    .formats-grid { grid-template-columns: repeat(2, 1fr); }
    .articles-grid, .blog-grid { grid-template-columns: 1fr; }
    .stats-bar { gap: 20px; }
    .stat-item strong { font-size: 1.3rem; }
    .footer-inner { grid-template-columns: 1fr; }
}
@media (max-width: 480px) {
    .trust-badges { flex-direction: column; align-items: center; }
    .formats-grid { grid-template-columns: 1fr; }
    .hero { padding: 40px 0 24px; }
}
"""


def _get_js():
    return """
const API = '""" + API_BACKEND + """';

async function startDownload() {
    const input = document.getElementById('urlInput');
    const btn = document.getElementById('downloadBtn');
    const btnText = document.getElementById('btnText');
    const spinner = document.getElementById('btnSpinner');
    const url = input.value.trim();

    if (!url) { input.focus(); return; }
    if (!url.includes('scribd.com')) {
        showStatus('error', '❌ Vui lòng nhập link Scribd hợp lệ');
        return;
    }

    btn.disabled = true;
    btnText.textContent = 'Đang lấy thông tin...';
    spinner.classList.remove('hidden');

    // === Step 1: Get document info ===
    showStatus('downloading', '<div class="step-indicator"><span class="step-badge active">1</span> Lấy thông tin <span class="step-badge">2</span> Tải file <span class="step-badge">3</span> Hoàn tất</div><div class="step-msg"><span class="spinner-sm"></span> Đang lấy thông tin tài liệu...</div>');

    try {
        const infoRes = await fetch(API + '/api/info', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({url})
        });
        const info = await infoRes.json();

        if (!infoRes.ok || !info.success) {
            showStatus('error', '❌ ' + (info.detail || info.error || 'Không lấy được thông tin'));
            resetBtn(); return;
        }

        // If cached — show file immediately
        if (info.cached) {
            const sizeMB = info.file_size ? (info.file_size/1048576).toFixed(1) + 'MB' : '';
            showStatus('success', `
                <div class="file-card">
                    <div class="file-card-icon">📄</div>
                    <div class="file-card-info">
                        <strong>${info.title}</strong>
                        <span>${info.pages} trang${sizeMB ? ' · ' + sizeMB : ''} · Đã có sẵn</span>
                    </div>
                </div>
                <a href="${API}/api/file/${info.doc_id}" target="_blank" class="dl-btn">📥 Tải PDF ngay</a>
            `);
            resetBtn(); return;
        }

        // === Step 2: Show info + start download ===
        btnText.textContent = 'Đang tải...';
        showStatus('downloading', `
            <div class="file-card">
                <div class="file-card-icon">📄</div>
                <div class="file-card-info">
                    <strong>${info.title}</strong>
                    <span>${info.pages} trang · doc: ${info.doc_id}</span>
                </div>
            </div>
            <div class="step-indicator"><span class="step-badge done">✓</span> Lấy thông tin <span class="step-badge active">2</span> Tải file <span class="step-badge">3</span> Hoàn tất</div>
            <div class="step-msg"><span class="spinner-sm"></span> Đang render & tải tài liệu... (30s — 2 phút)</div>
            <div class="progress-track"><div class="progress-bar-fill" id="progressFill"></div></div>
        `);

        const dlRes = await fetch(API + '/api/download', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({url})
        });
        const dlData = await dlRes.json();

        if (dlData.status === 'cached') {
            showFileComplete(dlData.title, dlData.pages, dlData.file_size, dlData.doc_id, dlData.download_url);
            resetBtn();
        } else if (dlData.status === 'started' || dlData.status === 'downloading' || dlData.status === 'queued') {
            pollStatus(dlData.doc_id, info.title, info.pages);
        } else {
            showStatus('error', '❌ ' + (dlData.detail || dlData.message || 'Lỗi'));
            resetBtn();
        }
    } catch (e) {
        showStatus('error', '❌ Lỗi kết nối server. Thử lại sau.');
        resetBtn();
    }
}

function showFileComplete(title, pages, fileSize, docId, downloadUrl) {
    const sizeMB = fileSize ? (fileSize/1048576).toFixed(1) + 'MB' : '';
    const dlUrl = downloadUrl || (API + '/api/file/' + docId);
    showStatus('success', `
        <div class="file-card">
            <div class="file-card-icon">✅</div>
            <div class="file-card-info">
                <strong>${title}</strong>
                <span>${pages} trang${sizeMB ? ' · ' + sizeMB : ''}</span>
            </div>
        </div>
        <div class="step-indicator"><span class="step-badge done">✓</span> Lấy thông tin <span class="step-badge done">✓</span> Tải file <span class="step-badge done">✓</span> Hoàn tất</div>
        <a href="${dlUrl}" target="_blank" class="dl-btn">📥 Tải PDF ngay</a>
    `);
}

async function startDownloadCTA() {
    const input = document.getElementById('urlInputCTA');
    const url = input.value.trim();
    if (!url || !url.includes('scribd.com')) {
        const s = document.getElementById('statusAreaCTA');
        if (s) { s.className = 'status-area error'; s.innerHTML = '❌ Vui lòng nhập link Scribd hợp lệ'; s.classList.remove('hidden'); }
        return;
    }
    window.location.href = '/?url=' + encodeURIComponent(url);
}

async function pollStatus(docId, title, pages) {
    let attempts = 0;
    const maxAttempts = 60;
    // Animate progress
    const animInt = setInterval(() => {
        const pct = Math.min(90, (attempts * 3 / 120) * 100);
        const fill = document.getElementById('progressFill');
        if (fill) fill.style.width = pct + '%';
    }, 3000);

    const interval = setInterval(async () => {
        attempts++;
        if (attempts > maxAttempts) {
            clearInterval(interval); clearInterval(animInt);
            showStatus('error', '❌ Timeout — Tài liệu quá lớn hoặc server bận.');
            resetBtn(); return;
        }
        try {
            const resp = await fetch(API + '/api/status/' + docId);
            const data = await resp.json();
            if (data.status === 'completed') {
                clearInterval(interval); clearInterval(animInt);
                showFileComplete(data.title || title, data.pages || pages, data.file_size, docId, data.download_url);
                resetBtn();
            } else if (data.status === 'failed') {
                clearInterval(interval); clearInterval(animInt);
                showStatus('error', '❌ ' + (data.error || 'Tải thất bại'));
                resetBtn();
            }
        } catch (e) { /* retry */ }
    }, 3000);
}

function showStatus(type, html) {
    const el = document.getElementById('statusArea');
    el.className = 'status-area ' + type;
    el.innerHTML = html;
    el.classList.remove('hidden');
}

function resetBtn() {
    const btn = document.getElementById('downloadBtn');
    const btnText = document.getElementById('btnText');
    const spinner = document.getElementById('btnSpinner');
    if (btn) btn.disabled = false;
    if (btnText) btnText.textContent = '📥 Tải PDF';
    if (spinner) spinner.classList.add('hidden');
}

// Pre-fill URL from query params
const params = new URLSearchParams(window.location.search);
if (params.get('url')) {
    const input = document.getElementById('urlInput');
    if (input) {
        input.value = params.get('url');
        startDownload();
    }
}
"""


# ═══════════════════════════════════════════
# Run
# ═══════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn
    db.init_db()
    uvicorn.run(app, host="0.0.0.0", port=PUBLIC_PORT)
