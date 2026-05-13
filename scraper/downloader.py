"""
downloader.py
-------------
Phase 2: reads a reviewed CSV and downloads images to organized folders.

Features:
  - Skips already-downloaded rows (resume-safe via local_path column)
  - Skips rows with empty image_url
  - Handles WebP → JPG conversion
  - Strips query params to detect true file extension
  - Falls back to Content-Type header for extension
  - Logs all failures to failed.log (never crashes on a single bad image)
  - Saves as {id}_{slugified-title}.jpg inside output_folder/{category}/
  - Updates local_path column in the DataFrame after each download
"""

import os
import logging
import httpx
import pandas as pd
from pathlib import Path
from urllib.parse import urlparse, urljoin
from slugify import slugify
from PIL import Image
import io

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}

# Extensions we accept directly without conversion
VALID_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".avif", ".gif"}

# Mapping from Content-Type → file extension
CONTENT_TYPE_MAP = {
    "image/jpeg":  ".jpg",
    "image/png":   ".png",
    "image/webp":  ".webp",
    "image/avif":  ".avif",
    "image/gif":   ".gif",
    "image/bmp":   ".bmp",
    "image/tiff":  ".tiff",
}


# ── HELPERS ───────────────────────────────────────────────────────────────────

def _guess_extension(url: str, content_type: str = "") -> str:
    """
    Determine file extension from URL path first, then Content-Type header.
    Always returns a dot-prefixed string like '.jpg'. Defaults to '.jpg'.
    """
    # Strip query string and get the path
    path = urlparse(url).path.lower()
    ext = Path(path).suffix  # e.g. ".webp", ".jpg"

    if ext in VALID_EXTENSIONS:
        return ext

    # Fallback to Content-Type
    for ct, extension in CONTENT_TYPE_MAP.items():
        if ct in content_type.lower():
            return extension

    return ".jpg"  # safe default


def _make_filename(row_id: str, title: str, ext: str) -> str:
    """
    Build a clean filename: 0001_white-kurta.jpg
    Slugify the title to remove special chars and spaces.
    """
    slug = slugify(title or "untitled", max_length=60, separator="-")
    return f"{row_id}_{slug}{ext}"


def _ensure_folder(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _convert_to_jpg(image_bytes: bytes) -> bytes:
    """Convert any Pillow-supported format to JPEG bytes."""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


# ── SINGLE IMAGE DOWNLOAD ─────────────────────────────────────────────────────

def download_image(
    image_url: str,
    save_path: Path,
    timeout: int = 20,
) -> bool:
    """
    Download a single image to save_path.
    Converts WebP/AVIF/non-standard formats to JPEG.
    Returns True on success, False on failure.
    """
    try:
        with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=timeout) as client:
            response = client.get(image_url)
            response.raise_for_status()

            content_type = response.headers.get("content-type", "")
            ext = _guess_extension(image_url, content_type)
            image_bytes = response.content

            # Convert non-JPG formats to JPG for dataset consistency
            if ext not in {".jpg", ".jpeg"}:
                try:
                    image_bytes = _convert_to_jpg(image_bytes)
                except Exception as conv_err:
                    logger.warning(f"Conversion failed for {image_url}: {conv_err}, saving as-is.")
                    # Save with original extension instead
                    save_path = save_path.with_suffix(ext)

            # Validate it's actually a real image before saving
            Image.open(io.BytesIO(image_bytes)).verify()

            # Re-open after verify (verify closes the file)
            final_bytes = image_bytes
            save_path.write_bytes(final_bytes)
            return True

    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP {e.response.status_code} for {image_url}")
    except httpx.RequestError as e:
        logger.error(f"Request error for {image_url}: {e}")
    except Exception as e:
        logger.error(f"Unexpected error for {image_url}: {e}")

    return False


# ── BATCH DOWNLOAD FROM CSV ───────────────────────────────────────────────────

def download_from_csv(
    csv_path: str | Path,
    output_folder: str | Path,
    progress_callback=None,
    image_limit: int | None = None,
) -> dict:
    """
    Read a CSV file and download all images with an empty local_path.

    Args:
        csv_path:          Path to the reviewed CSV file.
        output_folder:     Root folder. Images saved to {output_folder}/{category}/
        progress_callback: Optional callable(current, total, filename, status)
                           where status is 'downloading' | 'skipped' | 'failed' | 'no_url'
        image_limit:       Max images to download this run. None = no limit.

    Returns:
        Summary dict with counts: total, downloaded, skipped, failed, no_url
    """
    csv_path      = Path(csv_path)
    output_folder = Path(output_folder)
    log_path      = output_folder / "failed.log"

    # Load CSV
    df = pd.read_csv(csv_path, dtype=str).fillna("")

    # Ensure required columns exist
    required = {"id", "title", "category", "image_url", "local_path"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"CSV is missing required columns: {missing}")

    total      = len(df)
    downloaded = 0
    skipped    = 0
    failed     = 0
    no_url     = 0
    fail_lines = []

    for idx, row in df.iterrows():
        row_id    = str(row.get("id", idx)).strip()
        title     = str(row.get("title", "")).strip()
        category  = str(row.get("category", "uncategorized")).strip()
        image_url = str(row.get("image_url", "")).strip()
        local_path = str(row.get("local_path", "")).strip()

        current = idx + 1  # 1-based progress

        # ── Skip: already downloaded ─────────────────────────────────────────
        if local_path and Path(local_path).exists():
            skipped += 1
            if progress_callback:
                progress_callback(current, total, Path(local_path).name, "skipped")
            continue

        # ── Skip: no URL ─────────────────────────────────────────────────────
        if not image_url:
            no_url += 1
            if progress_callback:
                progress_callback(current, total, f"row {row_id}", "no_url")
            continue

        # ── Stop if image limit reached ───────────────────────────────────────
        if image_limit is not None and downloaded >= image_limit:
            logger.info(f"Image limit of {image_limit} reached, stopping.")
            break

        # ── Build save path ───────────────────────────────────────────────────
        category_folder = output_folder / slugify(category, separator="-")
        _ensure_folder(category_folder)

        filename  = _make_filename(row_id, title, ".jpg")  # always save as .jpg
        save_path = category_folder / filename

        if progress_callback:
            progress_callback(current, total, filename, "downloading")

        # ── Download ──────────────────────────────────────────────────────────
        success = download_image(image_url, save_path)

        if success:
            downloaded += 1
            df.at[idx, "local_path"] = str(save_path)
        else:
            failed += 1
            fail_lines.append(
                f"[FAILED] id={row_id} | url={image_url} | title={title}\n"
            )
            if progress_callback:
                progress_callback(current, total, filename, "failed")

    # ── Save updated CSV (local_path now filled) ──────────────────────────────
    df.to_csv(csv_path, index=False)
    logger.info(f"CSV updated with local_path values: {csv_path}")

    # ── Write failed.log ──────────────────────────────────────────────────────
    if fail_lines:
        _ensure_folder(output_folder)
        with open(log_path, "a", encoding="utf-8") as f:
            f.writelines(fail_lines)
        logger.info(f"Failures logged to {log_path}")

    summary = {
        "total":       total,
        "downloaded":  downloaded,
        "skipped":     skipped,
        "failed":      failed,
        "no_url":      no_url,
        "limit":       image_limit,
        "log_path":    str(log_path) if fail_lines else None,
    }

    logger.info(f"Download complete: {summary}")
    return summary