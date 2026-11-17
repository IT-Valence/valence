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


def _lrf(batch=BATCH, grade="bp", drums=((25, 3.5), (10, 1.25)), released=None, **kw):
	"""A Draft LRF as production fills it: drum no., net and tare only."""
	rows = [
		dict(drum_no=f"0{i}/0{len(drums)}", net_weight_kg=n, tare_weight_kg=t)
		for i, (n, t) in enumerate(drums, start=1)
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


def _step(doc, steps=1):
	for _ in range(steps):  # Draft -> Pending -> Sealed -> Approved (submits)
		doc = apply_workflow(frappe.get_doc(doc.doctype, doc.name), "Approve")
	return doc


def _qa(doc, container="Ok", seal="Ok", first_seal=10575):
	"""QA fills its columns on the same rows."""
	doc = frappe.get_doc(doc.doctype, doc.name)
	for i, row in enumerate(doc.label_details):
		row.seal_no = str(first_seal + i)
		row.container_condition = container
		row.seal_condition = seal
	doc.save()
	return doc


def _approve(doc):
	doc = _step(doc, 1)  # sent for approval: QA's turn
	_qa(doc)
	return _step(doc, 2)


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

	def test_unknown_grade_rejected(self):
		self.assertRaises(frappe.ValidationError, _lrf, grade="XYZ")

	def test_weights_totals_and_observed(self):
		doc = _lrf(drums=((25, 3.54), (8.4, 3.71)))
		self.assertEqual([r.gross_weight_kg for r in doc.label_details], [28.54, 12.11])
		# Observed starts equal to Gross; conditions and seals are left for QA
		self.assertEqual([r.observed_weight_kg for r in doc.label_details], [28.54, 12.11])
		self.assertEqual([(r.container_condition or "", r.seal_condition or "", r.seal_no or "") for r in doc.label_details],
			[("", "", ""), ("", "", "")])
		self.assertEqual((doc.total_net_weight, doc.total_gross_weight), (33.4, 40.65))
		self.assertEqual([r.no_of_labels_required for r in doc.label_details], [2, 2])

	def test_observed_follows_gross_while_draft(self):
		doc = _lrf()
		doc.label_details[0].tare_weight_kg = 3.6
		doc.save()
		self.assertEqual(doc.label_details[0].observed_weight_kg, 28.6)

	def test_qa_observed_mismatch_marks_container_not_ok(self):
		doc = _step(_lrf(), 1)
		doc.label_details[1].observed_weight_kg = 11.0  # gross is 11.25
		doc.save()
		self.assertEqual(doc.label_details[1].container_condition, "Not Ok")

	def test_container_tolerance(self):
		frappe.db.set_single_value("Labelling Settings", "container_weight_tolerance_kg", 0.3)
		doc = _step(_lrf(), 1)
		doc.label_details[1].observed_weight_kg = 11.0
		doc.save()
		self.assertFalse(doc.label_details[1].container_condition)  # within tolerance: QA decides

	def test_production_columns_locked_once_sent(self):
		doc = _step(_lrf(), 1)
		doc.label_details[0].net_weight_kg = 24
		self.assertRaises(frappe.ValidationError, doc.save)

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

	def test_qa_must_finish_before_final_approval(self):
		doc = _step(_lrf(), 2)  # Sealed, but QA columns empty
		self.assertRaises(frappe.ValidationError, _step, doc, 1)

	def test_duplicate_seal_refused(self):
		doc = _step(_lrf(), 1)
		doc = _qa(doc)
		doc.label_details[1].seal_no = doc.label_details[0].seal_no
		doc.save()
		doc = _step(doc, 1)
		self.assertRaises(frappe.ValidationError, _step, doc, 1)

	def test_not_ok_needs_qa_remark(self):
		doc = _step(_lrf(), 1)
		doc = _qa(doc, seal="Not Ok")
		doc = _step(doc, 1)
		self.assertRaises(frappe.ValidationError, _step, doc, 1)
		doc = frappe.get_doc(doc.doctype, doc.name)
		doc.remarks_qa = "Seal re-applied, checked by QA."
		doc.save()
		self.assertEqual(_step(doc, 1).docstatus, 1)

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
