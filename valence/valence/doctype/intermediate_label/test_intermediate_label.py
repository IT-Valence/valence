# Copyright (c) 2026, finbyz tech and contributors
# For license information, please see license.txt

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now

from valence.valence.labelling.intermediate_tags import (
	get_intermediate_tags,
	on_stock_entry_submit,
	split_into_drums,
	values_from_stock_entry,
)

ITEM = "_TEST-INT-PHP01"
BATCH = "_TEST-INT-B001"
SE = "_TEST-INT-SE-001"


def _nets(total, size, merge=0):
	return [r["net_weight"] for r in split_into_drums(total, size, 0, merge)]


def _setup():
	if not frappe.db.exists("Item Group", "Intermediates"):
		frappe.get_doc(doctype="Item Group", item_group_name="Intermediates", parent_item_group="All Item Groups").insert()
	if not frappe.db.exists("Item", ITEM):
		frappe.get_doc(
			doctype="Item", item_code=ITEM, item_name="Test PHP01", item_group="Intermediates", stock_uom="Kg",
			is_stock_item=0, has_batch_no=1, custom_item_stage=1,
			gst_hsn_code=frappe.db.get_value("GST HSN Code", {}, "name"),
		).insert(ignore_permissions=True)
	if not frappe.db.exists("Batch", BATCH):
		b = frappe.get_doc(doctype="Batch", batch_id=BATCH, item=ITEM, manufacturing_date="2026-11-13", expiry_date="2027-10-31")
		b.flags.ignore_validate = True
		b.insert(ignore_permissions=True)
	if not frappe.db.exists("Stock Entry", SE):
		# A submitted Manufacture entry, written directly: submitting a real one needs a BOM and stock.
		company = frappe.db.get_value("Company", {}, "name")
		frappe.db.sql(
			"insert into `tabStock Entry` (name, creation, modified, owner, modified_by, docstatus, company, purpose,"
			" stock_entry_type, posting_date) values (%s, %s, %s, 'Administrator', 'Administrator', 1, %s, 'Manufacture', 'Manufacture', '2026-11-13')",
			(SE, now(), now(), company),
		)
		frappe.get_doc(
			doctype="Stock Entry Detail", parent=SE, parenttype="Stock Entry", parentfield="items", idx=1,
			item_code=ITEM, qty=412, transfer_qty=412, conversion_factor=1, uom="Kg", stock_uom="Kg",
			batch_no=BATCH, is_finished_item=1, docstatus=1,
		).db_insert()


class TestIntermediateLabel(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_setup()

	def setUp(self):
		frappe.db.delete("Intermediate Label", {"batch_no": BATCH})

	# ---- the drum math (examples from the user)
	def test_whole_drums(self):
		self.assertEqual(_nets(400, 20), [20] * 20)
		self.assertEqual(_nets(400, 40), [40] * 10)
		self.assertEqual(_nets(400, 25), [25] * 16)

	def test_last_drum_takes_the_rest(self):
		self.assertEqual(_nets(412, 25), [25] * 16 + [12])
		self.assertEqual(_nets(10, 25), [10])

	def test_any_drum_size(self):
		self.assertEqual(_nets(100, 22.5), [22.5] * 4 + [10])
		self.assertEqual(_nets(453.05, 21), [21] * 21 + [12.05])

	def test_small_last_drum_merged_when_set(self):
		self.assertEqual(_nets(401, 25, merge=5), [25] * 15 + [26])
		self.assertEqual(_nets(412, 25, merge=5), [25] * 16 + [12])  # 12 kg is not small

	def test_tare_and_gross(self):
		rows = split_into_drums(50, 25, 3.5, 0)
		self.assertEqual([(r["container_no"], r["tare_weight"], r["gross_weight"]) for r in rows], [(1, 3.5, 28.5), (2, 3.5, 28.5)])

	def test_nothing_to_split(self):
		self.assertEqual(split_into_drums(0, 25, 0, 0), [])
		self.assertEqual(split_into_drums(100, 0, 0, 0), [])

	def test_too_many_drums_refused(self):
		self.assertRaises(frappe.ValidationError, split_into_drums, 100000, 1, 0, 0)

	# ---- the record
	def _tag(self, **kw):
		data = {"doctype": "Intermediate Label", "batch_no": BATCH, "plant": "PR 1", "material_status": "WET",
			"total_qty": 412, "drum_size": 25, "tare_per_drum": 2}
		data.update(kw)
		return frappe.get_doc(data).insert(ignore_permissions=True)

	def test_save_splits_and_fills_from_batch(self):
		tag = self._tag()
		self.assertEqual((tag.item_code, tag.product_name, tag.stage), (ITEM, "Test PHP01", "1"))
		self.assertEqual(str(tag.mfg_date), "2026-11-13")
		self.assertEqual(tag.no_of_containers, 17)
		self.assertEqual(tag.net_total, 412)
		self.assertEqual((tag.containers[-1].net_weight, tag.containers[-1].gross_weight), (12, 14))

	def test_hand_edits_are_kept(self):
		tag = self._tag()
		tag.containers[0].net_weight = 24.85
		tag.containers[0].tare_weight = 2.1
		tag.product_name = "PHP01 Wet Cake"
		tag.save()
		tag.reload()
		self.assertEqual((tag.containers[0].net_weight, tag.containers[0].gross_weight), (24.85, 26.95))
		self.assertEqual(tag.product_name, "PHP01 Wet Cake")
		self.assertEqual(tag.net_total, 411.85)  # warned on screen, still saved
		self.assertEqual(tag.no_of_containers, 17)

	def test_empty_drum_refused(self):
		tag = self._tag()
		tag.containers[3].net_weight = 0
		self.assertRaises(frappe.ValidationError, tag.save)

	# ---- made from the Manufacture Stock Entry
	def test_values_from_stock_entry(self):
		v = values_from_stock_entry(SE)
		self.assertEqual((v["item_code"], v["batch_no"], v["total_qty"], v["stage"]), (ITEM, BATCH, 412, "1"))

	def test_made_on_manufacture_submit_once(self):
		se = frappe.get_doc("Stock Entry", SE)
		on_stock_entry_submit(se)
		on_stock_entry_submit(se)  # submitting again must not make a second tag
		names = frappe.get_all("Intermediate Label", filters={"stock_entry": SE}, pluck="name")
		self.assertEqual(len(names), 1)
		tag = frappe.get_doc("Intermediate Label", names[0])
		self.assertEqual((tag.total_qty, tag.drum_size, tag.no_of_containers), (412, 25, 17))
		self.assertFalse(tag.plant)  # production picks it

	# ---- printing
	def test_print_pages(self):
		tag = self._tag()
		tag.material_status = ""  # unsaved form in the print preview: printed for ticking by hand
		data = get_intermediate_tags(tag)
		tags = [t for page in data["pages"] for row in page for t in row]
		self.assertEqual(len(tags), 17)
		self.assertEqual(data["cols"], 2)
		self.assertEqual(data["orientation"], "Landscape")
		self.assertEqual([len(p) for p in data["pages"]], [2, 2, 2, 2, 1])  # 4 per A4 page, 2 x 2
		self.assertEqual(tags[-1]["container"], "17 of 17")
		self.assertEqual((tags[-1]["net"], tags[-1]["gross"]), ("12.000 Kg", "14.000 Kg"))
		self.assertEqual(tags[0]["material_status"], "WET/DRY")
		self.assertEqual(tags[0]["mfg"], "13-11-2026")

	def test_print_preview_json(self):
		tag = self._tag()
		data = get_intermediate_tags(frappe.as_json(tag.as_dict()))
		self.assertEqual(sum(len(r) for p in data["pages"] for r in p), 17)
