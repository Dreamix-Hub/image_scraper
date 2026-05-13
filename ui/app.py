"""
app.py
------
Streamlit UI for the two-phase image scraper.

Pages:
  🔍 Scrape  — paste URL, set category, scrape & export CSV
  📥 Download — upload reviewed CSV, download images
"""

import streamlit as st
import pandas as pd
import threading
import time
import logging
import os
from pathlib import Path
from io import StringIO

from scraper.paginator import scrape_all_pages
from scraper.downloader import download_from_csv

# ── LOGGING SETUP ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

# ── PAGE CONFIG ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Image Scraper",
    page_icon="🖼️",
    layout="wide",
)

# ── SIDEBAR NAV ───────────────────────────────────────────────────────────────
st.sidebar.title("🖼️ Image Scraper")
st.sidebar.caption("FYP Dataset Collection Tool")
st.sidebar.divider()

page = st.sidebar.radio(
    "Navigate",
    ["🔍 Scrape", "📥 Download"],
    label_visibility="collapsed",
)

st.sidebar.divider()
output_folder = st.sidebar.text_input(
    "Output folder",
    value="./output",
    help="Root folder where images and CSVs are saved.",
)
output_path = Path(output_folder)
output_path.mkdir(parents=True, exist_ok=True)


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 1 — SCRAPE
# ══════════════════════════════════════════════════════════════════════════════

if page == "🔍 Scrape":
    st.title("🔍 Scrape Page")
    st.caption("Paste a product listing URL, set a category, and the scraper will extract image links, titles, and descriptions.")

    st.divider()

    # ── INPUTS ────────────────────────────────────────────────────────────────
    col1, col2 = st.columns([3, 1])

    with col1:
        url = st.text_input(
            "Page URL",
            placeholder="https://example.com/category/kurta",
        )
    with col2:
        category = st.text_input(
            "Garment Category",
            placeholder="e.g. shalwar kameez",
        )

    col3, col4 = st.columns([2, 1])
    with col3:
        csv_filename = st.text_input(
            "CSV filename",
            value="results.csv",
            help="Saved inside your output folder.",
        )
    with col4:
        follow_pagination = st.checkbox("Follow pagination", value=True)
        max_pages = st.number_input("Max pages", min_value=1, max_value=100, value=20)

    st.divider()

    # ── SCRAPE BUTTON ─────────────────────────────────────────────────────────
    start = st.button("🚀 Start Scraping", use_container_width=True, type="primary")

    if start:
        if not url.strip():
            st.error("Please enter a URL.")
        elif not category.strip():
            st.error("Please enter a garment category.")
        else:
            st.divider()

            # Live progress placeholders
            status_text  = st.empty()
            progress_bar = st.progress(0)
            stats_cols   = st.columns(3)
            pages_box    = stats_cols[0].empty()
            items_box    = stats_cols[1].empty()
            dedup_box    = stats_cols[2].empty()

            pages_scraped = [0]
            items_found   = [0]

            def progress_callback(page_num, current_url, total_items):
                pages_scraped[0] = page_num
                items_found[0]   = total_items
                status_text.info(f"📄 Scraping page {page_num} — {current_url}")
                pages_box.metric("Pages scraped", page_num)
                items_box.metric("Items found", total_items)

            with st.spinner("Scraping in progress..."):
                try:
                    results = scrape_all_pages(
                        start_url=url.strip(),
                        category=category.strip(),
                        follow_pagination=follow_pagination,
                        max_pages=int(max_pages),
                        progress_callback=progress_callback,
                    )
                except Exception as e:
                    st.error(f"Scraping failed: {e}")
                    st.stop()

            progress_bar.progress(100)

            if not results:
                st.warning("No items found. The page may be JS-rendered — the scraper will retry with Playwright automatically on the next run, or try a different URL.")
            else:
                # ── SUCCESS ───────────────────────────────────────────────────
                status_text.success(f"✅ Done! Found {len(results)} items across {pages_scraped[0]} page(s).")
                dedup_box.metric("After dedup", len(results))

                df = pd.DataFrame(results)

                st.divider()
                st.subheader("Preview")
                st.dataframe(
                    df,
                    use_container_width=True,
                    column_config={
                        "image_url":  st.column_config.LinkColumn("Image URL"),
                        "source_url": st.column_config.LinkColumn("Source URL"),
                    },
                )

                # ── SAVE & DOWNLOAD CSV ───────────────────────────────────────
                csv_save_path = output_path / csv_filename
                df.to_csv(csv_save_path, index=False)
                st.caption(f"💾 Saved to `{csv_save_path}`")

                csv_bytes = df.to_csv(index=False).encode("utf-8")
                st.download_button(
                    label="⬇️ Download CSV",
                    data=csv_bytes,
                    file_name=csv_filename,
                    mime="text/csv",
                    use_container_width=True,
                )

                st.info("💡 Review the CSV, remove unwanted rows, then go to the **📥 Download** page.")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 2 — DOWNLOAD
# ══════════════════════════════════════════════════════════════════════════════

elif page == "📥 Download":
    st.title("📥 Download Images")
    st.caption("Upload your reviewed CSV and the scraper will download all images into organized folders.")

    st.divider()

    # ── FILE UPLOAD ───────────────────────────────────────────────────────────
    uploaded_file = st.file_uploader("Upload reviewed CSV", type=["csv"])

    if uploaded_file:
        df = pd.read_csv(uploaded_file, dtype=str).fillna("")

        # ── VALIDATE COLUMNS ──────────────────────────────────────────────────
        required = {"id", "title", "category", "image_url", "local_path"}
        missing  = required - set(df.columns)

        if missing:
            st.error(f"CSV is missing required columns: `{missing}`")
            st.stop()

        # ── STATS ─────────────────────────────────────────────────────────────
        st.divider()
        st.subheader("CSV Summary")

        total       = len(df)
        already_done = df["local_path"].apply(
            lambda p: bool(p.strip()) and Path(p.strip()).exists()
        ).sum()
        no_url      = df["image_url"].apply(lambda u: not u.strip()).sum()
        pending     = total - already_done - no_url

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total rows",       total)
        c2.metric("Pending",          pending)
        c3.metric("Already downloaded", already_done)
        c4.metric("Empty URL",        no_url)

        # ── PREVIEW ───────────────────────────────────────────────────────────
        with st.expander("Preview first 10 rows"):
            st.dataframe(df.head(10), use_container_width=True)

        st.divider()

        if pending == 0:
            st.success("✅ Nothing to download — all rows are already done.")
        else:
            # ── DOWNLOAD BUTTON ───────────────────────────────────────────────
            start_dl = st.button(
                f"🚀 Start Downloading ({pending} images)",
                use_container_width=True,
                type="primary",
            )

            if start_dl:
                # Save uploaded CSV to output folder so downloader can update it
                csv_save_path = output_path / uploaded_file.name
                df.to_csv(csv_save_path, index=False)

                st.divider()

                # Live progress placeholders
                status_text   = st.empty()
                progress_bar  = st.progress(0)
                stat_cols     = st.columns(4)
                dl_box        = stat_cols[0].empty()
                skip_box      = stat_cols[1].empty()
                fail_box      = stat_cols[2].empty()
                current_box   = stat_cols[3].empty()

                dl_count   = [0]
                skip_count = [0]
                fail_count = [0]

                def progress_callback(current, total, filename, status):
                    pct = int((current / total) * 100) if total else 0
                    progress_bar.progress(pct)

                    if status == "downloading":
                        dl_count[0] += 1
                        status_text.info(f"⬇️  Downloading `{filename}`")
                    elif status == "skipped":
                        skip_count[0] += 1
                        status_text.info(f"⏭️  Skipped `{filename}` (already exists)")
                    elif status == "failed":
                        fail_count[0] += 1
                        status_text.warning(f"❌  Failed `{filename}`")
                    elif status == "no_url":
                        status_text.info(f"⚠️  No URL for row `{filename}`")

                    dl_box.metric("Downloaded",  dl_count[0])
                    skip_box.metric("Skipped",   skip_count[0])
                    fail_box.metric("Failed",    fail_count[0])
                    current_box.metric("Progress", f"{current}/{total}")

                with st.spinner("Downloading images..."):
                    try:
                        summary = download_from_csv(
                            csv_path=csv_save_path,
                            output_folder=output_path / "images",
                            progress_callback=progress_callback,
                        )
                    except Exception as e:
                        st.error(f"Download failed: {e}")
                        st.stop()

                progress_bar.progress(100)

                # ── SUMMARY ───────────────────────────────────────────────────
                st.divider()
                st.subheader("Summary")

                if summary["failed"] == 0:
                    st.success(f"✅ All done! {summary['downloaded']} image(s) downloaded successfully.")
                else:
                    st.warning(
                        f"Done with some failures — "
                        f"{summary['downloaded']} downloaded, "
                        f"{summary['failed']} failed."
                    )

                col_a, col_b = st.columns(2)
                with col_a:
                    # Download updated CSV (with local_path filled)
                    updated_df  = pd.read_csv(csv_save_path, dtype=str).fillna("")
                    updated_csv = updated_df.to_csv(index=False).encode("utf-8")
                    st.download_button(
                        label="⬇️ Download Updated CSV",
                        data=updated_csv,
                        file_name=f"updated_{uploaded_file.name}",
                        mime="text/csv",
                        use_container_width=True,
                    )

                with col_b:
                    # Download failed.log if it exists
                    if summary.get("log_path") and Path(summary["log_path"]).exists():
                        log_bytes = Path(summary["log_path"]).read_bytes()
                        st.download_button(
                            label="📋 Download failed.log",
                            data=log_bytes,
                            file_name="failed.log",
                            mime="text/plain",
                            use_container_width=True,
                        )
                    else:
                        st.info("No failures logged.")