"""Step 4: Image downloading and storage."""

import os


def download_image(image_url, output_dir="output/images"):
    """
    Download an image from URL and save to disk.
    
    Args:
        image_url (str): URL of image to download
        output_dir (str): Directory to save images
        
    Returns:
        str or None: Path to downloaded image if successful, None otherwise
    """
    pass


def batch_download_images(image_urls, output_dir="output/images", max_workers=4):
    """
    Download multiple images concurrently.
    
    Args:
        image_urls (list): List of image URLs to download
        output_dir (str): Directory to save images
        max_workers (int): Number of concurrent download threads
        
    Returns:
        dict: Dictionary with download results and statistics
    """
    pass
