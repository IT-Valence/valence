import frappe
from frappe.tests.utils import FrappeTestCase

from valence.valence.labelling.drum_labels import get_drum_labels

ITEM = "_TEST-DL-ITEM"


def _item():
	if not frappe.db.exists("Item", ITEM):
		frappe.get_doc(
			doctype="Item", item_code=ITEM, item_name="Test sticker product", item_group="All Item Groups",
			stock_uom="Kg", is_stock_item=0, custom_item_stage=1,
			gst_hsn_code=frappe.db.get_value("GST HSN Code", {}, "name"),
		).insert(ignore_permissions=True)


def _master(label_no, label_type, variant="Standard", grade="IP", **kw):
	frappe.db.delete("Master Label", {"name": label_no})
	frappe.get_doc(
		doctype="Master Label", label_no=label_no, label_type=label_type, variant=variant, grade=grade,
		item=ITEM, status="Approved", storage_condition="Keep cool." if label_type == "Product" else None,
		neutral_label_no="PB/DRUGS/2014-OSP" if variant == "Neutral" else None, **kw,
	).insert(ignore_permissions=True)


def _doc(grade="ip", docstatus=1, variant="Standard", drums=2, as_json=False):
	"""An LRF as the printer-icon preview sends it: plain dict, dates as strings."""
	data = {
		"doctype": "Label Requisition Form", "name": "LRF-TEST", "docstatus": docstatus,
		"production_item": ITEM, "grade": grade, "label_variant": variant,
		"released_b_no": " tphp0062 26 ", "manufacturing_date": "2026-10-01", "retestexpiry_date": "2031-09-30",
		"label_details": [
			{"idx": i, "drum_no": f"0{i}/0{drums}", "net_weight_kg": 25, "tare_weight_kg": 3.54, "gross_weight_kg": 28.54}
			for i in range(1, drums + 1)
		],
	}
	return frappe.as_json(data) if as_json else frappe._dict(data)


class TestDrumLabels(FrappeTestCase):
	def setUp(self):
		_item()
		for f, v in (
			("product_labels_per_drum", 1), ("identification_labels_per_drum", 1),
			("product_labels_per_page", 2), ("page_layout", "A4 Sheet"),
		):
			frappe.db.set_single_value("Labelling Settings", f, v)
		frappe.clear_document_cache("Labelling Settings", "Labelling Settings")

	def tearDown(self):
		frappe.db.rollback()
		frappe.clear_document_cache("Labelling Settings", "Labelling Settings")

	def test_all_labels_in_order_with_master_data(self):
		_master("L971-00", "Product")
		_master("L972-00", "Identification")
		data = get_drum_labels(_doc(as_json=True), "all")  # JSON string, like the printer-icon preview
		kinds = [l["kind"] for l in data["labels"]]
		# printed as two groups: all Identification stickers, then all Product stickers
		self.assertEqual(kinds, ["identification", "identification", "product", "product"])
		self.assertEqual([l["drum"] for l in data["identification_labels"]], ["1 of 2", "2 of 2"])
		p = data["labels"][2]
		self.assertEqual(p["title"], "TEST STICKER PRODUCT (IP)")
		self.assertEqual(p["batch_no"], "TPHP0062 26")
		self.assertEqual((p["mfg"], p["retest"]), ("OCT-2026", "SEP-2031"))
		self.assertEqual((p["gross"], p["tare"], p["net"]), ("28.540", "3.540", "25.000"))
		self.assertEqual((p["drum"], p["label_no"], p["storage"]), ("1 of 2", "L971-00", "Keep cool."))
		self.assertTrue(p["qr"].startswith("data:image/png;base64,"))  # made in the ERP, no outside site
		self.assertEqual(data["labels"][0]["label_no"], "L972-00")
		self.assertEqual(data["watermark"], "")

	def test_product_stickers_two_per_page(self):
		data = get_drum_labels(_doc(drums=5), "all")
		self.assertEqual([len(p) for p in data["product_pages"]], [2, 2, 1])
		self.assertEqual(len(data["identification_labels"]), 5)
		html = frappe.render_template(
			'{% set which = "all" %}{% include "valence/templates/labels/drum_labels.html" %}', {"doc": _doc(drums=5)}
		)
		self.assertEqual(html.count("vl-new-page"), 3 + 1)  # 3 product pages, each starts a new page (+ css rule)

	def test_gross_is_qa_observed_weight(self):
		doc = _doc()
		doc.label_details[0]["observed_weight_kg"] = 28.6
		p = get_drum_labels(doc, "product")["labels"][0]
		self.assertEqual((p["gross"], p["net"]), ("28.600", "25.000"))

	def test_only_product_and_copies(self):
		_master("L971-00", "Product")
		frappe.db.set_single_value("Labelling Settings", "product_labels_per_drum", 2)
		frappe.clear_document_cache("Labelling Settings", "Labelling Settings")
		data = get_drum_labels(_doc(drums=3), "product")
		self.assertEqual(len(data["labels"]), 6)
		self.assertEqual({l["kind"] for l in data["labels"]}, {"product"})

	def test_watermarks(self):
		_master("L971-00", "Product")
		self.assertEqual(get_drum_labels(_doc(docstatus=0), "product")["watermark"], "NOT APPROVED - DO NOT USE")
		self.assertEqual(get_drum_labels(_doc(grade="BP"), "product")["watermark"], "NO APPROVED MASTER LABEL")

	def test_no_master_label_falls_back_to_item_name(self):
		data = get_drum_labels(_doc(grade="usp"), "product")
		self.assertEqual(data["labels"][0]["title"], "TEST STICKER PRODUCT (USP)")
		self.assertEqual(data["labels"][0]["label_no"], "")

	def test_grade_na_has_no_suffix(self):
		_master("L973-00", "Product", grade="NA", printed_title="Some Base")
		data = get_drum_labels(_doc(grade="NA"), "product")
		self.assertEqual(data["labels"][0]["title"], "SOME BASE")

	def test_neutral_label_hides_manufacturer(self):
		_master("L974-00", "Product", variant="Neutral")
		label = get_drum_labels(_doc(variant="Neutral"), "product")["labels"][0]
		self.assertEqual((label["show_manufacturer"], label["neutral_label_no"]), (0, "PB/DRUGS/2014-OSP"))

	def test_template_renders_for_both_routes(self):
		_master("L971-00", "Product")
		html = frappe.render_template(
			'{% set which = "all" %}{% include "valence/templates/labels/drum_labels.html" %}', {"doc": _doc()}
		)
		self.assertIn("Label No.:</span> L971-00", html)
		self.assertIn("OCT-2026", html)
		self.assertNotIn("api.qrserver.com", html)
