"""
carriers.py — thin wrappers around Shippo and Shopify.

Env vars (put them in a .env file next to this script):
  SHIPPO_TOKEN         Shippo API token (live: shippo_live_...)
  SHOPIFY_STORE        e.g. priyum-products.myshopify.com
  SHOPIFY_TOKEN        Shopify Admin API access token (custom app)
  SHIP_FROM_*          your warehouse address (see .env.example)
  DEFAULT_SERVICE      Shippo servicelevel_token, e.g. ups_ground_saver
"""

from __future__ import annotations

import os

import requests

SHIPPO = "https://api.goshippo.com"
API_VERSION = "2024-10"  # Shopify Admin API version


# ----------------------------------------------------------------- Shippo ----
def _sh_headers() -> dict:
    token = os.environ["SHIPPO_TOKEN"]
    return {"Authorization": f"ShippoToken {token}", "Content-Type": "application/json"}


def ship_from() -> dict:
    return {
        "name": os.environ.get("SHIP_FROM_NAME", ""),
        "company": os.environ.get("SHIP_FROM_COMPANY", ""),
        "street1": os.environ.get("SHIP_FROM_STREET1", ""),
        "street2": os.environ.get("SHIP_FROM_STREET2", ""),
        "city": os.environ.get("SHIP_FROM_CITY", ""),
        "state": os.environ.get("SHIP_FROM_STATE", ""),
        "zip": os.environ.get("SHIP_FROM_ZIP", ""),
        "country": os.environ.get("SHIP_FROM_COUNTRY", "US"),
        "phone": os.environ.get("SHIP_FROM_PHONE", ""),
        "email": os.environ.get("SHIP_FROM_EMAIL", ""),
    }


def create_label(address_to: dict, parcel: dict, servicelevel: str | None = None) -> dict:
    """Create shipment, pick a rate, buy the label. Returns the transaction dict."""
    shipment = requests.post(
        f"{SHIPPO}/shipments/",
        json={
            "address_from": ship_from(),
            "address_to": address_to,
            "parcels": [parcel],
            "async": False,
        },
        headers=_sh_headers(),
        timeout=60,
    )
    shipment.raise_for_status()
    rates = shipment.json().get("rates", [])
    if not rates:
        raise RuntimeError("Shippo returned no rates for this shipment")

    servicelevel = servicelevel or os.environ.get("DEFAULT_SERVICE")
    rate = None
    if servicelevel:
        rate = next(
            (r for r in rates if r["servicelevel"]["token"] == servicelevel), None
        )
    if rate is None:  # fall back to cheapest
        rate = min(rates, key=lambda r: float(r["amount"]))

    tx = requests.post(
        f"{SHIPPO}/transactions/",
        json={"rate": rate["object_id"], "label_file_type": "PDF_4x6", "async": False},
        headers=_sh_headers(),
        timeout=90,
    )
    tx.raise_for_status()
    tx = tx.json()
    if tx.get("status") != "SUCCESS":
        raise RuntimeError(f"Label purchase failed: {tx.get('messages')}")
    return tx


def get_transaction(transaction_id: str) -> dict:
    r = requests.get(
        f"{SHIPPO}/transactions/{transaction_id}", headers=_sh_headers(), timeout=30
    )
    r.raise_for_status()
    return r.json()


def recent_transactions(limit: int = 10) -> list[dict]:
    """Labels bought recently — including ones the owner made inside Shopify."""
    r = requests.get(
        f"{SHIPPO}/transactions?results={limit}", headers=_sh_headers(), timeout=30
    )
    r.raise_for_status()
    return r.json().get("results", [])


def download_label(url: str) -> bytes:
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    return r.content


# ---------------------------------------------------------------- Shopify ----
def get_order(order_id_or_name: str) -> dict:
    """Accepts a numeric order id or an order name like '#1234'."""
    store = os.environ["SHOPIFY_STORE"]
    headers = {"X-Shopify-Access-Token": os.environ["SHOPIFY_TOKEN"]}
    base = f"https://{store}/admin/api/{API_VERSION}"

    if str(order_id_or_name).lstrip("#").isdigit() and not str(order_id_or_name).startswith("#"):
        r = requests.get(f"{base}/orders/{order_id_or_name}.json", headers=headers, timeout=30)
        r.raise_for_status()
        return r.json()["order"]

    name = str(order_id_or_name)
    r = requests.get(
        f"{base}/orders.json",
        params={"name": name, "status": "any"},
        headers=headers,
        timeout=30,
    )
    r.raise_for_status()
    orders = r.json().get("orders", [])
    if not orders:
        raise RuntimeError(f"No Shopify order matching {name}")
    return orders[0]


def order_to_address(order: dict) -> dict:
    a = order.get("shipping_address") or order.get("billing_address")
    if not a:
        raise RuntimeError("Order has no shipping address")
    return {
        "name": a.get("name") or "",
        "company": a.get("company") or "",
        "street1": a.get("address1") or "",
        "street2": a.get("address2") or "",
        "city": a.get("city") or "",
        "state": a.get("province_code") or a.get("province") or "",
        "zip": a.get("zip") or "",
        "country": a.get("country_code") or "US",
        "phone": a.get("phone") or order.get("phone") or "",
        "email": order.get("email") or "",
    }


def order_to_parcel(order: dict, box: str | None = None) -> dict:
    """Weight = sum of line-item grams (Shopify's own numbers), converted to lb."""
    grams = sum(
        (li.get("grams") or 0) * (li.get("quantity") or 1) for li in order.get("line_items", [])
    )
    lbs = max(round(grams / 453.59237, 2), 0.1)
    lbs += float(os.environ.get("PACKAGING_LBS", "0"))  # optional dunnage allowance

    dims = (box or os.environ.get("DEFAULT_BOX", "12x9x6")).lower().split("x")
    return {
        "length": dims[0],
        "width": dims[1],
        "height": dims[2],
        "distance_unit": "in",
        "weight": str(round(lbs, 2)),
        "mass_unit": "lb",
    }
