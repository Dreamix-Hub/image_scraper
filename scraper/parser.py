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
        with httpx.Client(
            headers=HEADERS,
            follow_redirects=True,
            timeout=timeout,
            http2=False,  # Disable HTTP/2 to avoid some protocol errors
        ) as client:
            response = client.get(url)
            response.raise_for_status()
            return response.text
    except Exception as e:
        logger.debug(f"httpx fetch failed for {url}: {e}")
        return None


def _needs_javascript(html: str) -> bool:
    """
    Detect if HTML likely needs JavaScript rendering.
    Returns True if the page appears to be JS-heavy.
    """
    if not html:
        return True
    
    # Check for signs of JS-rendered content
    js_indicators = [
        "react", "vue", "angular", "next.js", "nuxt",  # Framework indicators
        "data-react", "data-vue", "__NEXT_DATA__",      # React/Vue specific
        "<noscript>", "window.location", "window.fetch", # JS dependency markers
        "json-ld", "schema.org",                        # Structured data (often lazy-loaded)
    ]
    
    html_lower = html.lower()
    for indicator in js_indicators:
        if indicator in html_lower:
            logger.debug(f"Detected JS indicator: {indicator}")
            return True
    
    # Check if there are very few actual product elements
    soup = BeautifulSoup(html, "lxml")
    product_elements = soup.find_all(class_=lambda x: x and any(
        keyword in x.lower() for keyword in ["product", "item", "card", "listing", "tile"]
    ))
    
    img_tags = soup.find_all("img", src=True)
    
    if len(product_elements) == 0 and len(img_tags) < 3:
        logger.debug("Very few product elements or images found, likely needs JS rendering")
        return True
    
    return False


def fetch_html_playwright(url: str) -> str | None:
    """
    Fallback: fetch HTML via headless Chromium (for JS-rendered pages).
    Waits for network idle, scrolls to trigger lazy loading, and waits for content.
    Includes anti-bot detection measures.
    """
    try:
        from playwright.sync_api import sync_playwright
        
        logger.info(f"Loading {url} with Playwright...")
        
        with sync_playwright() as p:
            # Launch with anti-detection arguments
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-web-resources",
                    "--disable-features=IsolateOrigins,site-per-process",
                ]
            )
            
            # Create context with realistic settings
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 1080},
                extra_http_headers=HEADERS,
                ignore_https_errors=True,
            )
            
            page = context.new_page()
            
            # Add script to hide headless indicators
            page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined,
                });
                window.chrome = { runtime: {} };
            """)
            
            try:
                # Try to navigate with different wait strategies
                page.goto(url, timeout=20000, wait_until="domcontentloaded")
                logger.info(f"Page loaded for {url}")
            except Exception as nav_err:
                logger.warning(f"Navigation error: {nav_err}, retrying with different strategy...")
                try:
                    # Retry with simpler approach
                    page.goto(url, timeout=15000)
                except Exception as retry_err:
                    logger.error(f"Failed to navigate after retry: {retry_err}")
                    browser.close()
                    return None
            
            # Wait for network to settle
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                logger.debug("Network idle timeout, continuing anyway...")
            
            # Scroll to trigger lazy loading
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(1500)
            
            # Scroll back to top to capture all content
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(1000)
            
            # Try to wait for product containers
            try:
                page.wait_for_selector(
                    "img[src], img[data-src], [class*='product'], [class*='item']",
                    timeout=5000,
                )
                logger.info(f"Content detected for {url}")
            except Exception:
                logger.debug("Product selectors timeout, proceeding anyway...")
            
            # Get final HTML
            html = page.content()
            
            # Cleanup
            page.close()
            context.close()
            browser.close()
            
            logger.info(f"Playwright successfully fetched {url} ({len(html)} bytes)")
            return html
            
    except ImportError:
        logger.error("Playwright not installed or browser not available")
        return None
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