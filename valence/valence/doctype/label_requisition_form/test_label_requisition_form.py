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


def _user(email, *roles):
	if not frappe.db.exists("User", email):
		frappe.get_doc(doctype="User", email=email, first_name=email.split("@")[0], send_welcome_email=0,
			roles=[{"role": r} for r in roles]).insert(ignore_permissions=True)
	return email


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

	def test_qa_sends_back_for_wrong_weights(self):
		doc = _step(_lrf(), 1)  # with QA
		doc = _qa(doc)
		send_back = lambda d: apply_workflow(frappe.get_doc(d.doctype, d.name), "Send Back")
		self.assertRaises(frappe.ValidationError, send_back, doc)  # reason needed
		doc = frappe.get_doc(doc.doctype, doc.name)
		doc.remarks_qa = "Drum 02/02 net weight is 9.8 kg on our scale."
		doc.save()
		doc = send_back(doc)
		self.assertEqual(doc.workflow_state, "Draft")
		self.assertEqual([r.container_condition or "" for r in doc.label_details], ["", ""])  # QA re-checks
		self.assertTrue(frappe.db.exists("Comment", {"reference_name": doc.name, "content": ["like", "%Sent back%9.8 kg%"]}))
		# production corrects the weight and sends it again
		doc = frappe.get_doc(doc.doctype, doc.name)
		doc.label_details[1].net_weight_kg = 9.8
		doc.released_qty_kgs = 34.8
		doc.save()
		self.assertEqual(doc.label_details[1].observed_weight_kg, 11.05)  # follows the corrected gross
		doc = _step(doc, 1)
		_qa(doc)
		self.assertEqual(_step(doc, 2).docstatus, 1)

	def test_send_back_from_sealed(self):
		doc = _step(_qa(_step(_lrf(), 1)), 1)  # Sealed
		doc = frappe.get_doc(doc.doctype, doc.name)
		doc.remarks_qa = "Re-weigh drum 01/02."
		doc.save()
		self.assertEqual(apply_workflow(doc, "Send Back").workflow_state, "Draft")

	def test_roles_like_the_demo(self):
		"""Production (Manufacturing User) prepares; QA (Stock Manager) checks, sends back, approves."""
		prod = _user("lrf-prod@test.local", "Manufacturing User", "Stock User")
		qa = _user("lrf-qa@test.local", "Stock Manager", "Stock User")
		doc = _step(_lrf(), 1)  # with QA
		try:
			frappe.set_user(prod)
			self.assertRaises(Exception, apply_workflow, frappe.get_doc(doc.doctype, doc.name), "Send Back")
			frappe.set_user(qa)
			d = frappe.get_doc(doc.doctype, doc.name)
			d.remarks_qa = "Re-weigh drum 01/02."
			d.save()
			self.assertEqual(apply_workflow(frappe.get_doc(doc.doctype, doc.name), "Send Back").workflow_state, "Draft")
		finally:
			frappe.set_user("Administrator")

	def test_production_cannot_fill_qa_columns(self):
		if not frappe.get_meta("Label Requisition Form Item").get_field("seal_no").permlevel:
			return self.skipTest("site has no field levels on the drum table")
		prod = _user("lrf-prod@test.local", "Manufacturing User", "Stock User")
		doc = _lrf()
		try:
			frappe.set_user(prod)
			d = frappe.get_doc(doc.doctype, doc.name)
			d.label_details[0].seal_no = "99999"
			d.save()
			self.assertFalse(frappe.db.get_value("Label Requisition Form Item", d.label_details[0].name, "seal_no"))
		finally:
			frappe.set_user("Administrator")

	def test_cannot_seal_without_seal_numbers(self):
		doc = _step(_lrf(), 1)  # Pending, QA has not entered seals
		self.assertRaises(frappe.ValidationError, _step, doc, 1)

	def test_qa_must_finish_before_final_approval(self):
		doc = _step(_lrf(), 1)
		doc = frappe.get_doc(doc.doctype, doc.name)
		for i, row in enumerate(doc.label_details):  # seals done, container check not done
			row.seal_no, row.seal_condition = str(500 + i), "Ok"
		doc.save()
		doc = _step(doc, 1)  # Sealed
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

	def test_rows_made_for_stock_entry_style_lrf(self):
		# what the Manufacture Stock Entry hook does: item, batch and reference only
		doc = frappe.get_doc(dict(doctype="Label Requisition Form", production_item=ITEM, production_b_no=BATCH,
			grade="", inspected_by="Administrator"))
		doc.flags.ignore_mandatory = doc.flags.ignore_permissions = True
		doc.insert()
		self.assertEqual(int(doc.no_of_packages), 2)  # from the batch
		self.assertEqual([r.drum_no for r in doc.label_details], ["01/02", "02/02"])
		self.assertEqual([r.no_of_labels_required for r in doc.label_details], [2, 2])

	def test_incomplete_form_cannot_go_to_qa(self):
		self.assertRaises(frappe.ValidationError, _step, _lrf(released=5), 1)  # qty does not match drums
		self.assertRaises(frappe.ValidationError, _step, _lrf(drums=((25, 3.5),)), 1)  # 1 drum, batch says 2
		self.assertRaises(frappe.ValidationError, _step, _lrf(drums=((25, 3.5), (0, 1.0))), 1)  # empty drum

	def test_grade_and_label_batch_can_wait_until_final_approval(self):
		# live site practice: release batch no. and grade are added while the form is with QA
		doc = _lrf()
		doc.released_b_no, doc.grade = "", ""
		doc.save()
		doc = _step(doc, 1)  # allowed to go to QA
		doc = _step(_qa(doc), 1)  # Sealed
		self.assertRaises(frappe.ValidationError, _step, doc, 1)  # but not final approval
		doc = frappe.get_doc(doc.doctype, doc.name)
		doc.released_b_no, doc.grade = "php006426", "ip"
		doc.save()
		doc = _step(doc, 1)
		self.assertEqual((doc.docstatus, doc.released_b_no, doc.grade), (1, "PHP006426", "IP"))

	def test_require_master_label_setting(self):
		frappe.db.delete("Master Label", {"item": ITEM})
		frappe.db.set_single_value("Labelling Settings", "require_master_label", 1)
		self.assertRaises(frappe.ValidationError, _approve, _lrf())
		_master("L981-00", "Product")
		doc = _approve(_lrf())
		self.assertEqual(doc.product_label_no, "L981-00")
