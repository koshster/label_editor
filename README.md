# Label tool — Priyum Products LLC

Removes the `DWT:` (dimensions) line from a Shippo/UPS label so the owner no longer
has to crop it by hand. The printed weight (`4 LBS`) is left alone, as is everything
else — barcodes, addresses, tracking numbers, the USPS Parcel Select block.

Shippo's UPS labels are a flat image inside the PDF, so the tool finds the DWT line
with OCR (Tesseract), paints a white box over exactly those words, and rebuilds the
PDF at the same 4x6 size. Postage is still billed off the manifest/barcode data,
which the tool never touches.

## Install (once)

```bash
# macOS
brew install tesseract
# Windows: install Tesseract from https://github.com/UB-Mannheim/tesseract/wiki
# Ubuntu: sudo apt install tesseract-ocr

pip3 install -r requirements.txt
cp .env.example .env      # then fill in your keys/address
```

## The easy way (owner-facing)

```bash
python labeltool.py serve
```

Opens `http://localhost:8000`: a page where you drag the Shippo PDF in and the
cleaned label downloads and opens straight away. Nothing else changes about the
current Shopify → Shippo workflow.

## The other ways

| Command | What it does |
|---|---|
| `python labeltool.py clean "<shippo label URL>"` | paste the link Shippo gives you |
| `python labeltool.py clean label.pdf -o clean.pdf` | clean a saved file |
| `python labeltool.py last` | pull the most recent label bought in Shippo (including from the Shopify app) and clean it |
| `python labeltool.py order 1234 --service ups_ground_saver` | full automation: read the Shopify order, buy the label in Shippo, clean it |

Useful flags: `--keep-dwt` (leave the `DWT:` line), `--no-open`, `-y` (skip the
buy confirmation), `--weight 4` / `--box 12x9x6` (override the parcel).

`order` prints the address, weight and box and asks before spending money.

## Config

`.env` holds the Shippo token, the Shopify custom-app token (scope `read_orders`),
your ship-from address, and defaults (`DEFAULT_SERVICE=ups_ground_saver`,
`DEFAULT_BOX=12x9x6`, `PACKAGING_LBS=0.5`).

Weight for the `order` command comes from the sum of the Shopify line-item weights
plus `PACKAGING_LBS`. If the store's product weights aren't accurate, use
`--weight` or fix the weights in Shopify — it's what gets billed on the manifest
and it's still printed on the cleaned label.

## Files

- `weight_remover.py` — the actual redaction (import `clean_pdf_bytes(pdf_bytes)` anywhere)
- `carriers.py` — Shippo + Shopify API calls
- `labeltool.py` — CLI and the drag-and-drop web page

## Going fully hands-off later

`clean_pdf_bytes()` is the reusable piece. Point a Shopify webhook
(`orders/fulfilled` or a tagged order) at a small server that calls
`carriers.create_label()` → `clean_pdf_bytes()` → emails/prints the PDF, and no one
touches Shippo at all. The current design keeps the owner's Shippo step intact
because that's where the carrier/rate selection judgment lives.
