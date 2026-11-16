# Copyright (c) 2026, finbyz tech and contributors
# For license information, please see license.txt

import os
import tempfile
import zipfile

import frappe
from frappe.tests.utils import FrappeTestCase

from valence.valence.doctype.master_label.master_label import find_master_label, get_master_labels
from valence.valence.labelling import master_label_import as imp
from valence.valence.labelling.normalize import normalize_grade, normalize_label_no, upper_text

ITEM = "_TEST-ML-ITEM"


def _item():
	if not frappe.db.exists("Item", ITEM):
		hsn = frappe.db.get_value("GST HSN Code", {}, "name")
		frappe.get_doc(
			doctype="Item", item_code=ITEM, item_name="Test Phenylephrine Hydrochloride",
			item_group="All Item Groups", stock_uom="Nos", is_stock_item=0,
			custom_item_stage=1, gst_hsn_code=hsn,
		).insert(ignore_permissions=True)
	return ITEM


def _label(label_no, **kw):
	frappe.db.delete("Master Label", {"name": normalize_label_no(label_no)})
	doc = frappe.get_doc(
		{
			"doctype": "Master Label",
			"label_no": label_no,
			"label_type": "Product",
			"variant": "Standard",
			"grade": "IP",
			"item": _item(),
			"storage_condition": "Store below 25 C",
			**kw,
		}
	)
	return doc.insert(ignore_permissions=True)


class TestNormalize(FrappeTestCase):
	def test_label_no(self):
		self.assertEqual(normalize_label_no("L0041-00"), "L041-00")
		self.assertEqual(normalize_label_no(" l2-0 "), "L002-00")
		self.assertEqual(normalize_label_no("L002-00"), "L002-00")
		self.assertIsNone(normalize_label_no("LC0024-00"))
		self.assertIsNone(normalize_label_no("PB/DRUGS/2014-OSP"))

	def test_grade(self):
		self.assertEqual(normalize_grade("bp"), "BP")
		self.assertEqual(normalize_grade(" ip "), "IP")
		self.assertEqual(normalize_grade("In-house (With Neutral code)"), "IH")
		self.assertEqual(normalize_grade("USP(With Neutral code)"), "USP")
		self.assertEqual(normalize_grade("Others/IH"), "IH")
		self.assertEqual(normalize_grade(""), "NA")
		self.assertIsNone(normalize_grade("XYZ"))

	def test_upper(self):
		self.assertEqual(upper_text("  php005726 "), "PHP005726")
		self.assertEqual(upper_text("Phenylephrine   hydrochloride"), "PHENYLEPHRINE HYDROCHLORIDE")
		self.assertIsNone(upper_text(None))


class TestMasterLabel(FrappeTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_name_and_cleanup(self):
		doc = _label("l0991-01", grade="bp", printed_title="  test product ")
		self.assertEqual(doc.name, "L991-01")
		self.assertEqual(doc.revision, "01")
		self.assertEqual(doc.grade, "BP")
		self.assertEqual(doc.printed_title, "TEST PRODUCT")

	def test_title_defaults_to_item_name(self):
		doc = _label("L992-00")
		self.assertEqual(doc.printed_title, "TEST PHENYLEPHRINE HYDROCHLORIDE")

	def test_bad_label_no(self):
		self.assertRaises(frappe.ValidationError, _label, "LABEL-ONE")

	def test_neutral_hides_manufacturer(self):
		doc = _label("L993-00", variant="Neutral")
		self.assertEqual(doc.show_manufacturer, 0)

	def test_approve_needs_storage_for_product_label(self):
		doc = _label("L994-00", storage_condition=None)
		doc.status = "Approved"
		self.assertRaises(frappe.ValidationError, doc.save)

	def test_only_one_approved_per_item_grade_type(self):
		_label("L995-00", status="Approved")
		second = _label("L996-00")
		second.status = "Approved"
		self.assertRaises(frappe.ValidationError, second.save)

	def test_lookup(self):
		_label("L997-00", status="Approved")
		_label("L998-00", status="Approved", label_type="Identification", storage_condition=None)
		_label("L999-00")  # draft, must be ignored
		self.assertEqual(find_master_label(ITEM, "ip", "Product").name, "L997-00")
		self.assertEqual(get_master_labels(ITEM, "IP"), {"product": "L997-00", "identification": "L998-00"})
		self.assertIsNone(find_master_label(ITEM, "BP", "Product"))


def _docx(path, tables):
	"""Minimal .docx with one Word table per label; each table is a list of cells (list of paragraphs)."""
	body = ""
	for cells in tables:
		row = "".join(
			"<w:tc>" + "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in cell) + "</w:tc>" for cell in cells
		)
		body += f"<w:tbl><w:tr>{row}</w:tr></w:tbl>"
	xml = (
		'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/'
		f'wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>'
	)
	with zipfile.ZipFile(path, "w") as z:
		z.writestr("word/document.xml", xml)


class TestRegisterImport(FrappeTestCase):
	def setUp(self):
		from openpyxl import Workbook

		self.dir = tempfile.mkdtemp()
		wb = Workbook()
		ws = wb.active
		ws.append(["Sr. No.", "Product ", "Label Type", "Label No.", "Pharmacopoeial Reference", "Remarks"])
		ws.append([1, "Test Phenylephrine Hydrochloride", "Identification ", "L0901-00", "IP", None])
		ws.append([None, None, "Product Label", "L902-00", "ip", None])
		ws.append([2, "Fexofenadine hydrochloride", "Product Label", "L903-00", "USP", "DRAFT"])
		ws.append([3, "Some Base Neutral", "Product Label", "L904-00", "In-house (With Neutral code)", None])
		self.register = os.path.join(self.dir, "register.xlsx")
		wb.save(self.register)

		os.makedirs(os.path.join(self.dir, "PHP"))
		_docx(
			os.path.join(self.dir, "PHP", "php.docx"),
			[
				[["TEST PHENYLEPHRINE HYDROCHLORIDE (IP)"], ["Batch No:", "Label No:", "L901-00"]],
				[
					["TEST PHENYLEPHRINE HYDROCHLORIDE (IP)"],
					["Batch No.: Gross Wt.: Net Wt.:", "Storage: Keep cool and dry. | x"],
					["Label No.: L902-00"],
				],
			],
		)
		# a copy-paste mistake: another product using L903-00
		_docx(os.path.join(self.dir, "wrong.docx"), [[["URSODEOXYCHOLIC ACID (BP)"], ["Gross Wt.", "Label No.: L903-00"]]])
		_item()

	def test_read_register_carries_merged_cells(self):
		rows = imp.read_register(self.register)
		self.assertEqual([r["label_no"] for r in rows], ["L901-00", "L902-00", "L903-00", "L904-00"])
		self.assertEqual(rows[1]["product"], "Test Phenylephrine Hydrochloride")
		self.assertEqual(rows[1]["sr"], 1)

	def test_plan(self):
		plan = {p["label_no"]: p for p in imp.build_plan(self.register, self.dir)}
		ident, prod, fexo, neutral = plan["L901-00"], plan["L902-00"], plan["L903-00"], plan["L904-00"]

		self.assertEqual((ident["label_type"], ident["grade"], ident["item"]), ("Identification", "IP", ITEM))
		self.assertEqual(prod["label_type"], "Product")
		self.assertEqual(prod["storage_condition"], "Keep cool and dry.")
		self.assertEqual(prod["printed_title"], "TEST PHENYLEPHRINE HYDROCHLORIDE")
		self.assertIsNone(prod["import_notes"])

		self.assertIn("uses this label number for 'URSODEOXYCHOLIC ACID (BP)'", fexo["import_notes"])
		self.assertIn("Register remark: DRAFT", fexo["import_notes"])

		self.assertEqual((neutral["variant"], neutral["grade"], neutral["show_manufacturer"]), ("Neutral", "IH", 0))

	def test_run_creates_drafts_and_never_touches_approved(self):
		for no in ("L901-00", "L902-00", "L903-00", "L904-00"):
			frappe.db.delete("Master Label", {"name": no})
		imp.run(self.register, self.dir)
		self.assertEqual(frappe.db.get_value("Master Label", "L902-00", "status"), "Draft")

		frappe.db.set_value("Master Label", "L902-00", {"status": "Approved", "storage_condition": "QA text"})
		summary = imp.run(self.register, self.dir)
		self.assertIn("L902-00", summary["skipped_not_draft"])
		self.assertEqual(frappe.db.get_value("Master Label", "L902-00", "storage_condition"), "QA text")
		for no in ("L901-00", "L902-00", "L903-00", "L904-00"):
			frappe.db.delete("Master Label", {"name": no})
		frappe.db.delete("Item", {"name": ITEM})
		frappe.db.commit()
