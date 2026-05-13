"""Step 3: Pagination logic for handling multi-page scraping."""


def get_next_page_url(html_content, current_url):
    """
    Extract the next page URL from current page.
    
    Args:
        html_content (str): HTML content of current page
        current_url (str): URL of current page
        
    Returns:
        str or None: URL of next page if exists, None otherwise
    """
    pass


def handle_pagination(start_url, max_pages=None):
    """
    Generator to handle pagination across multiple pages.
    
    Args:
        start_url (str): Starting URL for pagination
        max_pages (int, optional): Maximum number of pages to process
        
    Yields:
        str: URLs to scrape
    """
    pass
