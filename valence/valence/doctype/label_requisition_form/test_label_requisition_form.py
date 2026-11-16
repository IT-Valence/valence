# Copyright (c) 2025, finbyz tech and Contributors
# See license.txt

import frappe
from frappe.model.workflow import apply_workflow
from frappe.tests.utils import FrappeTestCase

ITEM = "_TEST-LRF-ITEM"
BATCH = "_TLRF-B1"
BAD_BATCH = "_TLRF-B2"


def _setup_masters():
	if not frappe.db.exists("Item", ITEM):
		frappe.get_doc(
			doctype="Item", item_code=ITEM, item_name="Test Drum Product", item_group="All Item Groups",
			stock_uom="Kg", is_stock_item=1, has_batch_no=1, custom_item_stage=1,
			gst_hsn_code=frappe.db.get_value("GST HSN Code", {}, "name"),
		).insert(ignore_permissions=True)
	for batch, mfg, retest in ((BATCH, "2026-10-01", "2031-09-30"), (BAD_BATCH, "2026-09-01", "2026-08-31")):
		if not frappe.db.exists("Batch", batch):
			frappe.get_doc(
				doctype="Batch", batch_id=batch, item=ITEM, manufacturing_date=mfg,
				retest_date=retest, no_of_packages=2,
			).insert(ignore_permissions=True)


def _master(label_no, label_type, status="Approved"):
	frappe.db.delete("Master Label", {"name": label_no})
	frappe.get_doc(
		doctype="Master Label", label_no=label_no, label_type=label_type, variant="Standard", grade="BP",
		item=ITEM, status=status, storage_condition="Store below 25 C" if label_type == "Product" else None,
	).insert(ignore_permissions=True)


def _lrf(batch=BATCH, grade="bp", drums=((25, 3.5, 28.5), (10, 1.25, 11.25)), released=None, **kw):
	rows = [
		dict(drum_no=f"0{i}/0{len(drums)}", net_weight_kg=n, tare_weight_kg=t, observed_weight_kg=o, seal_no=f"s{i}")
		for i, (n, t, o) in enumerate(drums, start=1)
	]
	doc = frappe.get_doc(
		dict(
			doctype="Label Requisition Form", production_item=ITEM, production_b_no=batch, grade=grade,
			released_b_no=" tphp00 9 ", released_qty_kgs=sum(d[0] for d in drums) if released is None else released,
			inspected_by="Administrator", label_details=rows, **kw,
		)
	)
	doc.flags.ignore_permissions = True
	return doc.insert()


def _approve(doc):
	for _ in range(3):  # Draft -> Pending -> Sealed -> Approved (submits)
		doc = apply_workflow(frappe.get_doc(doc.doctype, doc.name), "Approve")
	return doc


class TestLabelRequisitionForm(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_setup_masters()
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		frappe.db.rollback()
		for name in frappe.get_all("Label Requisition Form", {"production_item": ITEM}, pluck="name"):
			frappe.db.delete("Label Requisition Form Item", {"parent": name})
			frappe.db.delete("Label Requisition Form", {"name": name})
		frappe.db.delete("Master Label", {"item": ITEM})
		frappe.db.delete("Batch", {"item": ITEM})
		frappe.db.delete("Item", {"name": ITEM})
		frappe.db.commit()
		super().tearDownClass()

	def setUp(self):
		frappe.db.set_single_value("Labelling Settings", "require_master_label", 0)
		frappe.db.set_single_value("Labelling Settings", "container_weight_tolerance_kg", 0)

	def tearDown(self):
		frappe.db.rollback()

	def test_text_is_cleaned(self):
		doc = _lrf()
		self.assertEqual(doc.grade, "BP")
		self.assertEqual(doc.released_b_no, "TPHP00 9")
		self.assertEqual(doc.label_details[0].seal_no, "S1")

	def test_unknown_grade_rejected(self):
		self.assertRaises(frappe.ValidationError, _lrf, grade="XYZ")

	def test_weights_and_totals(self):
		doc = _lrf(drums=((25, 3.54, 28.54), (8.4, 3.71, 12.0)))
		self.assertEqual(doc.label_details[0].gross_weight_kg, 28.54)
		self.assertEqual(doc.label_details[0].container_condition, "Ok")
		self.assertEqual(doc.label_details[1].gross_weight_kg, 12.11)
		self.assertEqual(doc.label_details[1].container_condition, "Not Ok")
		self.assertEqual(doc.total_net_weight, 33.4)
		self.assertEqual(doc.total_gross_weight, 40.65)

	def test_container_tolerance(self):
		frappe.db.set_single_value("Labelling Settings", "container_weight_tolerance_kg", 0.2)
		doc = _lrf(drums=((8.4, 3.71, 12.0),))
		self.assertEqual(doc.label_details[0].container_condition, "Ok")

	def test_header_filled_from_batch(self):
		doc = _lrf()
		self.assertEqual(str(doc.manufacturing_date), "2026-10-01")
		self.assertEqual(str(doc.retestexpiry_date), "2031-09-30")

	def test_label_numbers_from_master_label(self):
		_master("L981-00", "Product")
		_master("L982-00", "Identification")
		_master("L983-00", "Identification", status="Draft")  # not approved, ignored
		doc = _lrf()
		self.assertEqual((doc.product_label_no, doc.identification_label_no), ("L981-00", "L982-00"))

	def test_approval_happy_path(self):
		doc = _approve(_lrf())
		self.assertEqual((doc.docstatus, doc.workflow_state), (1, "Approved"))

	def test_bad_dates_save_but_cannot_approve(self):
		doc = _lrf(batch=BAD_BATCH)  # saving is allowed (warning only)
		self.assertRaises(frappe.ValidationError, _approve, doc)

	def test_released_qty_must_match_net(self):
		doc = _lrf(released=30)
		self.assertRaises(frappe.ValidationError, _approve, doc)

	def test_require_master_label_setting(self):
		frappe.db.delete("Master Label", {"item": ITEM})
		frappe.db.set_single_value("Labelling Settings", "require_master_label", 1)
		self.assertRaises(frappe.ValidationError, _approve, _lrf())
		_master("L981-00", "Product")
		doc = _approve(_lrf())
		self.assertEqual(doc.product_label_no, "L981-00")
