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
from frappe.utils import cint, flt, getdate

from valence.valence.labelling.normalize import upper_text

DOCTYPE = "Intermediate Label"
MAX_DRUMS = 500
# A4 in mm; the template sets the same page margin for the PDF maker and the browser.
A4_SHORT_MM, A4_LONG_MM, PAGE_MARGIN_MM = 210, 297, 8
LOGO_SHARE = 0.17  # part of the tag height for the logo row; the 11 text rows share the rest
SCREEN_SHEET_MM = 168  # width Frappe's on-screen print preview gives the content

DEFAULTS = {
	"intermediate_item_group": "Intermediates",
	"default_drum_size_kg": 25,
	"default_tare_kg": 0,
	"merge_last_drum_below_kg": 0,
	# QA's Word tag sheet: tags of about 127 x 70 mm, 4 per A4 (2 across, 2 down on a
	# landscape page) with room between them to cut each tag out.
	"intermediate_tag_orientation": "Landscape",
	"intermediate_tag_width_mm": 127,
	"intermediate_tag_height_mm": 70,
	"intermediate_tag_gap_mm": 10,
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
	# A setting never saved reads back as 0; only a gap someone set to 0 means no gap.
	if not flt(out["intermediate_tag_gap_mm"]) and not frappe.db.exists(
		"Singles", {"doctype": "Labelling Settings", "field": "intermediate_tag_gap_mm"}
	):
		out["intermediate_tag_gap_mm"] = DEFAULTS["intermediate_tag_gap_mm"]
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


def gets_tag(item_code):
	"""An intermediate that is not the final stage (the final stage gets an LRF instead)."""
	return is_intermediate(item_code) and not cint(frappe.db.get_value("Item", item_code, "custom_is_final_stage"))


def _finished_rows(se):
	return [r for r in se.get("items") or [] if cint(r.get("is_finished_item")) and gets_tag(r.get("item_code"))]


@frappe.whitelist()
def tag_buttons(stock_entry):
	"""For the Stock Entry form: the entry's tags, and whether it made an intermediate that gets one."""
	se = frappe.get_doc("Stock Entry", stock_entry)
	se.check_permission("read")
	tags = frappe.get_all(DOCTYPE, filters={"stock_entry": se.name}, pluck="name")
	return {"tags": tags, "can_make": bool(_finished_rows(se)) if se.purpose == "Manufacture" else False}


@frappe.whitelist()
def item_query_filters():
	"""Item picker on the tag form: the Labelling Settings item group, final stage left out."""
	return {"item_group": tag_settings()["intermediate_item_group"], "custom_is_final_stage": 0}


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
	for row in _finished_rows(doc):  # final-stage items get a Label Requisition Form instead
		if frappe.db.exists(DOCTYPE, {"stock_entry": doc.name, "item_code": row.item_code, "batch_no": row.batch_no}):
			continue
		savepoint = "intermediate_tag"
		frappe.db.savepoint(savepoint)
		try:
			old = _tag_of_amended(doc, row)
			tag = update_tag(old, doc, row) if old else make_tag(doc, row)
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


def _tag_of_amended(se, row):
	"""The tag of the cancelled entry this one corrects (MAT-STE-1 -> MAT-STE-1-1), if any."""
	if not se.get("amended_from"):
		return None
	return frappe.db.get_value(DOCTYPE, {"stock_entry": se.amended_from, "item_code": row.item_code})


def update_tag(name, se, row):
	"""Corrected entry: the same tag moves to it, with the new qty split into drums again.
	Plant, Wet/Dry and Checked By that production already picked are kept."""
	tag = frappe.get_doc(DOCTYPE, name)
	values = _values_from_row(se, row)
	tag.update({k: values[k] for k in ("stock_entry", "batch_no", "total_qty")})
	tag.update({k: values[k] for k in ("mfg_date", "retest_date") if values.get(k)})
	tag.set("containers", [])  # re-split from the corrected total
	tag.add_comment("Comment", _("Moved from cancelled {0} to {1}; drums split again.").format(se.amended_from, se.name))
	tag.flags.ignore_mandatory = True
	tag.flags.ignore_permissions = True
	tag.save()
	return tag


def on_stock_entry_cancel(doc, method=None):
	"""A cancelled Manufacture entry: its tags are marked so nobody prints them by mistake.
	They are not deleted; submitting the corrected (amended) entry moves them to it."""
	if doc.get("purpose") != "Manufacture" or not frappe.db.exists("DocType", DOCTYPE):
		return
	try:
		_mark_cancelled(doc)
	except Exception:
		frappe.log_error(title=f"Intermediate tag not marked for cancelled {doc.name}")  # never stop the cancel


def _mark_cancelled(doc):
	for name in frappe.get_all(DOCTYPE, filters={"stock_entry": doc.name}, pluck="name"):
		frappe.get_doc(DOCTYPE, name).add_comment(
			"Comment", _("Stock Entry {0} was cancelled. Do not print this tag until the entry is corrected.").format(doc.name)
		)
		frappe.msgprint(
			_("Intermediate Tag {0} belongs to this cancelled entry. Do not print it until the entry is corrected.").format(
				frappe.utils.get_link_to_form(DOCTYPE, name)
			),
			indicator="orange",
			alert=True,
		)


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


def _landscape(s):
	return (s.get("intermediate_tag_orientation") or "Landscape") == "Landscape"


def _gap(s):
	return max(0, flt(s.get("intermediate_tag_gap_mm")))


def _grid(s):
	"""Tags across and down an A4 page at their real size, with the cutting gap between."""
	width, height, gap = flt(s["intermediate_tag_width_mm"]), flt(s["intermediate_tag_height_mm"]), _gap(s)
	page_w, page_h = (A4_LONG_MM, A4_SHORT_MM) if _landscape(s) else (A4_SHORT_MM, A4_LONG_MM)
	usable_w, usable_h = page_w - 2 * PAGE_MARGIN_MM, page_h - 2 * PAGE_MARGIN_MM
	# 1 mm spare per row so rounding in the PDF maker never pushes a row onto the next page
	cols = max(1, int((usable_w + gap) // (width + gap)))
	rows = max(1, int((usable_h - 1 + gap) // (height + gap)))
	return cols, rows


def _sizes(height):
	"""Row height and font size that fill a tag of this height (logo row + 11 ruled rows)."""
	logo = round(height * LOGO_SHARE, 2)
	row = round((height - logo - 1.5) / 11, 2)
	return {
		"logo_mm": logo,
		"logo_img_mm": round(logo - 1.2, 2),
		"row_mm": row,
		"font_pt": round(min(12, row * 1.6), 1),
		"title_pt": round(min(14, row * 1.85), 1),
	}


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
	from valence.valence.labelling.drum_labels import logo_url

	sheet_width = cols * flt(s["intermediate_tag_width_mm"]) + (cols - 1) * _gap(s)

	return {
		"pages": [[page[i : i + cols] for i in range(0, len(page), cols)] for page in pages],
		"cols": cols,
		"width_mm": flt(s["intermediate_tag_width_mm"]),
		"height_mm": flt(s["intermediate_tag_height_mm"]),
		"gap_mm": _gap(s),
		"sheet_width_mm": sheet_width,
		"screen_zoom": round(min(1, SCREEN_SHEET_MM / sheet_width), 3),
		"margin_mm": PAGE_MARGIN_MM,
		"orientation": "Landscape" if _landscape(s) else "Portrait",
		**_sizes(flt(s["intermediate_tag_height_mm"])),
		"sop_no": s["intermediate_tag_sop_no"],
		"company": upper_text(s["manufacturer_name"]),
		"logo_url": logo_url(s["logo"]),
	}
