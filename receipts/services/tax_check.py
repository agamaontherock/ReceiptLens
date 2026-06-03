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


def _unit(qty: Decimal) -> str:
    return "kg" if qty != qty.to_integral_value() else "pcs"


def _parse_check01(root: ET.Element) -> Receipt:
    """Original check01.xsd schema: CHECKHEAD / CHECKBODY/ROW / CHECKTOTAL."""
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
            unit=_unit(qty),
            unit_price=Decimal(g(row, "PRICE")),
            total=Decimal(g(row, "COST")),
        ))
    return receipt


def _parse_rq(root: ET.Element) -> Receipt:
    """Compact RQ schema used by Silpo and some other chains.

    Amounts are in kopecks (÷100 = UAH), quantities in thousandths (÷1000).
    Store name is not embedded; org_legal_name carries the tax number instead.
    """
    dat = root.find("DAT")
    if dat is None:
        raise ValueError("RQ schema: missing <DAT> element")

    fiscal_rro = dat.get("FN", "")
    order_num = dat.get("DI", "")
    tax_num = dat.get("TN", "")

    # Timestamp and total are in <C/E ...>
    c_el = dat.find("C")
    e_el = c_el.find("E") if c_el is not None else None
    if e_el is None:
        raise ValueError("RQ schema: missing <E> element with totals")

    ts = e_el.get("TS", "")  # YYYYMMDDHHmmss
    receipt_dt = (f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]} {ts[8:10]}:{ts[10:12]}:{ts[12:14]}"
                  if len(ts) >= 14 else "")
    total = Decimal(e_el.get("SM", "0")) / 100

    receipt = Receipt(
        org_legal_name=tax_num,
        point_name="",
        point_address="",
        fiscal_rro=fiscal_rro,
        order_num=order_num,
        datetime=receipt_dt,
        total=total,
    )

    if c_el is not None:
        for p_el in c_el.findall("P"):
            qty = Decimal(p_el.get("Q", "0")) / 1000
            unit_price = Decimal(p_el.get("PRC", "0")) / 100
            item_total = Decimal(p_el.get("SM", "0")) / 100
            receipt.items.append(Item(
                code=p_el.get("C", ""),
                barcode=p_el.get("CD", ""),
                name=p_el.get("NM", ""),
                qty=qty,
                unit=_unit(qty),
                unit_price=unit_price,
                total=item_total,
            ))
    return receipt


def parse_chk_response(data: dict) -> Receipt:
    if not data.get("checkXml"):
        raise ValueError(f"No checkXml in response (resultText={data.get('resultText')!r})")

    root = _decode_check_xml(data["checkXml"])

    if root.tag == "RQ":
        return _parse_rq(root)
    return _parse_check01(root)
