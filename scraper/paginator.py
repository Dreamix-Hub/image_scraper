"""
paginator.py
------------
Detects and follows pagination on any listing page.

Detection priority:
  1. <a rel="next"> — semantic, most reliable
  2. <a> whose text contains "next", "›", "»", "→"
  3. ?page=N  or  ?p=N  URL pattern — increments until page returns no new cards
"""

import re
import logging
from urllib.parse import urljoin, urlparse, parse_qs, urlencode, urlunparse

from bs4 import BeautifulSoup
from scraper.parser import fetch_html, fetch_html_playwright, extract_cards, _needs_javascript

logger = logging.getLogger(__name__)

# Text patterns that indicate a "next page" link
NEXT_TEXT_PATTERNS = re.compile(
    r"^\s*(next|›|»|→|next\s*page|older\s*posts?|load\s*more)\s*$",
    re.IGNORECASE,
)

# URL query keys commonly used for page numbers
PAGE_QUERY_KEYS = ["page", "p", "pg", "paged", "pagenumber", "currentpage"]


# ── NEXT LINK DETECTION ───────────────────────────────────────────────────────

def find_next_url(soup: BeautifulSoup, current_url: str) -> str | None:
    """
    Try to find the URL of the next page from the current page's HTML.
    Returns None if no next page is found.
    """

    # 1. <a rel="next"> — gold standard
    rel_next = soup.find("a", rel=lambda r: r and "next" in r)
    if rel_next and rel_next.get("href"):
        return urljoin(current_url, rel_next["href"])

    # 2. <a> whose visible text looks like "next"
    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True)
        aria = a.get("aria-label", "")
        if NEXT_TEXT_PATTERNS.match(text) or NEXT_TEXT_PATTERNS.match(aria):
            href = a["href"]
            if href and href not in ("#", "javascript:void(0)", "javascript:;"):
                return urljoin(current_url, href)

    # 3. ?page=N pattern — find current page number, increment it
    incremented = _increment_page_param(current_url)
    if incremented:
        return incremented

    return None


def _increment_page_param(url: str) -> str | None:
    """
    If the URL has a recognizable page query param, return the URL
    with that param incremented by 1. Otherwise return None.
    """
    parsed = urlparse(url)
    params = parse_qs(parsed.query, keep_blank_values=True)

    for key in PAGE_QUERY_KEYS:
        if key in params:
            try:
                current_page = int(params[key][0])
                params[key] = [str(current_page + 1)]
                new_query = urlencode(params, doseq=True)
                new_parsed = parsed._replace(query=new_query)
                return urlunparse(new_parsed)
            except ValueError:
                continue

    return None


# ── FULL CRAWL ────────────────────────────────────────────────────────────────

def scrape_all_pages(
    start_url: str,
    category: str,
    follow_pagination: bool = True,
    max_pages: int = 20,
    progress_callback=None,
    stop_event=None,
) -> list[dict]:
    """
    Scrape a listing page and optionally follow pagination.

    Args:
        start_url:          The first page URL to scrape.
        category:           Garment category label (added to every row).
        follow_pagination:  If False, only scrape the first page.
        max_pages:          Safety cap to avoid infinite loops.
        progress_callback:  Optional callable(page_num, url, items_so_far)
                            called after each page — used to update Streamlit UI.
        stop_event:         Optional threading.Event. When set, the loop exits
                            cleanly after the current page finishes.

    Returns:
        List of dicts: {id, title, description, category, image_url, source_url, local_path}
    """
    all_cards = []
    seen_urls = set()       # deduplicate image_url globally across pages
    visited_pages = set()   # avoid revisiting the same page URL
    current_url = start_url
    page_num = 1

    while current_url and page_num <= max_pages:
        # Guard against redirect loops
        if current_url in visited_pages:
            logger.warning(f"Already visited {current_url}, stopping.")
            break
        visited_pages.add(current_url)

        logger.info(f"Scraping page {page_num}: {current_url}")

        # Fetch HTML — try plain httpx first
        html = fetch_html(current_url)

        # Check if page needs JavaScript rendering
        if html and _needs_javascript(html):
            logger.info(f"Page {page_num} appears to be JS-rendered, using Playwright for better results...")
            html_pw = fetch_html_playwright(current_url)
            if html_pw:
                html = html_pw
        
        # If httpx returned nothing, definitely try Playwright
        if not html:
            logger.warning(f"httpx returned nothing for page {page_num}, trying Playwright...")
            html = fetch_html_playwright(current_url)

        if not html:
            logger.error(f"Could not fetch page {page_num}: {current_url}")
            break

        soup = BeautifulSoup(html, "lxml")

        # Extract cards from this page
        page_cards = extract_cards(soup, current_url)

        # If we found very few cards on first attempt, try Playwright
        # (page might be JS-rendered and httpx didn't capture it fully)
        if len(page_cards) < 2:
            logger.info(f"Found only {len(page_cards)} item(s) with httpx, trying Playwright for better results...")
            html_pw = fetch_html_playwright(current_url)
            if html_pw:
                soup = BeautifulSoup(html_pw, "lxml")
                page_cards_pw = extract_cards(soup, current_url)
                if len(page_cards_pw) > len(page_cards):
                    logger.info(f"Playwright found {len(page_cards_pw)} items (vs {len(page_cards)} from httpx)")
                    page_cards = page_cards_pw

        # Filter out already-seen image URLs
        new_cards = []
        for card in page_cards:
            img_url = card.get("image_url", "")
            if img_url and img_url not in seen_urls:
                seen_urls.add(img_url)
                new_cards.append(card)

        # If this page had zero new items, stop — likely hit a dead end
        if not new_cards:
            logger.info(f"No new items on page {page_num}, stopping pagination.")
            break

        all_cards.extend(new_cards)

        if progress_callback:
            progress_callback(page_num, current_url, len(all_cards))

        # Check stop signal — exit cleanly after this page
        if stop_event and stop_event.is_set():
            logger.info("Stop signal received, halting pagination.")
            break

        # Find next page
        if not follow_pagination:
            break

        next_url = find_next_url(soup, current_url)

        # If next URL is the same as current (some sites do this), stop
        if next_url == current_url:
            logger.info("Next URL same as current, stopping.")
            break

        current_url = next_url
        page_num += 1

    # Attach metadata to every card
    result = []
    for i, card in enumerate(all_cards, start=1):
        result.append({
            "id":          str(i).zfill(4),
            "title":       card.get("title", ""),
            "description": card.get("description", ""),
            "category":    category,
            "image_url":   card.get("image_url", ""),
            "source_url":  card.get("source_url", ""),
            "local_path":  "",          # filled in Phase 2
        })

    logger.info(f"Total items scraped: {len(result)} across {page_num} page(s).")
    return result