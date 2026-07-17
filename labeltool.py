#!/usr/bin/env python3
"""
labeltool.py — make a shipping label with no printed weight on it.

Four ways to use it:

  # 1. You already made the label in Shopify/Shippo — just paste the link:
  python labeltool.py clean "https://deliver.goshippo.com/....pdf"

  # 2. You saved the PDF:
  python labeltool.py clean label.pdf -o clean.pdf

  # 3. Grab the last label bought in Shippo (incl. from the Shopify app) and clean it:
  python labeltool.py last

  # 4. Full automation from a Shopify order:
  python labeltool.py order 1234 --service ups_ground_saver

  # 5. Drag-and-drop web page for the owner (http://localhost:8000):
  python labeltool.py serve

Cleaned labels land in ./labels/ and open automatically (unless --no-open).
"""

from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys
import time

import requests

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

import carriers
from weight_remover import clean_pdf_bytes

OUT_DIR = pathlib.Path(os.environ.get("LABEL_DIR", "labels"))


def _save(data: bytes, stem: str, open_after: bool = True) -> pathlib.Path:
    OUT_DIR.mkdir(exist_ok=True)
    path = OUT_DIR / f"{stem}-{time.strftime('%Y%m%d-%H%M%S')}.pdf"
    path.write_bytes(data)
    print(f"✅  {path}")
    if open_after:
        opener = {"darwin": "open", "win32": "start"}.get(sys.platform, "xdg-open")
        try:
            subprocess.run([opener, str(path)], check=False)
        except FileNotFoundError:
            pass
    return path


def _load(src: str) -> bytes:
    if src.startswith("http://") or src.startswith("https://"):
        r = requests.get(src, timeout=60)
        r.raise_for_status()
        return r.content
    return pathlib.Path(src).read_bytes()


def cmd_clean(args):
    data = clean_pdf_bytes(_load(args.source), include_dwt=not args.keep_dwt)
    if args.output:
        pathlib.Path(args.output).write_bytes(data)
        print(f"✅  {args.output}")
    else:
        _save(data, "label", not args.no_open)


def cmd_last(args):
    for tx in carriers.recent_transactions(limit=10):
        if tx.get("status") == "SUCCESS" and tx.get("label_url"):
            print(f"Tracking {tx.get('tracking_number')}")
            raw = carriers.download_label(tx["label_url"])
            _save(clean_pdf_bytes(raw, include_dwt=not args.keep_dwt),
                  tx.get("tracking_number", "label"), not args.no_open)
            return
    sys.exit("No successful labels found in Shippo.")


def cmd_order(args):
    order = carriers.get_order(args.order)
    addr = carriers.order_to_address(order)
    parcel = carriers.order_to_parcel(order, args.box)
    if args.weight:
        parcel["weight"] = str(args.weight)

    print(f"Order {order.get('name')} → {addr['name']}, {addr['city']} {addr['state']}")
    print(f"Parcel: {parcel['weight']} lb, {parcel['length']}x{parcel['width']}x{parcel['height']} in")
    if not args.yes:
        if input("Buy this label? [y/N] ").strip().lower() != "y":
            sys.exit("Cancelled.")

    tx = carriers.create_label(addr, parcel, args.service)
    print(f"Bought. Tracking {tx.get('tracking_number')}")
    raw = carriers.download_label(tx["label_url"])
    _save(clean_pdf_bytes(raw, include_dwt=not args.keep_dwt),
          f"{order.get('name','order').lstrip('#')}-{tx.get('tracking_number','')}",
          not args.no_open)


def cmd_serve(args):
    from flask import Flask, request, send_file, Response
    import io

    app = Flask(__name__)
    PAGE = """
    <!doctype html><meta charset=utf-8><title>Label cleaner</title>
    <style>
      body{font:16px system-ui;margin:0;display:grid;place-items:center;height:100vh;background:#faf7f2}
      .card{background:#fff;padding:40px;border-radius:16px;box-shadow:0 2px 20px #0001;text-align:center}
      #drop{border:2px dashed #c8bda9;border-radius:12px;padding:60px 80px;color:#7a6f5c;cursor:pointer}
      #drop.over{background:#f2ede3;border-color:#8a7a5c}
      h1{font-size:20px;margin:0 0 8px}p{color:#7a6f5c;margin:0 0 20px}
    </style>
    <div class=card>
      <h1>Shipping label cleaner</h1>
      <p>Drop the Shippo PDF here — the weight gets removed, the rest stays.</p>
      <div id=drop>Drop PDF or click to choose</div>
      <input id=f type=file accept=application/pdf hidden>
    </div>
    <script>
      const d=document.getElementById('drop'),f=document.getElementById('f');
      d.onclick=()=>f.click();
      f.onchange=()=>send(f.files[0]);
      d.ondragover=e=>{e.preventDefault();d.classList.add('over')};
      d.ondragleave=()=>d.classList.remove('over');
      d.ondrop=e=>{e.preventDefault();d.classList.remove('over');send(e.dataTransfer.files[0])};
      async function send(file){
        if(!file)return; d.textContent='Cleaning…';
        const fd=new FormData(); fd.append('file',file);
        const r=await fetch('/clean',{method:'POST',body:fd});
        if(!r.ok){d.textContent='Error — try again';return}
        const b=await r.blob(), u=URL.createObjectURL(b);
        const a=document.createElement('a'); a.href=u; a.download='label-clean.pdf'; a.click();
        window.open(u,'_blank'); d.textContent='Done — drop another';
      }
    </script>
    """

    @app.get("/")
    def index() -> Response:
        return Response(PAGE, mimetype="text/html")

    @app.post("/clean")
    def clean() -> Response:
        f = request.files["file"]
        data = clean_pdf_bytes(f.read())
        return send_file(io.BytesIO(data), mimetype="application/pdf",
                         as_attachment=True, download_name="label-clean.pdf")

    print(f"\n  Open http://localhost:{args.port} in your browser\n")
    app.run(port=args.port)


def main():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--keep-dwt", action="store_true", help="leave the DWT: line on the label")
    common.add_argument("--no-open", action="store_true", help="don't auto-open the result")

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
                                parents=[common])
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("clean", help="clean a label from a URL or file", parents=[common])
    c.add_argument("source")
    c.add_argument("-o", "--output")
    c.set_defaults(func=cmd_clean)

    l = sub.add_parser("last", help="clean the most recent Shippo label", parents=[common])
    l.set_defaults(func=cmd_last)

    o = sub.add_parser("order", help="buy + clean a label for a Shopify order", parents=[common])
    o.add_argument("order", help="order id (1234) or name (#1234)")
    o.add_argument("--service", help="Shippo servicelevel token, e.g. ups_ground_saver")
    o.add_argument("--box", help="LxWxH inches, e.g. 12x9x6")
    o.add_argument("--weight", type=float, help="override weight in lb")
    o.add_argument("-y", "--yes", action="store_true", help="skip confirmation")
    o.set_defaults(func=cmd_order)

    s = sub.add_parser("serve", help="drag-and-drop web page", parents=[common])
    s.add_argument("--port", type=int, default=8000)
    s.set_defaults(func=cmd_serve)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
