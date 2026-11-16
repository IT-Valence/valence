"""Data for the drum stickers printed from a Label Requisition Form.

The print formats only lay things out; every value printed on a sticker comes
from here, already cleaned (CAPITALS, fixed date and weight formats). Works for
both print routes:
  * Print > Labels button  -> doc is loaded from the database
  * printer icon preview  -> doc comes from the browser as JSON (dates are strings)
"""
import frappe
from frappe.utils import cint, flt, get_url, getdate

from valence.valence.doctype.master_label.master_label import find_master_label
from valence.valence.labelling.normalize import normalize_grade, upper_text

DEFAULTS = {
	"product_labels_per_drum": 1,
	"identification_labels_per_drum": 1,
	"product_label_width_mm": 190,
	"product_label_height_mm": 100,
	"identification_label_width_mm": 170,
	"identification_label_height_mm": 45,
	"page_layout": "A4 Sheet",
	"manufacturer_name": "Valence Labs Pvt Ltd",
	"manufacturer_address": "Vill. Jansui, Tehsil Rajpura,\nDistt. Patiala. (Punjab).",
	"logo": "/files/VLPL Logo.jpg",
}


def label_settings():
	"""Labelling Settings with safe defaults for anything left blank."""
	out = dict(DEFAULTS)
	if frappe.db.exists("DocType", "Labelling Settings"):
		s = frappe.get_cached_doc("Labelling Settings")
		for key in DEFAULTS:
			value = s.get(key)
			if value not in (None, "", 0):
				out[key] = value
	return out


def _date(value):
	return getdate(value).strftime("%b-%Y").upper() if value else ""


def _kg(value):
	return f"{flt(value, 3):.3f}" if value not in (None, "") else ""


def _qr_data_uri(text):
	import pyqrcode

	return "data:image/png;base64," + pyqrcode.create(text, error="M", encoding="utf-8").png_as_base64_str(
		scale=4, quiet_zone=2
	)


def _master(doc, label_type, field):
	"""Approved Master Label for this LRF: the one saved on the LRF, else a live lookup."""
	name = doc.get(field)
	if name and frappe.db.get_value("Master Label", name, "status") == "Approved":
		return frappe.get_cached_doc("Master Label", name)
	return find_master_label(
		doc.get("production_item"), doc.get("grade"), label_type, doc.get("label_variant") or "Standard", doc.get("customer")
	)


def get_drum_labels(doc, which="all"):
	"""Everything the sticker template needs.

	which: "all" (Identification + Product per drum), "product" or "identification".
	"""
	if isinstance(doc, str):
		doc = frappe.get_doc(frappe.parse_json(doc))
	settings = label_settings()
	grade = normalize_grade(doc.get("grade")) if doc.get("grade") else ""
	item_name = frappe.db.get_value("Item", doc.get("production_item"), "item_name") if doc.get("production_item") else ""
	product_ml = _master(doc, "Product", "product_label_no")
	ident_ml = _master(doc, "Identification", "identification_label_no")
	rows = sorted(doc.get("label_details") or [], key=lambda r: cint(r.get("idx")))
	total = len(rows)

	def common(ml):
		title = (ml.printed_title if ml else None) or upper_text(item_name) or ""
		return {
			"title": f"{title} ({grade})" if grade and grade != "NA" else title,
			"label_no": ml.label_no if ml else "",
			"show_manufacturer": cint(ml.show_manufacturer) if ml else 1,
			"neutral_label_no": (ml.neutral_label_no if ml else "") or "",
			"licence_no": (ml.licence_no if ml else "") or "2014-OSP",
			"storage": (ml.storage_condition if ml else "") or "",
			"hazard_lines": [l.strip() for l in ((ml.hazard_text if ml else "") or "").splitlines() if l.strip()],
		}

	product_base, ident_base = common(product_ml), common(ident_ml)
	batch_no = upper_text(doc.get("released_b_no")) or ""
	mfg, retest = _date(doc.get("manufacturing_date")), _date(doc.get("retestexpiry_date"))

	labels = []
	for pos, row in enumerate(rows, start=1):
		drum = f"{pos} of {total}"
		weights = {
			"gross": _kg(row.get("gross_weight_kg") or flt(row.get("net_weight_kg")) + flt(row.get("tare_weight_kg"))),
			"tare": _kg(row.get("tare_weight_kg")),
			"net": _kg(row.get("net_weight_kg")),
		}
		if which in ("all", "identification"):
			for _ in range(max(1, cint(settings["identification_labels_per_drum"]))):
				labels.append({"kind": "identification", **ident_base, "batch_no": batch_no, "drum": drum})
		if which in ("all", "product"):
			qr_text = "\n".join(
				[
					f"Product: {product_base['title']}",
					f"Batch No: {batch_no}",
					f"Mfg Date: {mfg}",
					f"Retest/Expiry: {retest}",
					f"Gross Wt: {weights['gross']} KG",
					f"Tare Wt: {weights['tare']} KG",
					f"Net Wt: {weights['net']} KG",
					f"Drum: {drum}",
					f"Label No: {product_base['label_no'] or '-'}",
					f"LRF: {doc.get('name') or '-'}",
				]
			)
			qr = _qr_data_uri(qr_text)
			for _ in range(max(1, cint(settings["product_labels_per_drum"]))):
				labels.append(
					{"kind": "product", **product_base, "batch_no": batch_no, "mfg": mfg, "retest": retest,
					 "drum": drum, "qr": qr, **weights}
				)

	warnings = []
	if cint(doc.get("docstatus")) != 1:
		warnings.append("NOT APPROVED - DO NOT USE")
	if which in ("all", "product") and not product_ml:
		warnings.append("NO APPROVED MASTER LABEL")
	if which in ("all", "identification") and not ident_ml:
		warnings.append("NO APPROVED MASTER LABEL")

	logo = settings["logo"]
	return {
		"labels": labels,
		"watermark": " / ".join(dict.fromkeys(warnings)),
		"settings": settings,
		"logo_url": get_url(logo) if logo and logo.startswith("/") else logo,
		"manufacturer_name": settings["manufacturer_name"],
		"manufacturer_lines": [l for l in (settings["manufacturer_address"] or "").splitlines() if l.strip()],
	}
