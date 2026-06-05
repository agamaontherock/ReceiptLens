from urllib.parse import urlparse, parse_qs
import httpx

CHK_URL = "https://cabinet.tax.gov.ua/ws/api_public/rro/chkAllWeb"
CABINET_CHECK_PREFIX = "https://cabinet.tax.gov.ua/cashregs/"


def is_cabinet_check_url(url: str) -> bool:
    return url.startswith(CABINET_CHECK_PREFIX)
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
