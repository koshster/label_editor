"""
weight_remover.py — strips the printed dimensions ("DWT: 6,5,5") off a
Shippo/UPS shipping label PDF, leaving the weight and everything else
(barcodes, addresses, tracking numbers) untouched.

How it works
------------
Shippo's UPS labels are a raster image inside a PDF (no selectable text), so we:
  1. try the text layer first (some carriers/formats do have one) -> true redaction
  2. otherwise rasterize the page, OCR it with Tesseract to locate the DWT
     tokens, paint a white box over just those words, and rebuild the PDF at the
     original page size/orientation.

Only the matched words get covered. Postage is billed off the manifest/barcode
data, which is unchanged.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass

import fitz  # PyMuPDF
import pytesseract
from PIL import Image, ImageDraw

# UPS dimensional-weight line, e.g. "DWT: 6,5,5"
DWT = re.compile(r"^DWT[.:]?$", re.I)
DWT_FULL = re.compile(r"^DWT[.:]?\s*[\d.,]+$", re.I)
# guards the DWT digit-merge below from swallowing the actual weight (e.g. "4 LB")
WEIGHT_UNIT = re.compile(r"^(LBS?|KGS?|OZS?)[.:]?$", re.I)

RENDER_DPI = 300
PAD = 4  # px of padding around the whiteout box


@dataclass
class Box:
    x0: int
    y0: int
    x1: int
    y1: int

    def union(self, o: "Box") -> "Box":
        return Box(min(self.x0, o.x0), min(self.y0, o.y0),
                   max(self.x1, o.x1), max(self.y1, o.y1))


def _ocr_words(img: Image.Image):
    """Return [(text, Box, line_key, word_idx)] from Tesseract."""
    data = pytesseract.image_to_data(
        img, config="--psm 11", output_type=pytesseract.Output.DICT
    )
    out = []
    for i, txt in enumerate(data["text"]):
        txt = (txt or "").strip()
        if not txt:
            continue
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1
        if conf < 40:
            continue
        b = Box(data["left"][i], data["top"][i],
                data["left"][i] + data["width"][i],
                data["top"][i] + data["height"][i])
        line_key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        out.append((txt, b, line_key, data["word_num"][i]))
    return out


def _find_weight_boxes(words, include_dwt: bool = True) -> list[Box]:
    """Match the DWT (dimensions) token and return only its bounding box."""
    hits: list[Box] = []
    for i, (txt, box, line, wnum) in enumerate(words):
        if DWT_FULL.match(txt) and include_dwt:
            hits.append(box)
            continue
        # "DWT:" followed by the numbers
        if include_dwt and DWT.match(txt):
            merged = box
            for j in range(i + 1, min(i + 4, len(words))):
                t2, b2, l2, _ = words[j]
                if l2 != line:
                    break
                if re.match(r"^[\d.,]+$", t2):
                    # a number immediately followed by a weight unit is the
                    # actual weight, not a dimension digit — stop before it
                    if j + 1 < len(words):
                        t3, _, l3, _ = words[j + 1]
                        if l3 == line and WEIGHT_UNIT.match(t3):
                            break
                    merged = merged.union(b2)
                else:
                    break
            hits.append(merged)
    return hits


def _clean_text_layer(page: fitz.Page, include_dwt: bool) -> bool:
    """If the PDF has real text, redact any DWT match in place. Returns True if
    this page has a text layer at all (whether or not anything needed redacting),
    so callers know not to fall back to rasterizing it."""
    text = page.get_text("text")
    if not text.strip():
        return False
    targets = set()
    if include_dwt:
        targets |= set(m.group(0) for m in re.finditer(r"DWT[.:]?\s*[\d.,]+", text, re.I))
    found = False
    for t in targets:
        for rect in page.search_for(t):
            page.add_redact_annot(rect + (-2, -2, 2, 2), fill=(1, 1, 1))
            found = True
    if found:
        page.apply_redactions()
    return True


def clean_pdf_bytes(pdf_bytes: bytes, include_dwt: bool = True) -> bytes:
    """Take a label PDF, return the same PDF with the dimensions text whited out."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    out = fitz.open()

    for page in doc:
        if _clean_text_layer(page, include_dwt):
            out.insert_pdf(doc, from_page=page.number, to_page=page.number)
            continue

        # Raster path
        rect = page.rect
        pix = page.get_pixmap(dpi=RENDER_DPI)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert("RGB")

        # The label art is often rotated 90 deg on the page; try each orientation.
        cleaned = None
        for angle in (0, 90, 180, 270):
            rot = img.rotate(angle, expand=True, fillcolor="white")
            boxes = _find_weight_boxes(_ocr_words(rot), include_dwt)
            if boxes:
                d = ImageDraw.Draw(rot)
                for b in boxes:
                    d.rectangle(
                        [b.x0 - PAD, b.y0 - PAD, b.x1 + PAD, b.y1 + PAD], fill="white"
                    )
                cleaned = rot.rotate(-angle, expand=True, fillcolor="white")
                break

        if cleaned is None:  # nothing found: pass the page through untouched
            out.insert_pdf(doc, from_page=page.number, to_page=page.number)
            continue

        buf = io.BytesIO()
        cleaned.convert("L").save(buf, format="PNG", optimize=True)
        newpage = out.new_page(width=rect.width, height=rect.height)
        newpage.insert_image(fitz.Rect(0, 0, rect.width, rect.height), stream=buf.getvalue())

    data = out.tobytes(deflate=True)
    out.close()
    doc.close()
    return data


def clean_file(src: str, dst: str, include_dwt: bool = True) -> str:
    with open(src, "rb") as f:
        data = clean_pdf_bytes(f.read(), include_dwt)
    with open(dst, "wb") as f:
        f.write(data)
    return dst
