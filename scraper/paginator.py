"""
paginator.py
------------
Detects and follows pagination on any listing page.

Detection priority:
  1. <a rel="next"> — semantic, most reliable
  2. <a> whose text contains "next", "›", "»", "→"
  3. ?page=N  or  ?p=N  URL pattern — increments until page returns no new cards

Deep scrape mode:
  After collecting listing cards, visits each product's source_url and
  extracts all gallery images. Each image becomes its own CSV row with
  an image_index column (0 = main, 1+ = gallery extras).
"""

import re
import time
import logging
from urllib.parse import urljoin, urlparse, parse_qs, urlencode, urlunparse

from bs4 import BeautifulSoup
from scraper.parser import fetch_smart, extract_cards, extract_gallery_images

logger = logging.getLogger(__name__)

NEXT_TEXT_PATTERNS = re.compile(
    r"^\s*(next|›|»|→|next\s*page|older\s*posts?|load\s*more)\s*$",
    re.IGNORECASE,
)
PAGE_QUERY_KEYS = ["page", "p", "pg", "paged", "pagenumber", "currentpage"]


# ── NEXT LINK DETECTION ───────────────────────────────────────────────────────

def find_next_url(soup: BeautifulSoup, current_url: str) -> str | None:
    rel_next = soup.find("a", rel=lambda r: r and "next" in r)
    if rel_next and rel_next.get("href"):
        return urljoin(current_url, rel_next["href"])

    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True)
        aria = a.get("aria-label", "")
        if NEXT_TEXT_PATTERNS.match(text) or NEXT_TEXT_PATTERNS.match(aria):
            href = a["href"]
            if href and href not in ("#", "javascript:void(0)", "javascript:;"):
                return urljoin(current_url, href)

    incremented = _increment_page_param(current_url)
    if incremented:
        return incremented

    return None


def _increment_page_param(url: str) -> str | None:
    parsed = urlparse(url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    for key in PAGE_QUERY_KEYS:
        if key in params:
            try:
                current_page = int(params[key][0])
                params[key]  = [str(current_page + 1)]
                new_query    = urlencode(params, doseq=True)
                return urlunparse(parsed._replace(query=new_query))
            except ValueError:
                continue
    return None


# ── DEEP SCRAPE: visit each product page ─────────────────────────────────────

def _scrape_product_gallery(source_url: str, main_image_url: str) -> list[str]:
    """
    Visit a product detail page and return all gallery image URLs.
    The main listing image is always index 0 — gallery extras follow.
    Deduplicates against the main image.
    """
    html = fetch_smart(source_url)
    if not html:
        logger.warning(f"Could not fetch product page: {source_url}")
        return [main_image_url] if main_image_url else []

    soup   = BeautifulSoup(html, "lxml")
    images = extract_gallery_images(soup, source_url)

    # Ensure the main listing image is always present as index 0
    if main_image_url and main_image_url not in images:
        images.insert(0, main_image_url)
    elif main_image_url in images:
        # Move it to front
        images.remove(main_image_url)
        images.insert(0, main_image_url)

    return images if images else ([main_image_url] if main_image_url else [])


# ── FULL CRAWL ────────────────────────────────────────────────────────────────

def scrape_all_pages(
    start_url: str,
    category: str,
    follow_pagination: bool = True,
    max_pages: int = 20,
    deep_scrape: bool = False,
    progress_callback=None,
    stop_event=None,
) -> list[dict]:
    """
    Scrape a listing page and optionally follow pagination.

    Args:
        start_url:          First page URL.
        category:           Garment category label added to every row.
        follow_pagination:  Follow next-page links.
        max_pages:          Safety cap on pagination.
        deep_scrape:        If True, visit each product page to collect all
                            gallery images. Each image becomes its own CSV row.
        progress_callback:  callable(page_num, status_msg, items_so_far)
        stop_event:         threading.Event — when set, halts after current page.

    Returns:
        List of row dicts ready for CSV export.
    """
    raw_cards    = []
    seen_urls    = set()
    visited_pages = set()
    current_url  = start_url
    page_num     = 1

    # ── Phase 1: scrape listing pages ─────────────────────────────────────────
    while current_url and page_num <= max_pages:
        if current_url in visited_pages:
            logger.warning(f"Already visited {current_url}, stopping.")
            break
        visited_pages.add(current_url)

        logger.info(f"Scraping listing page {page_num}: {current_url}")

        html = fetch_smart(current_url)
        if not html:
            logger.error(f"Could not fetch page {page_num}: {current_url}")
            break

        soup       = BeautifulSoup(html, "lxml")
        page_cards = extract_cards(soup, current_url)

        new_cards = []
        for card in page_cards:
            img_url = card.get("image_url", "")
            if img_url and img_url not in seen_urls:
                seen_urls.add(img_url)
                new_cards.append(card)

        if not new_cards:
            logger.info(f"No new items on page {page_num}, stopping pagination.")
            break

        raw_cards.extend(new_cards)

        if progress_callback:
            progress_callback(page_num, f"Listing page {page_num}", len(raw_cards))

        if stop_event and stop_event.is_set():
            logger.info("Stop signal received — halting listing phase.")
            break

        if not follow_pagination:
            break

        next_url = find_next_url(soup, current_url)
        if not next_url or next_url == current_url:
            break

        current_url = next_url
        page_num   += 1

    logger.info(f"Listing phase done: {len(raw_cards)} products across {page_num} page(s).")

    # ── Phase 2: deep scrape product pages ────────────────────────────────────
    result = []
    row_id = 1

    for card_idx, card in enumerate(raw_cards):
        if stop_event and stop_event.is_set():
            logger.info("Stop signal received — halting deep scrape phase.")
            break

        title       = card.get("title", "")
        description = card.get("description", "")
        source_url  = card.get("source_url", "")
        main_image  = card.get("image_url", "")

        if deep_scrape and source_url and source_url != start_url:
            if progress_callback:
                progress_callback(
                    page_num,
                    f"Deep scraping product {card_idx + 1}/{len(raw_cards)}: {title or source_url}",
                    row_id - 1,
                )

            gallery = _scrape_product_gallery(source_url, main_image)
            time.sleep(0.5)  # polite delay between product page requests

            for img_index, img_url in enumerate(gallery):
                result.append({
                    "id":          str(row_id).zfill(4),
                    "title":       title,
                    "description": description,
                    "category":    category,
                    "image_url":   img_url,
                    "image_index": img_index,   # 0 = main, 1+ = gallery
                    "source_url":  source_url,
                    "local_path":  "",
                })
                row_id += 1
        else:
            # Shallow mode — one row per product
            result.append({
                "id":          str(row_id).zfill(4),
                "title":       title,
                "description": description,
                "category":    category,
                "image_url":   main_image,
                "image_index": 0,
                "source_url":  source_url,
                "local_path":  "",
            })
            row_id += 1

    logger.info(f"Total rows: {len(result)} (deep_scrape={deep_scrape})")
    return result