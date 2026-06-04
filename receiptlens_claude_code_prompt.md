# ReceiptLens — Claude Code Implementation Prompt

Build **ReceiptLens**: a personal grocery expense tracker web app. The user scans a
QR code on a Ukrainian fiscal receipt (фіскальний чек), the app fetches the
itemised purchase list from the State Tax Service API, the user reviews and
categorises each item, and the app shows spending analytics by food category, store,
and month.

Work **phase by phase**. After each phase run the listed verification commands and
confirm everything passes before moving to the next phase.

---

## Stack

- **Django 5** (fullstack — templates, no separate frontend)
- **HTMX 2** (via CDN) — dynamic partials without a SPA
- **Alpine.js** (via CDN) — light client-side interactivity on the review form
- **Bootstrap 5** (via CDN) — mobile-first styling, zero build step
- **html5-qrcode** (via CDN) — camera QR decoding in the browser
- **Chart.js** (via CDN) — analytics charts
- **httpx** — sync HTTP client for the ДПС API call
- **WhiteNoise** — static file serving in production
- **SQLite** for dev, settings wired to swap to Postgres via `DATABASE_URL` env var
- **gunicorn** for production

`requirements.txt`:
```
django>=5.0
httpx>=0.27
whitenoise>=6.7
gunicorn>=22.0
python-dotenv>=1.0
psycopg[binary]>=3.1
```

---

## Confirmed technical facts — do not deviate from these

These were validated against a real receipt. They are facts, not assumptions.

### ДПС chkAllWeb endpoint

```
GET https://cabinet.tax.gov.ua/ws/api_public/rro/chkAllWeb
```

- **No authentication** — the API accepts `Authorization: Bearer undefined` (literally). No login needed.
- **No captcha enforced** — confirmed by removing the `captcha` param entirely and getting a full response (Test A passed).
- **Required params:** `id` (check number), `fn` (РРО fiscal number), `sm` (sum), `date` (format: `YYYY-MM-DD HH:MM:SS`, seconds always `:00`), `type=3`.
- **Required headers:** a browser-like `User-Agent`, `Accept: application/json, text/plain, */*`, `Lang: uk`, `Referer: https://cabinet.tax.gov.ua/cashregs/check`.

### Response structure

The JSON has a `checkXml` field: **base64 of windows-1251 XML** (schema `check01.xsd`).

- Decode: `base64.b64decode(b64).decode("cp1251")` — **cp1251, not utf-8**. Garbled names = wrong encoding.
- Strip the XML declaration line before parsing with `ET.fromstring()`.
- `CHECKBODY/ROW` elements have: `CODE`, `BARCODE`, `NAME`, `AMOUNT` (qty), `PRICE` (unit), `COST` (line total), `LETTERS` (tax letter, ignore).
- `CHECKTOTAL/SUM` is the receipt total.
- `CHECKHEAD` has: `ORGNM` (legal entity name), `POINTNM` (generic, usually "Магазин"), `POINTADDR`, `CASHREGISTERNUM` (РРО — the stable store key), `ORDERDATE` (DDMMYYYY), `ORDERTIME` (HHMMSS), `ORDERNUM`.
- Ignore `check` (plaintext) and `checkP7s` (PKCS#7 signature) fields — not needed.

### QR code format

The QR on a Ukrainian fiscal receipt encodes a URL like:
```
https://cabinet.tax.gov.ua/cashregs/check?mac=...&date=YYYYMMDD&time=HHmm&id=...&sm=...&fn=...
```
Parse it to extract `id`, `fn`, `sm`, `date` (YYYYMMDD → reformat), `time` (HHmm → reformat).
The `mac` field is **not** used in the API call.

### Store identity

`POINTNM` is always generic ("Магазин"). `ORGNM` is the legal entity name (not the brand).
The **`CASHREGISTERNUM` (РРО)** is the stable, unique key for a store till.
Let the user assign a friendly `display_name` alias once per РРО.

---

## Reusable code — use these verbatim

### `receipts/services/tax_check.py` — copy exactly, do not modify

```python
"""Parse a ДПС `chkAllWeb` response into structured receipt data."""
from __future__ import annotations

import base64
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from decimal import Decimal


@dataclass
class Item:
    code: str
    barcode: str       # primary key for category rules
    name: str
    qty: Decimal
    unit: str          # "kg" | "pcs"
    unit_price: Decimal
    total: Decimal


@dataclass
class Receipt:
    org_legal_name: str
    point_name: str
    point_address: str
    fiscal_rro: str    # CASHREGISTERNUM -> store alias key
    order_num: str
    datetime: str      # "YYYY-MM-DD HH:MM:SS"
    total: Decimal
    items: list[Item] = field(default_factory=list)


def _decode_check_xml(b64: str) -> ET.Element:
    text = base64.b64decode(b64).decode("cp1251")
    text = text.split("?>", 1)[1].lstrip()
    return ET.fromstring(text)


def parse_chk_response(data: dict) -> Receipt:
    if not data.get("checkXml"):
        raise ValueError(f"No checkXml in response (resultText={data.get('resultText')!r})")

    root = _decode_check_xml(data["checkXml"])
    head = root.find("CHECKHEAD")
    g = lambda p, t: (p.findtext(t) or "").strip()

    d, t = g(head, "ORDERDATE"), g(head, "ORDERTIME")
    receipt = Receipt(
        org_legal_name=g(head, "ORGNM"),
        point_name=g(head, "POINTNM"),
        point_address=g(head, "POINTADDR"),
        fiscal_rro=g(head, "CASHREGISTERNUM"),
        order_num=g(head, "ORDERNUM"),
        datetime=f"{d[4:8]}-{d[2:4]}-{d[0:2]} {t[0:2]}:{t[2:4]}:{t[4:6]}",
        total=Decimal(root.findtext("CHECKTOTAL/SUM")),
    )

    for row in root.findall("CHECKBODY/ROW"):
        qty = Decimal(g(row, "AMOUNT"))
        receipt.items.append(Item(
            code=g(row, "CODE"),
            barcode=g(row, "BARCODE"),
            name=g(row, "NAME"),
            qty=qty,
            unit="kg" if qty != qty.to_integral_value() else "pcs",
            unit_price=Decimal(g(row, "PRICE")),
            total=Decimal(g(row, "COST")),
        ))
    return receipt
```

### `receipts/services/dps.py` — copy exactly, do not modify

```python
from urllib.parse import urlparse, parse_qs
import httpx

CHK_URL = "https://cabinet.tax.gov.ua/ws/api_public/rro/chkAllWeb"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Lang": "uk",
    "Referer": "https://cabinet.tax.gov.ua/cashregs/check",
}


def parse_qr_url(url: str) -> dict:
    q = parse_qs(urlparse(url).query)
    g = lambda k: (q.get(k) or [None])[0]
    d = (g("date") or "")
    t = (g("time") or "").replace(":", "").ljust(4, "0")
    return {
        "id": g("id"),
        "fn": g("fn"),
        "sm": g("sm"),
        "api_date": f"{d[0:4]}-{d[4:6]}-{d[6:8]} {t[0:2]}:{t[2:4]}:00",
    }


def fetch_check(*, id: str, fn: str, sm: str, api_date: str,
                type: int = 3, captcha: str | None = None, timeout: float = 15.0) -> dict:
    params = {"id": id, "fn": fn, "sm": sm, "date": api_date, "type": type}
    if captcha:
        params["captcha"] = captcha
    r = httpx.get(CHK_URL, params=params, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    if not data.get("checkXml"):
        raise ValueError(data.get("resultText") or "No itemized data returned")
    return data
```

---

## Data model (`receipts/models.py`)

```python
from django.db import models


class Store(models.Model):
    fiscal_rro = models.CharField(max_length=32, unique=True, db_index=True)
    legal_name = models.CharField(max_length=255, blank=True)
    display_name = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return self.display_name or self.legal_name or self.fiscal_rro


class Category(models.Model):
    name = models.CharField(max_length=64, unique=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = "categories"


class Receipt(models.Model):
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="receipts")
    check_id = models.CharField(max_length=64, blank=True)
    fn = models.CharField(max_length=32, blank=True)
    datetime = models.DateTimeField(db_index=True)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    source = models.CharField(max_length=16, default="qr")  # qr | xml | manual
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.store} {self.datetime:%Y-%m-%d} {self.total} ₴"


class Item(models.Model):
    receipt = models.ForeignKey(Receipt, on_delete=models.CASCADE, related_name="items")
    code = models.CharField(max_length=32, blank=True)
    barcode = models.CharField(max_length=64, blank=True, db_index=True)
    name = models.CharField(max_length=255)
    qty = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    unit = models.CharField(max_length=8, default="pcs")
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    category = models.ForeignKey(Category, null=True, blank=True,
                                 on_delete=models.SET_NULL, related_name="items")

    def __str__(self):
        return f"{self.name} ({self.qty} {self.unit})"


class CategoryRule(models.Model):
    BARCODE = "barcode"
    NAME = "name"
    key_type = models.CharField(max_length=8, default=BARCODE)
    key_value = models.CharField(max_length=255, db_index=True)
    category = models.ForeignKey(Category, on_delete=models.CASCADE)

    class Meta:
        unique_together = ("key_type", "key_value")

    def __str__(self):
        return f"{self.key_type}:{self.key_value} → {self.category}"
```

---

## Categorization (`receipts/services/categorize.py`)

```python
from receipts.models import CategoryRule


def normalize_name(name: str) -> str:
    return " ".join(name.upper().split())


def suggest_category_id(*, barcode: str, name: str) -> int | None:
    if barcode:
        rule = CategoryRule.objects.filter(key_type="barcode", key_value=barcode).first()
        if rule:
            return rule.category_id
    rule = CategoryRule.objects.filter(
        key_type="name", key_value=normalize_name(name)).first()
    return rule.category_id if rule else None


def learn_rule(*, barcode: str, name: str, category_id: int) -> None:
    key_type, key_value = ("barcode", barcode) if barcode else ("name", normalize_name(name))
    CategoryRule.objects.update_or_create(
        key_type=key_type, key_value=key_value,
        defaults={"category_id": category_id})
```

---

## Project structure to create

```
receiptlens/
├── manage.py
├── requirements.txt
├── .env.example               # SECRET_KEY, DEBUG, DATABASE_URL
├── receiptlens/
│   ├── __init__.py
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
├── receipts/
│   ├── __init__.py
│   ├── admin.py
│   ├── apps.py
│   ├── models.py
│   ├── views.py
│   ├── urls.py
│   ├── services/
│   │   ├── __init__.py
│   │   ├── tax_check.py       ← copy verbatim
│   │   ├── dps.py             ← copy verbatim
│   │   └── categorize.py
│   ├── management/commands/
│   │   ├── __init__.py
│   │   └── seed_categories.py
│   ├── migrations/
│   └── templates/receipts/
│       ├── base.html
│       ├── dashboard.html
│       ├── scan.html
│       ├── _review.html       ← HTMX partial
│       ├── _review_error.html ← HTMX partial for fetch errors
│       ├── manual.html
│       ├── receipt_detail.html
│       └── analytics.html
└── static/
    ├── js/
    │   └── scan.js            ← camera + QR decode
    ├── manifest.webmanifest
    └── sw.js
```

---

## Phase 0 — Foundation

**Goal:** project boots, models exist, admin works, DPS pipeline tested end-to-end.

1. Create the Django project `receiptlens` and app `receipts`.
2. Write `settings.py`:
   - `SECRET_KEY` from env (`python-dotenv`).
   - `DEBUG` from env, default `True`.
   - `DATABASE_URL` env var supported via `dj-database-url` or manual parse; default SQLite.
   - `INSTALLED_APPS` includes `receipts` and `whitenoise.middleware`.
   - `STATIC_ROOT`, `STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"`.
   - `TIME_ZONE = "Europe/Kiev"`, `USE_TZ = True`.
3. Copy `tax_check.py`, `dps.py`, `categorize.py` into `receipts/services/`.
4. Write `receipts/models.py` (exact schema above).
5. Run migrations.
6. Write `receipts/admin.py` — register all five models with sensible `list_display` and `search_fields`:
   - `Store`: list `fiscal_rro`, `display_name`, `legal_name`; search `fiscal_rro`, `display_name`.
   - `Category`: list `name`.
   - `Receipt`: list `store`, `datetime`, `total`, `source`; filter `source`, `store`; date hierarchy `datetime`.
   - `Item`: list `name`, `barcode`, `category`, `total`; search `name`, `barcode`; filter `category`.
   - `CategoryRule`: list `key_type`, `key_value`, `category`; search `key_value`; filter `key_type`.
7. Create superuser and open `/admin/` — all models visible.
8. Write `receipts/management/commands/seed_categories.py` — idempotent command that creates these categories if they don't exist:
   ```
   Овочі, Фрукти, Молочне, М'ясо та ковбаси, Випічка та хліб,
   Бакалія, Снеки та солодощі, Напої, Побутове, Інше
   ```
9. Write a management command `fetch_test` that accepts a QR URL as an argument, calls `parse_qr_url` → `fetch_check` → `parse_chk_response`, and pretty-prints the result (store info + item table). Use this to verify the pipeline works without a browser.

**Verify phase 0:**
```bash
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_categories
python manage.py createsuperuser
python manage.py fetch_test "https://cabinet.tax.gov.ua/cashregs/check?id=7006109077&fn=4000935353&sm=919.14&date=20260602&time=21:10"
# Expected: 11 items, total 919.14, store РРО 4000935353
python manage.py runserver
# Visit http://localhost:8000/admin/ — all models listed
```

---

## Phase 1 — Capture flow

**Goal:** scan a QR on a real receipt → pre-filled review screen → save to DB.

### `base.html`

Load via CDN in `<head>` / before `</body>`:
- Bootstrap 5 CSS + JS bundle
- HTMX 2: `<script src="https://unpkg.com/htmx.org@2" defer></script>`
- Alpine.js: `<script defer src="https://unpkg.com/alpinejs@3/dist/cdn.min.js"></script>`
- `{% block extra_head %}{% endblock %}` for page-specific additions

### URL routes

```
/                           → dashboard
/scan/                      → scan page
/receipts/parse/            → HTMX POST: returns _review.html partial
/receipts/save/             → POST: saves confirmed receipt, redirects to detail
/receipts/<pk>/             → receipt detail
/manual/                    → manual entry form
```

### Scan page (`scan.html` + `static/js/scan.js`)

- Load `html5-qrcode` from CDN: `https://unpkg.com/html5-qrcode@latest/html5-qrcode.min.js`
- A `<div id="reader">` where the camera preview renders.
- A hidden `<input id="qr-result">` and a `<form id="parse-form" hx-post="/receipts/parse/" hx-target="#review-container" hx-swap="innerHTML">` with a hidden `<input name="qr_url" id="qr-input">`.
- Fallback: a visible text input where the user can paste a QR URL manually, with the same form.
- `scan.js` initialises `Html5Qrcode`, on successful decode sets `#qr-input` value and submits `#parse-form` via HTMX.
- Show a spinner in `#review-container` while the HTMX request is in flight (use `htmx:beforeRequest` / `htmx:afterRequest` events or `hx-indicator`).

### Parse view

```python
# POST /receipts/parse/
def receipts_parse(request):
    qr_url = request.POST.get("qr_url", "").strip()
    xml_b64 = request.POST.get("check_xml_b64", "").strip()
    try:
        if xml_b64:
            data = {"checkXml": xml_b64}
        elif qr_url:
            p = parse_qr_url(qr_url)
            data = fetch_check(id=p["id"], fn=p["fn"], sm=p["sm"], api_date=p["api_date"])
        else:
            raise ValueError("Provide qr_url or check_xml_b64")

        receipt = parse_chk_response(data)
        store = Store.objects.filter(fiscal_rro=receipt.fiscal_rro).first()
        categories = Category.objects.order_by("name")
        items = [{
            "code": it.code, "barcode": it.barcode, "name": it.name,
            "qty": str(it.qty), "unit": it.unit,
            "unit_price": str(it.unit_price), "total": str(it.total),
            "suggested_category_id": suggest_category_id(barcode=it.barcode, name=it.name) or "",
        } for it in receipt.items]
        return render(request, "receipts/_review.html", {
            "receipt": receipt, "store": store,
            "categories": categories, "items": items,
        })
    except Exception as e:
        return render(request, "receipts/_review_error.html", {"error": str(e)})
```

### Review partial (`_review.html`)

Rendered by HTMX into `#review-container`. Must be a form POSTing to `/receipts/save/`.

- **Store row:** show `receipt.org_legal_name` + РРО. If `store.display_name` exists, show it in an editable input pre-filled with it; if the РРО is new, show an empty "Nickname for this store" input.  Hidden input `fiscal_rro`.
- **Item table:** one row per item with editable inputs for `name`, `qty`, `unit`, `unit_price`. `total` shown as text (recalculate in JS = qty × unit_price on blur). Per-row `<select name="category_id_N">` populated from `categories`, pre-selected to `suggested_category_id`.
- Submit button "Save receipt".

Use Alpine.js for row-level total recalculation:
```html
<tr x-data="{ qty: {{ it.qty }}, price: {{ it.unit_price }} }">
  <td><input x-model.number="qty" name="qty_N" ...></td>
  <td><input x-model.number="price" name="price_N" ...></td>
  <td x-text="(qty * price).toFixed(2)"></td>
  <input type="hidden" :name="'total_N'" :value="(qty*price).toFixed(2)">
</tr>
```

### Save view

```python
# POST /receipts/save/
def receipts_save(request):
    # 1. Upsert store by fiscal_rro
    # 2. Create Receipt
    # 3. For each item (read from POST arrays: name_N, barcode_N, qty_N, ...):
    #    - Create Item
    #    - If category_id provided: call learn_rule(barcode=..., name=..., category_id=...)
    # 4. Redirect to /receipts/<pk>/
```

Parse item arrays from POST: use a counter or a fixed `item_count` hidden input.

### Error partial (`_review_error.html`)

Simple: Bootstrap alert with the error message, a "Try again" link, and a textarea for pasting `check_xml_b64` as fallback.

**Verify phase 1:**
```bash
python manage.py runserver
# 1. Open /scan/ in a browser
# 2. Paste the test QR URL into the fallback input
# 3. Confirm the review partial renders with 11 items pre-filled
# 4. Assign categories, click Save
# 5. Confirm redirect to receipt detail showing all items
# Check DB: python manage.py shell -c "from receipts.models import Receipt; print(Receipt.objects.last())"
```

---

## Phase 2 — Categorization

**Goal:** suggestions auto-fill on parse; saving a confirmed category teaches a rule for next time.

- The `learn_rule` call is already in the save view — verify it creates `CategoryRule` rows.
- Parse and save a second receipt containing any of the same products; confirm they arrive with categories pre-selected.
- Add a management command `show_rules` that prints all learned rules as a table (handy for debugging).

**Verify phase 2:**
```bash
python manage.py fetch_test "...same URL..." | grep suggested
# After saving, parse again and confirm matched rows show the right category
python manage.py show_rules
```

---

## Phase 3 — Analytics

**Goal:** charts showing spend by category, by store, and by month.

`/analytics/` GET accepts optional `from_date` and `to_date` query params (date inputs).

Compute server-side with the ORM:

```python
from django.db.models import Sum
from django.db.models.functions import TruncMonth

# By category
by_category = (Item.objects
    .filter(receipt__datetime__date__gte=from_date, receipt__datetime__date__lte=to_date)
    .exclude(category=None)
    .values("category__name")
    .annotate(total=Sum("total"))
    .order_by("-total"))

# By store
by_store = (Receipt.objects
    .filter(datetime__date__gte=from_date, datetime__date__lte=to_date)
    .values("store__display_name", "store__legal_name")
    .annotate(total=Sum("total"))
    .order_by("-total"))

# By month
by_month = (Receipt.objects
    .filter(datetime__date__gte=from_date, datetime__date__lte=to_date)
    .annotate(month=TruncMonth("datetime"))
    .values("month")
    .annotate(total=Sum("total"))
    .order_by("month"))
```

Pass the results as JSON into the template via `json_script` or embed as `<script>const DATA = ...;</script>`.
Render three Chart.js charts: a **doughnut** for category, a **bar** for stores, a **line** for monthly trend.
Default date range: current month.

**Verify phase 3:**
```bash
# After saving at least one receipt:
python manage.py runserver
# Visit /analytics/ — three charts render with real data
```

---

## Phase 4 — Dashboard, PWA, polish

### Dashboard (`/`)

- Recent 10 receipts as a list (store name, date, total, link to detail).
- This month's spend total vs. last month.
- Top 3 categories this month as a mini bar or progress bars.

### PWA

`static/manifest.webmanifest`:
```json
{
  "name": "ReceiptLens",
  "short_name": "ReceiptLens",
  "start_url": "/scan/",
  "display": "standalone",
  "background_color": "#ffffff",
  "theme_color": "#0d6efd",
  "icons": [{"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"}]
}
```

`static/sw.js` — minimal service worker: cache the app shell on install so it loads offline.

Add to `base.html` `<head>`:
```html
<link rel="manifest" href="/static/manifest.webmanifest">
<meta name="theme-color" content="#0d6efd">
<script>if ('serviceWorker' in navigator) navigator.serviceWorker.register('/static/sw.js');</script>
```

Serve `manifest.webmanifest` and `sw.js` at `/static/` — Django + WhiteNoise handles it automatically.

### Manual entry (`/manual/`)

Identical layout to `_review.html` but with empty rows and an "Add item" button (Alpine.js appends a row).
Posts to `/receipts/save/` with `source=manual`.

---

## What NOT to build

Do not add any of the following — they are explicitly out of scope:

- User authentication or multi-user support
- Budgets, alerts, or spending limits
- OCR of paper receipts (only QR scanning)
- ML or AI-based categorisation (keyword/barcode rules only)
- Verification of the `checkP7s` PKCS#7 signature field
- Any background tasks or Celery workers
- REST API endpoints (this is a Django fullstack app, not an API)

---

## Error handling requirements

- If `fetch_check` raises (network error, 404, no `checkXml`): render `_review_error.html` with the error and the `check_xml_b64` paste fallback.
- If the camera is denied or unavailable: show the paste-URL fallback input immediately.
- Wrap the save view in a transaction so a failed item insert doesn't create a partial receipt.
- Never crash with a 500 on bad QR URLs — catch `ValueError` and show the error partial.

---

## Deployment notes (for when ready)

```bash
python manage.py collectstatic --no-input
gunicorn receiptlens.wsgi:application --bind 0.0.0.0:$PORT
```

Set env vars: `SECRET_KEY`, `DEBUG=False`, `DATABASE_URL`, `ALLOWED_HOSTS`.
HTTPS is mandatory — the camera will not open on HTTP in production.
