# Image Scraper — FYP Dataset Tool

A two-phase image dataset collection tool built with Streamlit.

## Setup

```bash
pip install -r requirements.txt
playwright install chromium
```

## Run

```bash
streamlit run ui/app.py
```

## Workflow

### Phase 1 — Scrape
1. Go to the **Scrape** page
2. Paste a product/category page URL
3. Enter a garment category (e.g. "shalwar kameez")
4. Click **Start Scraping**
5. Review the results table
6. Download the CSV

### Phase 2 — Download
1. Open the CSV, review and clean rows
2. Go to the **Download** page
3. Upload the reviewed CSV
4. Click **Start Downloading**
5. Images saved to `output/images/{category}/`
6. Check `failed.log` for any errors

## Project Structure

```
image-scraper/
├── scraper/
│   ├── parser.py       # Heuristic HTML parsing (title, desc, image)
│   ├── paginator.py    # Pagination detection and following
│   └── downloader.py   # Image downloading and format handling
├── ui/
│   └── app.py          # Streamlit interface
├── output/
│   └── images/         # Downloaded images saved here
├── requirements.txt
└── README.md
```