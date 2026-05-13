"""
parser.py
---------
Heuristic HTML parser to extract product cards from any e-commerce page.
Extraction priority:
  - Image  : og:image → largest <img> with src → first <img> with src
  - Title  : og:title → <h1> → <h2> → <title>
  - Desc   : og:description → meta[name=description] → first <p> near title
"""

import httpx
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import logging

logger = logging.getLogger(__name__)


# ── HTTP ──────────────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def fetch_html(url: str, timeout: int = 15) -> str | None:
    """Fetch raw HTML from a URL using httpx. Returns None on failure."""
    try:
        with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=timeout) as client:
            response = client.get(url)
            response.raise_for_status()
            return response.text
    except Exception as e:
        logger.error(f"Failed to fetch {url}: {e}")
        return None


def fetch_html_playwright(url: str) -> str | None:
    """
    Fallback: fetch HTML via headless Chromium (for JS-rendered pages).
    Used when httpx returns empty or no product cards detected.
    """
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(extra_http_headers=HEADERS)
            page.goto(url, timeout=20000, wait_until="domcontentloaded")
            page.wait_for_timeout(2500)  # let JS render
            html = page.content()
            browser.close()
            return html
    except Exception as e:
        logger.error(f"Playwright failed for {url}: {e}")
        return None


# ── SINGLE PAGE META EXTRACTION ───────────────────────────────────────────────

def extract_meta(soup: BeautifulSoup, base_url: str) -> dict:
    """
    Extract a single item's title, description, image from a product detail page.
    Uses Open Graph tags first, falls back to HTML heuristics.
    """
    return {
        "title":       _extract_title(soup),
        "description": _extract_description(soup),
        "image_url":   _extract_image(soup, base_url),
    }


def _extract_title(soup: BeautifulSoup) -> str:
    # 1. Open Graph
    og = soup.find("meta", property="og:title")
    if og and og.get("content", "").strip():
        return og["content"].strip()

    # 2. <h1>
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        return h1.get_text(strip=True)

    # 3. <h2>
    h2 = soup.find("h2")
    if h2 and h2.get_text(strip=True):
        return h2.get_text(strip=True)

    # 4. <title> tag
    title_tag = soup.find("title")
    if title_tag:
        return title_tag.get_text(strip=True)

    return ""


def _extract_description(soup: BeautifulSoup) -> str:
    # 1. Open Graph
    og = soup.find("meta", property="og:description")
    if og and og.get("content", "").strip():
        return og["content"].strip()

    # 2. meta[name="description"]
    meta = soup.find("meta", attrs={"name": "description"})
    if meta and meta.get("content", "").strip():
        return meta["content"].strip()

    # 3. First <p> that has reasonable length (not a nav/footer blurb)
    for p in soup.find_all("p"):
        text = p.get_text(strip=True)
        if 30 < len(text) < 500:
            return text

    return ""


def _extract_image(soup: BeautifulSoup, base_url: str) -> str:
    # 1. Open Graph image (usually the best quality)
    og = soup.find("meta", property="og:image")
    if og and og.get("content", "").strip():
        return urljoin(base_url, og["content"].strip())

    # 2. Largest <img> by width/height attributes
    best_img = None
    best_size = 0
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or img.get("data-lazy-src")
        if not src:
            continue
        try:
            w = int(img.get("width", 0))
            h = int(img.get("height", 0))
            size = w * h
        except (ValueError, TypeError):
            size = 0
        if size > best_size:
            best_size = size
            best_img = src

    if best_img:
        return urljoin(base_url, best_img)

    # 3. First <img> with any src
    first = soup.find("img", src=True)
    if first:
        return urljoin(base_url, first["src"])

    return ""


# ── PRODUCT CARD LIST EXTRACTION ──────────────────────────────────────────────

# Common CSS class fragments that indicate a product card wrapper
CARD_HINTS = [
    "product", "item", "card", "tile", "listing",
    "grid-item", "product-card", "product-item",
]

# Common class fragments to skip (nav, footer, banner, etc.)
SKIP_HINTS = [
    "nav", "footer", "header", "banner", "sidebar",
    "breadcrumb", "cookie", "popup", "modal", "cart",
]


def extract_cards(soup: BeautifulSoup, base_url: str) -> list[dict]:
    """
    Attempt to detect and extract all product cards on a listing page.
    Returns a list of dicts with title, description, image_url, source_url.
    Falls back to extracting all unique images with their nearby text.
    """
    cards = _extract_by_card_heuristic(soup, base_url)

    if len(cards) < 2:
        # Fallback: grab all images with nearby headings
        logger.warning("Card heuristic found < 2 items, falling back to image sweep.")
        cards = _extract_by_image_sweep(soup, base_url)

    # Deduplicate by image_url
    seen = set()
    unique = []
    for card in cards:
        key = card.get("image_url", "")
        if key and key not in seen:
            seen.add(key)
            unique.append(card)

    return unique


def _extract_by_card_heuristic(soup: BeautifulSoup, base_url: str) -> list[dict]:
    """Find repeated container elements that look like product cards."""
    candidates = []

    for tag in soup.find_all(True):
        classes = " ".join(tag.get("class", [])).lower()
        tag_id   = tag.get("id", "").lower()
        combined = classes + " " + tag_id

        # Must hint at product
        if not any(hint in combined for hint in CARD_HINTS):
            continue

        # Must not be nav/footer noise
        if any(skip in combined for skip in SKIP_HINTS):
            continue

        # Must contain an image
        img = tag.find("img")
        if not img:
            continue

        src = (
            img.get("src")
            or img.get("data-src")
            or img.get("data-lazy-src")
            or ""
        )
        if not src:
            continue

        title = ""
        for h in ["h1", "h2", "h3", "h4", "span", "a"]:
            el = tag.find(h)
            if el and el.get_text(strip=True):
                title = el.get_text(strip=True)
                break

        desc = ""
        p = tag.find("p")
        if p:
            desc = p.get_text(strip=True)[:300]

        link = tag.find("a", href=True)
        source_url = urljoin(base_url, link["href"]) if link else base_url

        candidates.append({
            "title":       title,
            "description": desc,
            "image_url":   urljoin(base_url, src),
            "source_url":  source_url,
        })

    return candidates


def _extract_by_image_sweep(soup: BeautifulSoup, base_url: str) -> list[dict]:
    """
    Fallback: collect every meaningful image on the page
    with whatever text is near it.
    """
    results = []
    for img in soup.find_all("img"):
        src = (
            img.get("src")
            or img.get("data-src")
            or img.get("data-lazy-src")
            or ""
        )
        if not src or src.startswith("data:"):
            continue

        # Skip tiny icons (if dimensions given)
        try:
            w = int(img.get("width", 9999))
            h = int(img.get("height", 9999))
            if w < 100 or h < 100:
                continue
        except (ValueError, TypeError):
            pass

        # Try to find title from alt text or nearest heading
        title = img.get("alt", "").strip()
        if not title:
            parent = img.parent
            for h in ["h1", "h2", "h3", "h4"]:
                heading = parent.find(h) if parent else None
                if heading:
                    title = heading.get_text(strip=True)
                    break

        # Nearest anchor for source_url
        anchor = img.find_parent("a")
        source_url = urljoin(base_url, anchor["href"]) if anchor and anchor.get("href") else base_url

        results.append({
            "title":       title,
            "description": "",
            "image_url":   urljoin(base_url, src),
            "source_url":  source_url,
        })

    return results