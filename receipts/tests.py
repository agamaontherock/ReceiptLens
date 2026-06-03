import base64
from decimal import Decimal
from django.test import TestCase

from receipts.services.tax_check import parse_chk_response, Receipt, Item


def _b64(xml_body: str) -> str:
    """Prepend XML declaration and base64-encode as cp1251 (matches real API format)."""
    full = f'<?xml version="1.0" encoding="windows-1251"?>{xml_body}'
    return base64.b64encode(full.encode("cp1251")).decode("ascii")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

CHECK01_XML = _b64("""
<CHECK>
  <CHECKHEAD>
    <ORGNM>ТОВ &quot;МЕРЕЖА-СЕРВІС ЛЬВІВ&quot;</ORGNM>
    <POINTNM>Магазин</POINTNM>
    <POINTADDR>м. Львів, вул. Пасічна, 166</POINTADDR>
    <CASHREGISTERNUM>4000935353</CASHREGISTERNUM>
    <ORDERNUM>175285</ORDERNUM>
    <ORDERDATE>02062026</ORDERDATE>
    <ORDERTIME>211044</ORDERTIME>
  </CHECKHEAD>
  <CHECKBODY>
    <ROW>
      <CODE>001</CODE>
      <BARCODE>4820000000001</BARCODE>
      <NAME>Молоко 2,5%</NAME>
      <AMOUNT>2.000</AMOUNT>
      <PRICE>39.99</PRICE>
      <COST>79.98</COST>
      <LETTERS>А</LETTERS>
    </ROW>
    <ROW>
      <CODE>002</CODE>
      <BARCODE></BARCODE>
      <NAME>Сардельки вагові</NAME>
      <AMOUNT>0.488</AMOUNT>
      <PRICE>485.00</PRICE>
      <COST>236.68</COST>
      <LETTERS>А</LETTERS>
    </ROW>
  </CHECKBODY>
  <CHECKTOTAL>
    <SUM>316.66</SUM>
  </CHECKTOTAL>
</CHECK>
""")

RQ_XML = _b64("""
<RQ V="1">
  <DAT TN="407201926538" DI="371121" ZN="" FN="4000530835" V="1">
    <C T="0">
      <P TX="1" CD="2732485" SM="7175" NM="БананКг" C="32485" Q="958" N="1" PRC="7490"/>
      <P TX="1" CD="4820272180196" SM="6699" NM="Мол840ПастерФерм2,5%" C="837449" Q="1000" N="2" PRC="6699"/>
      <E SM="13874" TS="20260529193437" FN="4000530835" N="3"/>
    </C>
    <TS/>
  </DAT>
  <MAC/>
</RQ>
""")


# ---------------------------------------------------------------------------
# check01 schema tests
# ---------------------------------------------------------------------------

class Check01SchemaTests(TestCase):

    def setUp(self):
        self.receipt = parse_chk_response({"checkXml": CHECK01_XML})

    def test_returns_receipt_instance(self):
        self.assertIsInstance(self.receipt, Receipt)

    def test_org_legal_name(self):
        self.assertEqual(self.receipt.org_legal_name, 'ТОВ "МЕРЕЖА-СЕРВІС ЛЬВІВ"')

    def test_point_name(self):
        self.assertEqual(self.receipt.point_name, "Магазин")

    def test_point_address(self):
        self.assertIn("Пасічна", self.receipt.point_address)

    def test_fiscal_rro(self):
        self.assertEqual(self.receipt.fiscal_rro, "4000935353")

    def test_order_num(self):
        self.assertEqual(self.receipt.order_num, "175285")

    def test_datetime_format(self):
        # ORDERDATE=02062026 ORDERTIME=211044 → 2026-06-02 21:10:44
        self.assertEqual(self.receipt.datetime, "2026-06-02 21:10:44")

    def test_total(self):
        self.assertEqual(self.receipt.total, Decimal("316.66"))

    def test_item_count(self):
        self.assertEqual(len(self.receipt.items), 2)

    def test_item_with_barcode(self):
        item = self.receipt.items[0]
        self.assertEqual(item.barcode, "4820000000001")
        self.assertEqual(item.name, "Молоко 2,5%")
        self.assertEqual(item.qty, Decimal("2.000"))
        self.assertEqual(item.unit, "pcs")
        self.assertEqual(item.unit_price, Decimal("39.99"))
        self.assertEqual(item.total, Decimal("79.98"))

    def test_item_without_barcode(self):
        item = self.receipt.items[1]
        self.assertEqual(item.barcode, "")
        self.assertEqual(item.name, "Сардельки вагові")

    def test_fractional_qty_is_kg(self):
        item = self.receipt.items[1]  # qty=0.488
        self.assertEqual(item.unit, "kg")
        self.assertEqual(item.qty, Decimal("0.488"))

    def test_integer_qty_is_pcs(self):
        item = self.receipt.items[0]  # qty=2.000
        self.assertEqual(item.unit, "pcs")


# ---------------------------------------------------------------------------
# RQ schema tests (Silpo)
# ---------------------------------------------------------------------------

class RQSchemaTests(TestCase):

    def setUp(self):
        self.receipt = parse_chk_response({"checkXml": RQ_XML})

    def test_returns_receipt_instance(self):
        self.assertIsInstance(self.receipt, Receipt)

    def test_fiscal_rro(self):
        self.assertEqual(self.receipt.fiscal_rro, "4000530835")

    def test_org_legal_name_is_tax_number(self):
        # RQ schema has no org name — tax number is used instead
        self.assertEqual(self.receipt.org_legal_name, "407201926538")

    def test_order_num(self):
        self.assertEqual(self.receipt.order_num, "371121")

    def test_datetime_format(self):
        # TS="20260529193437" → 2026-05-29 19:34:37
        self.assertEqual(self.receipt.datetime, "2026-05-29 19:34:37")

    def test_total_converted_from_kopecks(self):
        # SM="13874" → 138.74 UAH
        self.assertEqual(self.receipt.total, Decimal("138.74"))

    def test_item_count(self):
        self.assertEqual(len(self.receipt.items), 2)

    def test_item_price_converted_from_kopecks(self):
        # PRC="7490" → 74.90 UAH
        banana = self.receipt.items[0]
        self.assertEqual(banana.unit_price, Decimal("74.90"))

    def test_item_total_converted_from_kopecks(self):
        # SM="7175" → 71.75 UAH
        banana = self.receipt.items[0]
        self.assertEqual(banana.total, Decimal("71.75"))

    def test_item_qty_converted_from_thousandths(self):
        # Q="958" → 0.958
        banana = self.receipt.items[0]
        self.assertEqual(banana.qty, Decimal("0.958"))

    def test_fractional_qty_is_kg(self):
        banana = self.receipt.items[0]  # Q=958 → 0.958
        self.assertEqual(banana.unit, "kg")

    def test_integer_qty_is_pcs(self):
        milk = self.receipt.items[1]  # Q=1000 → 1.000
        self.assertEqual(milk.unit, "pcs")

    def test_item_barcode(self):
        banana = self.receipt.items[0]
        self.assertEqual(banana.barcode, "2732485")

    def test_item_name(self):
        banana = self.receipt.items[0]
        self.assertEqual(banana.name, "БананКг")


# ---------------------------------------------------------------------------
# Schema dispatch and error handling
# ---------------------------------------------------------------------------

class ParseDispatchTests(TestCase):

    def test_missing_check_xml_raises(self):
        with self.assertRaises(ValueError):
            parse_chk_response({})

    def test_empty_check_xml_raises(self):
        with self.assertRaises(ValueError):
            parse_chk_response({"checkXml": ""})

    def test_result_text_included_in_error(self):
        with self.assertRaises(ValueError) as ctx:
            parse_chk_response({"resultText": "Чек не знайдено"})
        self.assertIn("Чек не знайдено", str(ctx.exception))

    def test_check01_dispatched_by_root_tag(self):
        receipt = parse_chk_response({"checkXml": CHECK01_XML})
        # check01 receipts have a human-readable org name, not a tax number
        self.assertIn("ТОВ", receipt.org_legal_name)

    def test_rq_dispatched_by_root_tag(self):
        receipt = parse_chk_response({"checkXml": RQ_XML})
        # RQ receipts store the tax number as org_legal_name
        self.assertTrue(receipt.org_legal_name.isdigit())
