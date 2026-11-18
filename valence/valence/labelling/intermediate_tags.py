"""Intermediate Status Tags (Ref. SOP S-SG-046): one tag per drum of an intermediate batch.

Production weighs the batch as a whole; the ERP only knows the batch total from the
Manufacture Stock Entry. So here the drums are worked out instead of typed:
total qty + drum size -> full drums, and the last drum takes what is left
(412 kg in 25 kg drums = 16 x 25 kg + 1 x 12 kg). Every value stays editable on the form.

  split_into_drums      - the drum math
  values_from_stock_entry / values_from_batch - what the form fetches
  on_stock_entry_submit - makes a tag record when an intermediate is manufactured
  get_intermediate_tags - everything the print format needs
"""
import math

import frappe
from frappe import _
from frappe.utils import cint, flt, get_url, getdate

from valence.valence.labelling.normalize import upper_text

DOCTYPE = "Intermediate Label"
MAX_DRUMS = 500
# The PDF maker leaves about 15 mm at each page edge (measured on the printed PDF).
A4_WIDTH_MM, A4_HEIGHT_MM, PAGE_MARGIN_MM, GAP_MM = 210, 297, 15, 3

DEFAULTS = {
	"intermediate_item_group": "Intermediates",
	"default_drum_size_kg": 25,
	"default_tare_kg": 0,
	"merge_last_drum_below_kg": 0,
	"intermediate_tag_width_mm": 88,
	"intermediate_tag_height_mm": 62,
	"intermediate_tag_sop_no": "S-SG-046",
	"manufacturer_name": "Valence Labs Pvt Ltd",
	"logo": "/files/VLPL Logo.jpg",
}


def tag_settings():
	"""Labelling Settings for intermediate tags, with defaults for anything left blank."""
	out = dict(DEFAULTS)
	if frappe.db.exists("DocType", "Labelling Settings"):
		s = frappe.get_cached_doc("Labelling Settings")
		for key in DEFAULTS:
			value = s.get(key)
			if value not in (None, ""):
				out[key] = value
	for key in ("default_drum_size_kg", "intermediate_tag_width_mm", "intermediate_tag_height_mm"):
		if not flt(out[key]):
			out[key] = DEFAULTS[key]
	return out


# ---------------------------------------------------------------- drum math


@frappe.whitelist()
def split_into_drums(total_qty, drum_size, tare=0, merge_last_below=None):
	"""Net weight per drum: full drums of drum_size, the last one holds the rest.

	merge_last_below: a last drum lighter than this (kg) is added to the drum before it
	instead (401 kg / 25 kg with 5 -> 15 x 25 + 1 x 26). None = use Labelling Settings.
	Returns rows [{container_no, net_weight, tare_weight, gross_weight}].
	"""
	total, size, tare = flt(total_qty, 3), flt(drum_size, 3), flt(tare, 3)
	if total <= 0 or size <= 0:
		return []
	if merge_last_below is None:
		merge_last_below = tag_settings()["merge_last_drum_below_kg"]
	merge_last_below = flt(merge_last_below, 3)

	count = math.ceil(round(total / size, 6))  # round first: 400/25 must be 16, not 16.000000001
	if count > MAX_DRUMS:
		frappe.throw(
			_("{0} kg in {1} kg drums would make {2} drums. Check the Total Qty and Drum Size.").format(
				total, size, count
			)
		)
	nets = [size] * (count - 1) + [flt(total - size * (count - 1), 3)]
	if len(nets) > 1 and merge_last_below > 0 and nets[-1] < merge_last_below:
		last = nets.pop()
		nets[-1] = flt(nets[-1] + last, 3)
	return [
		{"container_no": i, "net_weight": net, "tare_weight": tare, "gross_weight": flt(net + tare, 3)}
		for i, net in enumerate(nets, start=1)
	]


# ---------------------------------------------------------------- what the form fetches


def is_intermediate(item_code):
	group = tag_settings()["intermediate_item_group"]
	return bool(item_code) and frappe.db.get_value("Item", item_code, "item_group") == group


def _stage(item_code):
	stage = cint(frappe.db.get_value("Item", item_code, "custom_item_stage")) if item_code else 0
	return str(stage) if stage > 0 else ""


def _batch_values(batch_no):
	if not batch_no or not frappe.db.exists("Batch", batch_no):
		return {}
	meta = frappe.get_meta("Batch")
	fields = ["item", "manufacturing_date", "expiry_date"] + (["retest_date"] if meta.has_field("retest_date") else [])
	b = frappe.db.get_value("Batch", batch_no, fields, as_dict=True)
	return {
		"item_code": b.item,
		"mfg_date": b.manufacturing_date,
		# Valence keeps the retest date in a custom field; fall back to the expiry date.
		"retest_date": b.get("retest_date") or b.expiry_date,
	}


def _item_values(item_code):
	if not item_code:
		return {}
	return {
		"item_code": item_code,
		"product_name": frappe.db.get_value("Item", item_code, "item_name") or item_code,
		"stage": _stage(item_code),
	}


@frappe.whitelist()
def values_from_batch(batch_no):
	frappe.has_permission("Batch", "read", batch_no, throw=True)
	values = _batch_values(batch_no)
	values.update(_item_values(values.get("item_code")))
	return values


def _finished_rows(se):
	rows = [r for r in se.get("items") or [] if cint(r.get("is_finished_item")) and is_intermediate(r.get("item_code"))]
	return rows


@frappe.whitelist()
def values_from_stock_entry(stock_entry, item_code=None):
	"""Item, batch, total qty and dates of the intermediate made by a Manufacture Stock Entry."""
	se = frappe.get_doc("Stock Entry", stock_entry)
	se.check_permission("read")
	rows = [r for r in _finished_rows(se) if not item_code or r.item_code == item_code]
	if not rows:
		frappe.throw(_("Stock Entry {0} did not manufacture an intermediate item.").format(stock_entry))
	return _values_from_row(se, rows[0])


def _values_from_row(se, row):
	values = {"stock_entry": se.name, "batch_no": row.get("batch_no"), "total_qty": flt(row.get("transfer_qty") or row.get("qty"), 3)}
	values.update(_batch_values(row.get("batch_no")))
	values.update(_item_values(row.get("item_code")))
	stage = se.get("stage")
	if not values.get("stage") and stage:
		values["stage"] = stage
	return values


# ---------------------------------------------------------------- made on Manufacture submit


def on_stock_entry_submit(doc, method=None):
	"""Manufacture of an intermediate -> a tag record, drums already split by the default size.

	Production then picks the plant and wet/dry, checks the drums and prints. This must
	never stop the Stock Entry, so any problem is logged and the entry goes through.
	"""
	if doc.get("purpose") != "Manufacture" or not frappe.db.exists("DocType", DOCTYPE):
		return
	for row in _finished_rows(doc):
		if cint(frappe.db.get_value("Item", row.item_code, "custom_is_final_stage")):
			continue  # final-stage items get a Label Requisition Form instead
		if frappe.db.exists(DOCTYPE, {"stock_entry": doc.name, "item_code": row.item_code, "batch_no": row.batch_no}):
			continue
		savepoint = "intermediate_tag"
		frappe.db.savepoint(savepoint)
		try:
			tag = make_tag(doc, row)
			frappe.msgprint(
				_("Intermediate Status Tag {0} made for {1}: pick the Plant and Wet/Dry, then print.").format(
					frappe.utils.get_link_to_form(DOCTYPE, tag.name), row.item_code
				),
				title=_("Intermediate Tag"),
				indicator="green",
			)
		except Exception:
			frappe.db.rollback(save_point=savepoint)
			frappe.log_error(title=f"Intermediate tag not made for {doc.name}")


def make_tag(se, row):
	s = tag_settings()
	tag = frappe.new_doc(DOCTYPE)
	tag.update(_values_from_row(se, row))
	tag.drum_size = flt(s["default_drum_size_kg"])
	tag.tare_per_drum = flt(s["default_tare_kg"])
	tag.done_by = se.get("owner") or frappe.session.user
	tag.flags.ignore_mandatory = True  # plant and wet/dry are picked by production afterwards
	tag.flags.ignore_permissions = True
	tag.insert()
	return tag


# ---------------------------------------------------------------- printing


def _full_name(user):
	if not user:
		return ""
	return frappe.db.get_value("User", user, "full_name") or user


def _kg(value):
	return f"{flt(value, 3):.3f} Kg" if value not in (None, "") else ""


def _date(value):
	return getdate(value).strftime("%d-%m-%Y") if value else ""


def _grid(s):
	width, height = flt(s["intermediate_tag_width_mm"]), flt(s["intermediate_tag_height_mm"])
	usable_w, usable_h = A4_WIDTH_MM - 2 * PAGE_MARGIN_MM, A4_HEIGHT_MM - 2 * PAGE_MARGIN_MM
	cols = max(1, int((usable_w + GAP_MM) // (width + GAP_MM)))
	rows = max(1, int((usable_h + GAP_MM) // (height + GAP_MM)))
	return cols, rows


def get_intermediate_tags(doc):
	"""Tags for the print format, grouped into A4 pages and rows (as many as fit)."""
	if isinstance(doc, str):
		doc = frappe.get_doc(frappe.parse_json(doc))
	s = tag_settings()
	rows = sorted(doc.get("containers") or [], key=lambda r: cint(r.get("idx")))
	total = len(rows)
	common = {
		"product_name": upper_text(doc.get("product_name")) or upper_text(doc.get("item_code")) or "",
		"stage": doc.get("stage") or "",
		"plant": doc.get("plant") or "",
		"batch_no": upper_text(doc.get("batch_no")) or "",
		"mfg": _date(doc.get("mfg_date")),
		"retest": _date(doc.get("retest_date")),
		# Left blank on the record -> printed as on the paper tag, for ticking by hand.
		"material_status": doc.get("material_status") or "WET/DRY",
		"total_qty": _kg(doc.get("total_qty")),
		"containers": total,
		"done_by": _full_name(doc.get("done_by")),
		"checked_by": _full_name(doc.get("checked_by")),
	}
	tags = []
	for pos, row in enumerate(rows, start=1):
		net, tare = flt(row.get("net_weight"), 3), flt(row.get("tare_weight"), 3)
		gross = flt(row.get("gross_weight"), 3) or flt(net + tare, 3)
		tags.append({**common, "container": f"{pos} of {total}", "net": _kg(net), "tare": _kg(tare), "gross": _kg(gross)})

	cols, per_col = _grid(s)
	per_page = cols * per_col
	pages = [tags[i : i + per_page] for i in range(0, len(tags), per_page)]
	logo = s["logo"]
	if logo and logo.startswith("/files/") and not frappe.db.exists("File", {"file_url": logo}):
		logo = None  # no logo uploaded on this site: the tag prints "VALENCE LABS" as text
	return {
		"pages": [[page[i : i + cols] for i in range(0, len(page), cols)] for page in pages],
		"cols": cols,
		"width_mm": flt(s["intermediate_tag_width_mm"]),
		"height_mm": flt(s["intermediate_tag_height_mm"]),
		"gap_mm": GAP_MM,
		"sop_no": s["intermediate_tag_sop_no"],
		"company": upper_text(s["manufacturer_name"]),
		"logo_url": get_url(logo) if logo and logo.startswith("/") else logo,
	}
