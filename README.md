# ReceiptLens

Personal grocery expense tracker for Ukrainian fiscal receipts. Scan a QR code on a receipt, review and categorise each item, then explore spending analytics by category, store, and month.

## How it works

1. Open `/scan/` and point the camera at a receipt QR code, or upload a photo of one.
2. ReceiptLens fetches the itemised list from the State Tax Service (ДПС) API — no login required.
3. Review the items, assign categories, give the store a friendly nickname, and save.
4. Visit `/analytics/` to see charts of your spending over time.

## Stack

| Layer | Technology |
|---|---|
| Backend | Django 6, SQLite (dev) / PostgreSQL (prod) |
| Dynamic UI | HTMX 2 (partials), Alpine.js (form interactivity) |
| Styling | Bootstrap 5 |
| Charts | Chart.js 4 |
| QR scanning | html5-qrcode (camera), jsQR (file upload) |
| Static files | WhiteNoise |
| Production server | Gunicorn |

All frontend libraries are loaded from CDN — no build step.

## Supported receipt schemas

| Schema | Root tag | Used by |
|---|---|---|
| check01.xsd | `<CHECK>` | Most stores (ATB, Novus, …) |
| Compact RQ | `<RQ>` | Silpo and some other chains |

## Setup

**Requires Python 3.11+**

```bash
git clone <repo>
cd ReceiptLens

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env        # edit SECRET_KEY at minimum
python manage.py migrate
python manage.py seed_categories
python manage.py createsuperuser
```

## Running locally

```bash
python manage.py runserver
```

Open `http://localhost:8000`.

### Accessible from other devices on the LAN

```bash
python manage.py runserver 0.0.0.0:8000
```

Set `ALLOWED_HOSTS=raspberrypi.local` (or your hostname) in `.env`.

> **Camera requires HTTPS.** On HTTP the browser blocks `getUserMedia`. Use the HTTPS setup below, or upload a photo of the QR code instead.

### HTTPS on a Raspberry Pi (or any LAN host)

```bash
sudo apt install mkcert libnss3-tools
mkcert -install
mkcert raspberrypi.local localhost 127.0.0.1

source venv/bin/activate
gunicorn receiptlens.wsgi:application \
  --bind 0.0.0.0:8443 \
  --certfile raspberrypi.local+2.pem \
  --keyfile raspberrypi.local+2-key.pem \
  --reload
```

Install the root CA on your phone once (one-time):

```bash
# Serve the CA file for your phone to download
python3 -m http.server 8080
# Then open http://raspberrypi.local:8080/rootCA.pem on the phone and install it
```

- **Android:** tap the downloaded file → install as CA certificate
- **iOS:** tap the link → Allow → Settings → General → VPN & Device Management → Install → then Settings → General → About → Certificate Trust Settings → enable the mkcert entry

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `SECRET_KEY` | *(required)* | Django secret key |
| `DEBUG` | `True` | Set to `False` in production |
| `DATABASE_URL` | SQLite | PostgreSQL URL, e.g. `postgres://user:pass@host/db` |
| `ALLOWED_HOSTS` | `localhost 127.0.0.1` | Space-separated list of allowed hostnames |

## Management commands

```bash
python manage.py seed_categories   # create the 10 default food categories
python manage.py fetch_test <url>  # fetch and print a receipt from a QR URL
python manage.py show_rules        # list all learned categorisation rules
```

## Running tests

```bash
python manage.py test receipts
```

32 unit tests covering both receipt XML schemas (check01 and RQ/Silpo), kopecks→UAH conversion, quantity unit detection, and error handling.

## Project structure

```
receiptlens/          Django project settings & URLs
receipts/
  models.py           Store, Category, Receipt, Item, CategoryRule
  views.py            All views (dashboard, scan, parse, save, analytics, manual)
  urls.py
  admin.py
  services/
    dps.py            QR URL parser + ДПС API client
    tax_check.py      XML parser for check01 and RQ schemas
    categorize.py     Category suggestion and rule learning
  management/commands/
    seed_categories.py
    fetch_test.py
    show_rules.py
  templates/receipts/
    base.html
    dashboard.html
    scan.html
    _review.html       HTMX partial — review form after fetch
    _review_error.html HTMX partial — error state
    receipt_detail.html
    analytics.html
    manual.html
static/
  js/scan.js          Camera (html5-qrcode) + file upload (jsQR) logic
  manifest.webmanifest
  sw.js               Service worker — offline app shell
```

## Production deployment

```bash
python manage.py collectstatic --no-input
gunicorn receiptlens.wsgi:application --bind 0.0.0.0:$PORT
```

Set `DEBUG=False`, a strong `SECRET_KEY`, `DATABASE_URL`, and `ALLOWED_HOSTS` in the environment. HTTPS is required in production for camera access.
