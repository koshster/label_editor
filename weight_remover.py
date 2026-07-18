"""
weight_remover.py — strips the printed weight ("4 LBS", "DWT: 6,5,5") off a
Shippo/UPS shipping label PDF using RapidOCR (pure Python, no system binaries).
"""

from __future__ import annotations

import io
import re

import fitz  # PyMuPDF
import numpy as np
from PIL import Image, ImageDraw
from rapidocr_onnxruntime import RapidOCR

# Patterns to match weight tokens
NUM_UNIT = re.compile(r"^\d{1,4}([.,]\d{1,3})?\s*(LBS?|KGS?|OZS?)[.:]?$", re.I)
NUM      = re.compile(r"^\d{1,4}([.,]\d{1,3})?$")
UNIT     = re.compile(r"^(LBS?|KGS?|OZS?)[.:]?$", re.I)
DWT      = re.compile(r"^DWT[.:]?\s*([\d.,]+)?$", re.I)

RENDER_DPI = 300
PAD = 6   # px padding around whiteout box

_ocr = None  # lazy singleton


def _get_ocr() -> RapidOCR:
    global _ocr
    if _ocr is None:
        _ocr = RapidOCR()
    return _ocr


def _bbox_to_box(bbox):
    """bbox is [[x0,y0],[x1,y0],[x1,y1],[x0,y1]] from RapidOCR."""
    xs = [p[0] for p in bbox]
    ys = [p[1] for p in bbox]
    return int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))


def _find_weight_regions(img: Image.Image, include_dwt: bool) -> list[tuple]:
    """Returns list of (x0, y0, x1, y1) boxes to white out."""
    ocr = _get_ocr()
    result, _ = ocr(np.array(img.convert("RGB")))
    if not result:
        return []

    hits = []
    items = [(text, _bbox_to_box(bbox), conf) for bbox, text, conf in result if conf > 0.4]

    for i, (text, box, conf) in enumerate(items):
        t = text.strip()

        # "4 LBS" or "4LBS" as a single token
        if NUM_UNIT.match(t):
            hits.append(box)
            continue

        # "DWT: 6,5,5" or "DWT:" standalone
        if include_dwt and DWT.match(t):
            hits.append(box)
            continue

        # Two adjacent tokens: "4" + "LBS"
        if NUM.match(t):
            for j in (i + 1, i + 2):
                if j < len(items):
                    t2, box2, _ = items[j]
                    if UNIT.match(t2.strip()):
                        x0 = min(box[0], box2[0])
                        y0 = min(box[1], box2[1])
                        x1 = max(box[2], box2[2])
                        y1 = max(box[3], box2[3])
                        hits.append((x0, y0, x1, y1))
                        break

    return hits


def clean_pdf_bytes(pdf_bytes: bytes, include_dwt: bool = True) -> bytes:
    """Take a label PDF, return same PDF with weight text whited out."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    out = fitz.open()

    for page in doc:
        rect = page.rect
        pix = page.get_pixmap(dpi=RENDER_DPI)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)

        cleaned = None
        for angle in (0, 90, 180, 270):
            rot = img.rotate(angle, expand=True, fillcolor="white")
            boxes = _find_weight_regions(rot, include_dwt)
            if boxes:
                d = ImageDraw.Draw(rot)
                for (x0, y0, x1, y1) in boxes:
                    d.rectangle([x0 - PAD, y0 - PAD, x1 + PAD, y1 + PAD], fill="white")
                cleaned = rot.rotate(-angle, expand=True, fillcolor="white")
                break

        if cleaned is None:
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