import base64
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch, MagicMock

from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from receipts.models import PendingImport, PENDING_IMPORT_RETRY_DELAYS_HOURS
from receipts.services.dps import is_cabinet_check_url
from receipts.services.pending import process_pending_import, _next_retry_at, _record_failure
from receipts.services.tax_check import parse_chk_response, Receipt, Item

User = get_user_model()


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
      <P TX="1" CD="1234567890" SM="4833" NM="ВБ МАФIН" C="999" Q="0" N="3" PRC="0"/>
      <E SM="18707" TS="20260529193437" FN="4000530835" N="4"/>
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
        # SM="18707" → 187.07 UAH
        self.assertEqual(self.receipt.total, Decimal("187.07"))

    def test_item_count(self):
        self.assertEqual(len(self.receipt.items), 3)

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

    def test_zero_qty_item_treated_as_single_unit(self):
        # METRO stores Q=0 PRC=0 for single-unit items; parser should default qty=1
        muffin = self.receipt.items[2]
        self.assertEqual(muffin.qty, Decimal("1"))
        self.assertEqual(muffin.unit, "pcs")
        self.assertEqual(muffin.unit_price, Decimal("48.33"))
        self.assertEqual(muffin.total, Decimal("48.33"))


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


# ---------------------------------------------------------------------------
# QR URL validation
# ---------------------------------------------------------------------------

class CabinetUrlValidationTests(TestCase):

    def test_valid_cabinet_url_accepted(self):
        self.assertTrue(is_cabinet_check_url(
            "https://cabinet.tax.gov.ua/cashregs/check?date=20260604&time=193036&id=313638&sm=5038.40&fn=3000994889"
        ))

    def test_cabinet_prefix_only_is_accepted(self):
        self.assertTrue(is_cabinet_check_url("https://cabinet.tax.gov.ua/cashregs/"))

    def test_wrong_domain_rejected(self):
        self.assertFalse(is_cabinet_check_url("https://evil.com/cashregs/check?id=1"))

    def test_http_rejected(self):
        self.assertFalse(is_cabinet_check_url("http://cabinet.tax.gov.ua/cashregs/check?id=1"))

    def test_other_path_rejected(self):
        self.assertFalse(is_cabinet_check_url("https://cabinet.tax.gov.ua/ws/api_public/something"))

    def test_empty_url_rejected(self):
        self.assertFalse(is_cabinet_check_url(""))

    def test_random_qr_rejected(self):
        self.assertFalse(is_cabinet_check_url("https://some-shop.ua/loyalty?code=ABC123"))


# ---------------------------------------------------------------------------
# PendingImport retry scheduling
# ---------------------------------------------------------------------------

class PendingImportSchedulingTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username="test@ex.com", email="test@ex.com", password="pw")

    def _make_pi(self, retry_count=0):
        pi = PendingImport.objects.create(
            user=self.user,
            qr_url="https://cabinet.tax.gov.ua/cashregs/check?id=1&fn=2&sm=3&date=20260101&time=120000",
        )
        pi.retry_count = retry_count
        return pi

    def test_initial_retry_delay_is_2_hours(self):
        self.assertEqual(PENDING_IMPORT_RETRY_DELAYS_HOURS[0], 2)

    def test_next_retry_at_increments_correctly(self):
        delays = PENDING_IMPORT_RETRY_DELAYS_HOURS
        pi = self._make_pi(retry_count=0)
        pi.save()
        for i, expected_hours in enumerate(delays):
            pi.retry_count = i
            nxt = _next_retry_at(pi)
            self.assertEqual(nxt, pi.created_at + timedelta(hours=expected_hours))

    def test_next_retry_at_returns_none_after_all_retries(self):
        pi = self._make_pi(retry_count=len(PENDING_IMPORT_RETRY_DELAYS_HOURS))
        pi.save()
        self.assertIsNone(_next_retry_at(pi))

    def test_record_failure_increments_retry_count(self):
        pi = self._make_pi()
        pi.save()
        _record_failure(pi, "timeout")
        pi.refresh_from_db()
        self.assertEqual(pi.retry_count, 1)
        self.assertEqual(pi.status, PendingImport.PENDING)

    def test_record_failure_marks_failed_after_last_retry(self):
        pi = self._make_pi()
        pi.save()
        for _ in range(len(PENDING_IMPORT_RETRY_DELAYS_HOURS)):
            _record_failure(pi, "timeout")
            pi.refresh_from_db()
        self.assertEqual(pi.status, PendingImport.FAILED)
        self.assertIsNone(pi.next_retry_at)

    def test_record_failure_stores_error_message(self):
        pi = self._make_pi()
        pi.save()
        _record_failure(pi, "Connection refused")
        pi.refresh_from_db()
        self.assertEqual(pi.last_error, "Connection refused")


# ---------------------------------------------------------------------------
# PendingImport processing service
# ---------------------------------------------------------------------------

FAKE_CHECK_DATA = {"checkXml": CHECK01_XML}


class PendingImportProcessingTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username="u@ex.com", email="u@ex.com", password="pw")
        self.pi = PendingImport.objects.create(
            user=self.user,
            qr_url="https://cabinet.tax.gov.ua/cashregs/check?id=1&fn=2&sm=316.66&date=20260602&time=211044",
        )
        self.pi.next_retry_at = self.pi.created_at + timedelta(hours=2)
        self.pi.save(update_fields=["next_retry_at"])

    @patch("receipts.services.pending.fetch_check", return_value=FAKE_CHECK_DATA)
    @patch("receipts.services.pending.parse_qr_url", return_value={"id": "1", "fn": "2", "sm": "316.66", "api_date": "2026-06-02 21:10:44"})
    def test_successful_processing_creates_receipt(self, mock_parse_url, mock_fetch):
        result = process_pending_import(self.pi)
        self.assertTrue(result)
        self.pi.refresh_from_db()
        self.assertEqual(self.pi.status, PendingImport.PROCESSED)
        self.assertIsNotNone(self.pi.receipt)
        self.assertEqual(self.pi.receipt.user, self.user)

    @patch("receipts.services.pending.fetch_check", return_value=FAKE_CHECK_DATA)
    @patch("receipts.services.pending.parse_qr_url", return_value={"id": "1", "fn": "2", "sm": "316.66", "api_date": "2026-06-02 21:10:44"})
    def test_successful_processing_sets_source_to_pending(self, mock_parse_url, mock_fetch):
        process_pending_import(self.pi)
        self.pi.refresh_from_db()
        self.assertEqual(self.pi.receipt.source, "pending")

    @patch("receipts.services.pending.fetch_check", side_effect=ValueError("Чек не знайдено"))
    @patch("receipts.services.pending.parse_qr_url", return_value={"id": "1", "fn": "2", "sm": "316.66", "api_date": "2026-06-02 21:10:44"})
    def test_failed_fetch_reschedules(self, mock_parse_url, mock_fetch):
        result = process_pending_import(self.pi)
        self.assertFalse(result)
        self.pi.refresh_from_db()
        self.assertEqual(self.pi.status, PendingImport.PENDING)
        self.assertEqual(self.pi.retry_count, 1)
        self.assertIsNotNone(self.pi.next_retry_at)

    @patch("receipts.services.pending.fetch_check", side_effect=ValueError("not found"))
    @patch("receipts.services.pending.parse_qr_url", return_value={"id": "1", "fn": "2", "sm": "316.66", "api_date": "2026-06-02 21:10:44"})
    def test_claim_prevents_double_processing(self, mock_parse_url, mock_fetch):
        # Simulate that import is already being processed
        PendingImport.objects.filter(pk=self.pi.pk).update(status=PendingImport.PROCESSING)
        result = process_pending_import(self.pi)
        self.assertFalse(result)
        # fetch should NOT have been called because claim failed
        mock_fetch.assert_not_called()

    @patch("receipts.services.pending.fetch_check", return_value=FAKE_CHECK_DATA)
    @patch("receipts.services.pending.parse_qr_url", return_value={"id": "1", "fn": "2", "sm": "316.66", "api_date": "2026-06-02 21:10:44"})
    def test_failed_import_can_be_manually_retried(self, mock_parse_url, mock_fetch):
        PendingImport.objects.filter(pk=self.pi.pk).update(status=PendingImport.FAILED, retry_count=5)
        self.pi.refresh_from_db()
        result = process_pending_import(self.pi)
        self.assertTrue(result)
        self.pi.refresh_from_db()
        self.assertEqual(self.pi.status, PendingImport.PROCESSED)


# ---------------------------------------------------------------------------
# Pending import views
# ---------------------------------------------------------------------------

class PendingImportViewTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username="v@ex.com", email="v@ex.com", password="pw")
        self.client = Client()
        self.client.login(username="v@ex.com", password="pw")

    def test_save_creates_pending_import(self):
        url = "https://cabinet.tax.gov.ua/cashregs/check?id=1&fn=2&sm=3&date=20260101&time=120000"
        resp = self.client.post(reverse("pending_import_save"), {"qr_url": url})
        self.assertRedirects(resp, reverse("pending_imports"))
        self.assertEqual(PendingImport.objects.filter(user=self.user, qr_url=url).count(), 1)

    def test_save_sets_initial_next_retry(self):
        url = "https://cabinet.tax.gov.ua/cashregs/check?id=1&fn=2&sm=3&date=20260101&time=120000"
        self.client.post(reverse("pending_import_save"), {"qr_url": url})
        pi = PendingImport.objects.get(user=self.user, qr_url=url)
        expected_delay = timedelta(hours=PENDING_IMPORT_RETRY_DELAYS_HOURS[0])
        delta = pi.next_retry_at - pi.created_at
        self.assertAlmostEqual(delta.total_seconds(), expected_delay.total_seconds(), delta=5)

    def test_save_rejects_duplicate_pending(self):
        url = "https://cabinet.tax.gov.ua/cashregs/check?id=1&fn=2&sm=3&date=20260101&time=120000"
        self.client.post(reverse("pending_import_save"), {"qr_url": url})
        self.client.post(reverse("pending_import_save"), {"qr_url": url})
        self.assertEqual(PendingImport.objects.filter(user=self.user, qr_url=url).count(), 1)

    def test_save_rejects_unsupported_url(self):
        resp = self.client.post(reverse("pending_import_save"), {"qr_url": "https://evil.com/qr"})
        self.assertRedirects(resp, reverse("scan"))
        self.assertEqual(PendingImport.objects.count(), 0)

    def test_delete_removes_import(self):
        pi = PendingImport.objects.create(
            user=self.user,
            qr_url="https://cabinet.tax.gov.ua/cashregs/check?id=9&fn=9&sm=9&date=20260101&time=120000",
        )
        resp = self.client.post(reverse("pending_import_delete", args=[pi.pk]))
        self.assertRedirects(resp, reverse("pending_imports"))
        self.assertFalse(PendingImport.objects.filter(pk=pi.pk).exists())

    def test_delete_requires_ownership(self):
        other = User.objects.create_user(username="other@ex.com", email="other@ex.com", password="pw")
        pi = PendingImport.objects.create(
            user=other,
            qr_url="https://cabinet.tax.gov.ua/cashregs/check?id=8&fn=8&sm=8&date=20260101&time=120000",
        )
        resp = self.client.post(reverse("pending_import_delete", args=[pi.pk]))
        self.assertEqual(resp.status_code, 404)

    def test_list_requires_login(self):
        self.client.logout()
        resp = self.client.get(reverse("pending_imports"))
        self.assertEqual(resp.status_code, 302)

    def test_unsupported_qr_shows_error_in_parse(self):
        resp = self.client.post(
            reverse("receipts_parse"),
            {"qr_url": "https://silpo.ua/qr/abc123"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Непідтримуваний тип QR-коду")


# ---------------------------------------------------------------------------
# receipts_parse view — successful-fetch behaviour
# ---------------------------------------------------------------------------

class ReceiptsParseViewTests(TestCase):
    """
    Guards against silent regressions in receipts_parse where any exception
    inside the broad try/except (e.g. a template syntax error, a bad DB query)
    causes the view to return the pending-import prompt instead of the review form.
    """

    def setUp(self):
        self.user = User.objects.create_user(username="parse@ex.com", password="pw")
        self.client = Client()
        self.client.login(username="parse@ex.com", password="pw")

    def test_review_template_loads_without_syntax_errors(self):
        from django.template import Engine, TemplateSyntaxError
        try:
            Engine.get_default().get_template("receipts/_review.html")
        except TemplateSyntaxError as exc:
            self.fail(f"_review.html has a template syntax error: {exc}")

    @patch("receipts.views.fetch_check", return_value=FAKE_CHECK_DATA)
    def test_successful_fetch_renders_review_form(self, _mock_fetch):
        """fetch succeeds → review form is shown, not the pending-import prompt."""
        resp = self.client.post(
            reverse("receipts_parse"),
            {"qr_url": "https://cabinet.tax.gov.ua/cashregs/check"
                       "?id=175285&fn=4000935353&sm=316.66&date=20260602&time=211044"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Зберегти чек")
        self.assertNotContains(resp, "Зберегти для повторної спроби")

    @patch("receipts.views.fetch_check", return_value=FAKE_CHECK_DATA)
    def test_successful_fetch_does_not_create_pending_import(self, _mock_fetch):
        """A working fetch must never create a PendingImport record."""
        self.client.post(
            reverse("receipts_parse"),
            {"qr_url": "https://cabinet.tax.gov.ua/cashregs/check"
                       "?id=175285&fn=4000935353&sm=316.66&date=20260602&time=211044"},
        )
        self.assertEqual(PendingImport.objects.count(), 0)
