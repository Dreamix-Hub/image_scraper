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
        "Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Sec-CH-UA": '"Not_A Brand";v="8", "Chromium";v="124", "Microsoft Edge";v="124"',
    "Sec-CH-UA-Mobile": "?0",
    "Sec-CH-UA-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
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


def _is_js_shell(html: str) -> bool:
    """
    Detect if an HTML response is a JS-rendered shell with no real content.
    Signs: very few <img> tags, almost no visible text, heavy <script> usage.
    """
    if not html:
        return True
    soup = BeautifulSoup(html, "lxml")
    body = soup.body
    if not body:
        return True
    visible_text = body.get_text(separator=" ", strip=True)
    img_count    = len(soup.find_all("img", src=True))
    script_count = len(soup.find_all("script"))
    # Heuristic: very little text + almost no images = JS shell
    return len(visible_text) < 500 and img_count < 3 and script_count > 3


def fetch_html_playwright(url: str, wait_for_selector: str | None = None) -> str | None:
    """
    Fetch HTML via headless Chromium with full JS rendering.

    Improvements over the basic version:
      - Waits for network to go idle (not just DOM ready)
      - Auto-scrolls to trigger lazy-loaded images
      - Tries to dismiss cookie banners
      - Optional: waits for a specific CSS selector before capturing HTML
    """
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"]
            )
            context = browser.new_context(
                viewport={"width": 1440, "height": 900},
                user_agent=HEADERS["User-Agent"],
                extra_http_headers={
                    "Accept-Language": HEADERS["Accept-Language"],
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                    "Accept-Encoding": "gzip, deflate, br",
                    "DNT": "1",
                    "Connection": "keep-alive",
                    "Upgrade-Insecure-Requests": "1"
                },
            )
            page = context.new_page()

            # Block ads/trackers to speed up loading
            page.route(
                "**/{ads,analytics,doubleclick,googletagmanager}**",
                lambda route: route.abort(),
            )

            try:
                page.goto(url, timeout=30000, wait_until="networkidle")
            except Exception as e:
                # networkidle can time out or fail — fall back to domcontentloaded
                logger.warning(f"networkidle failed for {url} ({type(e).__name__}), falling back to domcontentloaded")
                try:
                    page.goto(url, timeout=30000, wait_until="domcontentloaded")
                    page.wait_for_timeout(3000)
                except Exception as e2:
                    # Last resort: just load whatever we can
                    logger.warning(f"domcontentloaded also failed, trying load with timeout...")
                    try:
                        page.goto(url, timeout=15000, wait_until="load")
                    except Exception as e3:
                        logger.error(f"All page load strategies failed for {url}: {e3}")
                        browser.close()
                        return None

            # Try to dismiss cookie consent banners
            for selector in [
                "button:has-text('Accept')",
                "button:has-text('Accept All')",
                "button:has-text('I agree')",
                "[id*='cookie'] button",
                "[class*='cookie'] button",
            ]:
                try:
                    btn = page.locator(selector).first
                    if btn.is_visible(timeout=1000):
                        btn.click(timeout=1000)
                        page.wait_for_timeout(500)
                        break
                except Exception:
                    pass

            # Wait for a specific element if requested
            if wait_for_selector:
                try:
                    page.wait_for_selector(wait_for_selector, timeout=5000)
                except PWTimeout:
                    pass

            # Wait for product elements to appear (common CSS patterns)
            product_selectors = [
                "[class*='product']",
                "[class*='item']",
                "[class*='card']",
                "article",
                "[data-product]",
            ]
            
            for selector in product_selectors:
                try:
                    page.wait_for_selector(selector, timeout=3000)
                    logger.info(f"Found product selector: {selector}")
                    break
                except PWTimeout:
                    continue
            
            # Extra wait to ensure JS finishes rendering
            page.wait_for_timeout(2000)

            # Auto-scroll to trigger lazy-loaded images
            try:
                page.evaluate("""
                    async () => {
                        await new Promise(resolve => {
                            let total = document.body.scrollHeight;
                            let current = 0;
                            const step = 400;
                            const delay = 120;
                            const timer = setInterval(() => {
                                window.scrollBy(0, step);
                                current += step;
                                if (current >= total) {
                                    clearInterval(timer);
                                    window.scrollTo(0, 0);
                                    resolve();
                                }
                            }, delay);
                        });
                    }
                """)
                page.wait_for_timeout(1000)  # brief settle after scroll
            except Exception as e:
                logger.warning(f"Scroll failed for {url}: {e}")

            html = page.content()
            browser.close()
            return html

    except Exception as e:
        logger.error(f"Playwright failed for {url}: {e}")
        return None


def fetch_smart(url: str, force_playwright: bool = False) -> str | None:
    """
    Smart fetcher: tries httpx first, auto-detects JS-shell pages,
    falls back to Playwright when needed.

    Args:
        url:              URL to fetch.
        force_playwright: Skip httpx and go straight to Playwright.
                          Use for known JS-heavy product detail pages.
    """
    if not force_playwright:
        html = fetch_html(url)
        if html and not _is_js_shell(html):
            return html
        logger.info(f"JS shell detected or fetch failed for {url} — switching to Playwright")

    return fetch_html_playwright(url)


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


# ── PRODUCT DETAIL GALLERY EXTRACTION ────────────────────────────────────────

# CSS class/id fragments that wrap product image galleries
GALLERY_HINTS = [
    "gallery", "product-image", "product-photo", "product-media",
    "product-img", "zoom", "lightbox", "swiper", "slider",
    "carousel", "thumbnails", "thumb", "image-viewer",
]

# Attributes that often carry the full-res URL (lazy-load / zoom patterns)
HIRES_ATTRS = [
    "data-zoom-image", "data-large", "data-full",
    "data-original", "data-hi-res", "data-src",
    "data-lazy-src", "data-image", "data-zoom",
    "data-srcset", "srcset",
]


def extract_gallery_images(soup: BeautifulSoup, base_url: str, min_size: int = 200) -> list[str]:
    """
    Extract all product gallery image URLs from a product detail page.

    Strategy:
      1. Look for known gallery container elements by class/id hints
      2. Within those containers collect every <img> with a real src
      3. Also check hi-res data-* attributes (zoom, lazy-load patterns)
      4. Parse srcset to pick the largest available image
      5. Fallback: collect all page <img> tags above min_size threshold
      6. Deduplicate and return ordered list (index 0 = main image)

    Args:
        soup:     Parsed BeautifulSoup of the product detail page.
        base_url: Used to resolve relative URLs.
        min_size: Minimum width or height in px to accept an image (filters icons).

    Returns:
        List of absolute image URLs, deduplicated.
    """
    collected = []
    seen      = set()

    def _add(url: str):
        url = url.strip()
        if url and url not in seen and not url.startswith("data:"):
            seen.add(url)
            collected.append(urljoin(base_url, url))

    def _best_from_srcset(srcset: str) -> str:
        """Parse a srcset string and return the URL with the highest width descriptor."""
        best_url  = ""
        best_w    = 0
        for part in srcset.split(","):
            part = part.strip()
            if not part:
                continue
            tokens = part.split()
            if not tokens:
                continue
            url = tokens[0]
            w   = 0
            if len(tokens) > 1:
                try:
                    w = int(tokens[1].rstrip("w"))
                except ValueError:
                    pass
            if w > best_w:
                best_w   = w
                best_url = url
        return best_url or (tokens[0] if tokens else "")

    def _harvest_imgs(container):
        """Collect all usable image URLs from a container element."""
        for img in container.find_all("img"):
            # Check hi-res / lazy-load data attributes first (higher quality)
            for attr in HIRES_ATTRS:
                val = img.get(attr, "").strip()
                if not val:
                    continue
                if attr in ("srcset", "data-srcset"):
                    best = _best_from_srcset(val)
                    if best:
                        _add(best)
                else:
                    _add(val)

            # Standard src
            src = img.get("src", "").strip()
            if src:
                # Skip if it's a 1×1 tracker or tiny icon by dimensions
                try:
                    w = int(img.get("width",  min_size + 1))
                    h = int(img.get("height", min_size + 1))
                    if w < min_size and h < min_size:
                        continue
                except (ValueError, TypeError):
                    pass
                _add(src)

    # ── Step 1: gallery containers ────────────────────────────────────────────
    gallery_containers = []
    for tag in soup.find_all(True):
        classes  = " ".join(tag.get("class", [])).lower()
        tag_id   = tag.get("id", "").lower()
        combined = classes + " " + tag_id
        if any(hint in combined for hint in GALLERY_HINTS):
            gallery_containers.append(tag)

    for container in gallery_containers:
        _harvest_imgs(container)

    # ── Step 2: fallback — full page sweep if gallery found < 2 images ───────
    if len(collected) < 2:
        _harvest_imgs(soup)

    # ── Step 3: also check <a> tags that link directly to images ─────────────
    IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".avif"}
    for a in soup.find_all("a", href=True):
        href = a["href"].lower().split("?")[0]
        if any(href.endswith(ext) for ext in IMAGE_EXTS):
            _add(a["href"])

    return collected