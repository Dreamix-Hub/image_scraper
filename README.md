# Image Scraper

A powerful two-phase e-commerce product image collection tool built with **Streamlit**. Automatically scrapes product data from e-commerce websites (especially Shopify-based stores), extracts metadata, downloads images, and organizes them by category.

Perfect for building fashion/apparel image datasets with pagination support, deep scraping for gallery images, and intelligent framework detection.

---

## ✨ Features

### Core Scraping
- ✅ **Smart HTML Parsing** — Uses framework-specific patterns first (Shopify Hydrogen), then falls back to generic heuristics
- ✅ **Dual Fetch Strategy** — Fast httpx for initial fetch, Playwright for JavaScript-heavy pages
- ✅ **Automatic Pagination** — Detects and follows `rel="next"` links or page parameters
- ✅ **Deep Scrape Mode** — Visits individual product pages to extract gallery images
- ✅ **Multi-Image Extraction** — Captures main image + all gallery images per product
- ✅ **Metadata Extraction** — Collects title, description, image URL, and product source

### Data Management
- ✅ **CSV Export** — Structured output with columns: `id, title, description, category, image_url, image_index, source_url, local_path`
- ✅ **Image Organization** — Automatically sorts images by category into folders
- ✅ **Progress Tracking** — Real-time status updates during scraping
- ✅ **Error Logging** — Detailed failure logs for debugging

### Web Interface
- ✅ **Two-Page Streamlit App** — Separate pages for Scrape and Download workflows
- ✅ **Real-Time Updates** — Live progress indicator while scraping
- ✅ **Interactive Forms** — URL input, category selection, CSV upload/download
- ✅ **Data Preview** — View scraped results before downloading

### Browser & Network
- ✅ **HTTP Compression Handling** — Properly decompresses gzip and deflate responses
- ✅ **Anti-Detection Headers** — Custom User-Agent and browser fingerprinting
- ✅ **Headless Chromium** — JavaScript rendering with Playwright when needed
- ✅ **Rate Limiting** — Respectful 2-3 second delays between requests

### Supported Platforms
- ✅ **Shopify Stores** — Native support for Hydrogen Design Token patterns
- ✅ **Generic E-Commerce** — Fallback heuristics for other platforms
- ✅ **JavaScript-Rendered Sites** — Automatic fallback to Playwright if httpx detects JS shell

---

## 📦 Installation

### Prerequisites
- Python 3.10 or higher
- pip package manager
- ~500MB disk space (for Chromium browser)

### Step 1: Clone Repository
```bash
git clone https://github.com/Dreamix-Hub/image_scraper.git
cd image-scraper
```

### Step 2: Create Virtual Environment
```bash
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
```

### Step 3: Install Dependencies
```bash
pip install -r requirements.txt
```

### Step 4: Install Chromium Browser
```bash
playwright install chromium
```

This downloads the Chromium browser (~180MB) used for JavaScript rendering.

---

## 🚀 Quick Start

### Start the Application
```bash
streamlit run ui/app.py
```

The app opens at **http://localhost:8502**

### Basic Usage (Two Workflows)

#### Workflow 1: Scrape Products
1. Navigate to **Scrape** tab
2. Enter product URL (e.g., `https://www.junaidjamshed.com/collections/mens-kameez-shalwar`)
3. Enter category name (e.g., `kameez-shalwar`)
4. *(Optional)* Check **Deep Scrape** to extract gallery images from each product page
5. *(Optional)* Adjust **Max Pages** slider (default: 100)
6. *(Optional)* Check **Follow Pagination** to auto-detect and follow next page links
7. Click **🚀 Start Scraping**
8. Wait for completion (progress updates shown in real-time)
9. Review results table
10. Click **Download CSV** to save `results.csv`

#### Workflow 2: Download Images
1. Prepare your CSV (can use output from Workflow 1)
2. Navigate to **Download** tab
3. Upload CSV file
4. Enter filename prefix (e.g., `product_images`)
5. Click **Start Downloading**
6. Images saved to `output/images/{category}/`
7. Check terminal for any failed downloads

---

## 🎛️ Configuration Options

### Scrape Page Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| **URL** | string | - | Product/category page URL to scrape |
| **Category** | string | - | Garment category name (used for file organization) |
| **Follow Pagination** | boolean | `True` | Auto-detect and follow next page links |
| **Max Pages** | int | 100 | Maximum number of pages to scrape |
| **Deep Scrape** | boolean | `False` | Visit product detail pages to extract gallery images |
| **CSV Filename** | string | `results` | Name for exported CSV file (without extension) |

### Download Page Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| **CSV File** | file | - | Uploaded CSV with image URLs and metadata |
| **Filename Prefix** | string | `image` | Prefix for downloaded image files |

---

## 📊 CSV Output Format

After scraping, `output/results.csv` contains:

```
id,title,description,category,image_url,image_index,source_url,local_path
1,"Cream Kameez Shalwar","Plain kameez with matching shalwar","kameez-shalwar","https://...",0,"https://junaidjamshed.com/products/cream-kameez-shalwar-jjksa47600",""
2,"Blue Kameez Shalwar","Semi-formal kameez","kameez-shalwar","https://...",0,"https://junaidjamshed.com/products/blue-kameez-shalwar-jjksa33909",""
...
```

**Columns:**
- `id` — Unique product identifier
- `title` — Product name
- `description` — Product description
- `category` — Garment category (from user input)
- `image_url` — Direct URL to product image
- `image_index` — 0 for main image, 1+ for gallery images
- `source_url` — Link to product page on store
- `local_path` — Local file path after download (empty until Phase 2)

---

## 🗂️ Project Structure

```
image-scraper/
├── scraper/                    # Core scraping modules
│   ├── __init__.py
│   ├── parser.py               # HTML parsing & data extraction
│   │   ├── fetch_html()        # Fast HTTP fetching with httpx
│   │   ├── fetch_smart()       # Intelligent httpx vs Playwright selection
│   │   ├── extract_cards()     # Product card detection (3-tier strategy)
│   │   ├── _extract_by_framework_patterns()  # Shopify Hydrogen detection
│   │   └── extract_gallery_images()  # Gallery image extraction
│   │
│   ├── paginator.py            # Pagination & deep scrape logic
│   │   ├── find_next_url()     # Detects next page links
│   │   ├── scrape_all_pages()  # Main scraping loop
│   │   └── _scrape_product_gallery()  # Product detail page visitor
│   │
│   └── downloader.py           # Image downloading
│       ├── download_images()   # Batch image downloader
│       └── validate_image()    # Format validation & conversion
│
├── ui/
│   └── app.py                  # Streamlit web interface
│       ├── Scrape page         # Phase 1 scraping UI
│       └── Download page       # Phase 2 image download UI
├── requirements.txt            # Python dependencies
└── README.md                   # This file
```

---

## 🔧 How It Works

### Phase 1: Scraping

```
User Input (URL, Category)
    ↓
[fetch_smart()]  ← Fast httpx or Playwright?
    ↓
[extract_cards()]  ← Find products on page
    ├─→ [_extract_by_framework_patterns()]  ← Try Shopify first
    └─→ [Card Heuristic]  ← Fall back to generic detection
    ↓
[Deduplicate by image_url]
    ↓
[find_next_url()]  ← More pages?
    ├─→ Yes: Go to next page
    └─→ No: Proceed to Deep Scrape
    ↓
[Deep Scrape (Optional)]
    ├─→ For each product:
    │   ├─ Visit product detail page
    │   └─ [extract_gallery_images()]  ← Get all images
    └─→ Save results.csv
```

### Phase 2: Downloading

```
User Input (CSV, Category)
    ↓
[Parse CSV]  ← Load image URLs
    ↓
For each URL:
    ├─ Download image
    ├─ Validate format (PNG, JPG)
    ├─ Rename with pattern: {category}_{id}_{image_index}.ext
    └─ Save to output/images/{category}/
    ↓
Update local_path in CSV
    ↓
Log failures to failed.log
```

---

## 🧪 Testing the Tool

### Test with Sample URL (Shopify Store)
```bash
streamlit run ui/app.py
```

Then in the Scrape page, try:
- **URL:** `https://www.junaidjamshed.com/collections/mens-kameez-shalwar`
- **Category:** `kameez-shalwar`
- **Follow Pagination:** ✓ (checked)
- **Deep Scrape:** ✓ (checked, optional)
- **Max Pages:** 5

**Expected Result:** 
- ~500+ products collected (108 per page)
- CSV saved to `output/results.csv`
- With deep scrape: Each product may have 1-5 gallery images

---

## 🐛 Troubleshooting

### Issue: "0 products found"
**Cause:** Website uses server-side compression or different HTML structure

**Solution:**
- Verify URL is correct (should show products in browser)
- Check if site is Shopify-based (look for `hdt-` class names in inspector)
- Enable Deep Scrape mode to force Playwright rendering
- Check terminal logs for detailed error messages

### Issue: "Connection refused" or timeouts
**Cause:** Website is blocking rapid requests

**Solution:**
- Reduce requests by lowering `max_pages` slider
- Check website's `robots.txt` and terms of service
- Add delays between requests (already built in)
- Try a different URL or website

### Issue: Chromium not installed
**Cause:** `playwright install chromium` didn't run

**Solution:**
```bash
playwright install chromium
# If it's still missing, try:
pip uninstall playwright && pip install playwright
playwright install
```

### Issue: CSV upload fails
**Cause:** CSV format mismatch

**Solution:**
- Ensure CSV has required columns: `image_url, category`
- Use CSV exported from Phase 1 (safest option)
- Check file encoding (should be UTF-8)

---

## 📋 Requirements

```
beautifulsoup4==4.12.3
lxml==5.2.2
httpx==0.27.0
playwright==1.59.0
pandas==2.2.2
pillow==10.3.0
python-slugify==8.0.4
streamlit==1.35.0
```

---

## 🎯 Use Cases

1. **Fashion Dataset Building** — Collect kameez, shalwar, waistcoat images for training ML models
2. **Price Monitoring** — Extract product titles and URLs for competitor analysis
3. **Inventory Audits** — Bulk scrape product listings from multiple pages
4. **Image Labeling** — Organize images by category for annotation
5. **Archive Creation** — Download product images before inventory changes

---

## ⚙️ Advanced Tips

### Working with Multiple Sites
- Each site may have different HTML structures
- Use browser dev tools (`F12`) to inspect product card class names
- Update `framework_selectors` in `parser.py` if needed

### Optimizing Performance
- Disable deep scrape if only main images needed
- Reduce `max_pages` to speed up testing
- Use pagination to scrape large catalogs incrementally

### Debugging
- Check terminal output for detailed logs (timestamps, HTTP status codes)
- Enable Streamlit logging: `--logger.level=debug`
- Inspect CSV files in a spreadsheet app before downloading images

---

## 📝 Notes

- **Rate Limiting:** Built-in 2-3 second delays between requests
- **Robots.txt:** Always check site's terms of service before scraping
- **Legal:** Use only for personal/research purposes with proper attribution
- **Storage:** 1000 images ≈ 50-100 MB disk space
- **Browser:** Chromium runs headless (no visible window)

---

## 💡 Contributing

Found a bug or want to add features? Issues and pull requests welcome!

Possible improvements:
- Support for more e-commerce frameworks
- Parallel image downloading
- Image deduplication by similarity
- REST API endpoint
- Database backend for large-scale scraping

---

## 📄 License

MIT License — Free for personal and research use.

---
