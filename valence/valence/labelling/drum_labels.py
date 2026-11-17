"""Data for the drum stickers printed from a Label Requisition Form.

The print formats only lay things out; every value printed on a sticker comes
from here, already cleaned (CAPITALS, fixed date and weight formats). Works for
both print routes:
  * Print > Labels button  -> doc is loaded from the database
  * printer icon preview  -> doc comes from the browser as JSON (dates are strings)
"""
import frappe
from frappe.utils import cint, flt, formatdate, get_url, getdate

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
	"product_labels_per_page": 2,
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


def _qr_text(text):
	"""Plain ASCII for the QR: a QR does not say which character set it uses, and many
	scanners guess wrong for symbols like the degree sign ("25 ˚C" read back as "25 ﾋ咾").
	The printed sticker keeps the real symbols; only the QR content is spelled out."""
	import unicodedata

	for sign in ("°", "˚", "º"):
		text = text.replace(sign, " deg ")
	text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
	return "\n".join(" ".join(line.split()) for line in text.splitlines())


def _qr_data_uri(text, error="M"):
	"""QR as a PNG data URI. quiet_zone=4 is the white border the QR standard asks for
	(phones need it to find the code next to text). error: "M" for drums (survives scuffs),
	"L" for paper documents, which keeps a long QR less dense."""
	import pyqrcode

	return "data:image/png;base64," + pyqrcode.create(_qr_text(text), error=error).png_as_base64_str(
		scale=8, quiet_zone=4
	)


def _master(doc, label_type, field):
	"""Master Label for this LRF: an Approved one (saved on the LRF, else a live lookup),
	else the register's Draft one, so the label number still prints (watermarked)."""
	name = doc.get(field)
	if name and frappe.db.get_value("Master Label", name, "status") == "Approved":
		return frappe.get_cached_doc("Master Label", name)
	args = (doc.get("production_item"), doc.get("grade"), label_type, doc.get("label_variant") or "Standard", doc.get("customer"))
	found = find_master_label(*args)
	if found:
		return found
	if name and frappe.db.get_value("Master Label", name, "status") in ("Draft", "Pending"):
		return frappe.get_cached_doc("Master Label", name)
	return find_master_label(*args, include_drafts=True)


def _master_fields(ml, item_name="", grade=""):
	"""Sticker text that comes from a Master Label (title, label no., storage, ...)."""
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


def _manufacturer(settings):
	logo = settings["logo"]
	return {
		"logo_url": get_url(logo) if logo and logo.startswith("/") else logo,
		"manufacturer_name": settings["manufacturer_name"],
		"manufacturer_lines": [l for l in (settings["manufacturer_address"] or "").splitlines() if l.strip()],
	}


def get_master_label_print(doc):
	"""Everything the Master Label print format (SOP S-QA-006, Annexure III) needs.

	The sticker is the specimen: label text from the master, batch/date/weight fields
	left blank, Drum No. "1 of 1". Watermark DRAFT / SPECIMEN / RETIRED by status.
	"""
	if isinstance(doc, str):
		doc = frappe.get_doc(frappe.parse_json(doc))
	settings = label_settings()
	grade = normalize_grade(doc.get("grade")) if doc.get("grade") else ""
	item_name = frappe.db.get_value("Item", doc.get("item"), "item_name") if doc.get("item") else ""
	ml = frappe._dict(doc.as_dict() if hasattr(doc, "as_dict") else doc)
	ml.label_no = ml.label_no or ml.name
	fields = _master_fields(ml, item_name or ml.product_name, grade)
	kind = "product" if ml.label_type == "Product" else "identification"
	# Same as the drum sticker, but a master shows no weight fields at all.
	sticker = {"kind": kind, **fields, "batch_no": "", "mfg": "", "retest": "", "no_weights": 1, "drum": "1 of 1"}
	if kind == "product":
		# The master's own QR holds only what the master slip shows: no batch, dates or weights.
		maker = _manufacturer(settings)
		lines = [f"Product: {fields['title']}"]
		if fields["show_manufacturer"]:
			lines += [f"Manufacturing Licence No: {fields['licence_no']}", "(For Manufacturing Use Only)."]
		if fields["storage"]:
			lines.append(f"Storage: {fields['storage']}")
		lines += fields["hazard_lines"]
		if fields["show_manufacturer"]:
			lines += ["Manufactured in India by:", maker["manufacturer_name"], *maker["manufacturer_lines"]]
		elif fields["neutral_label_no"]:
			lines.append(f"Neutral Label No: {fields['neutral_label_no']}")
		lines += ["Drum No: 1 of 1", f"Label No: {fields['label_no'] or '-'}"]
		sticker["qr"] = _qr_data_uri("\n".join(lines), error="L")  # paper document: less dense
	status = ml.status or "Draft"
	return {
		"sticker": sticker,
		"sticker_data": {"watermark": "", **_manufacturer(settings)},
		"settings": settings,
		"watermark": {"Approved": "SPECIMEN", "Retired": "RETIRED"}.get(status, "DRAFT"),
		"status": status,
		"name_of_label": fields["title"],
		"label_no": fields["label_no"],
		"effective_date": formatdate(ml.effective_date) if ml.effective_date else "",
		**_manufacturer(settings),
	}


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

	product_base = _master_fields(product_ml, item_name, grade)
	ident_base = _master_fields(ident_ml, item_name, grade)
	batch_no = upper_text(doc.get("released_b_no")) or ""
	mfg, retest = _date(doc.get("manufacturing_date")), _date(doc.get("retestexpiry_date"))

	ident_labels, product_labels = [], []
	for pos, row in enumerate(rows, start=1):
		drum = f"{pos} of {total}"
		gross = flt(row.get("gross_weight_kg")) or flt(row.get("net_weight_kg")) + flt(row.get("tare_weight_kg"))
		weights = {
			# Gross Wt. is what QA read on the scale (Observed Wt.; it starts equal to net + tare).
			"gross": _kg(flt(row.get("observed_weight_kg")) or gross),
			"tare": _kg(row.get("tare_weight_kg")),
			"net": _kg(row.get("net_weight_kg")),
		}
		if which in ("all", "identification"):
			for _ in range(max(1, cint(settings["identification_labels_per_drum"]))):
				ident_labels.append({"kind": "identification", **ident_base, "batch_no": batch_no, "drum": drum})
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
					f"Seal No: {upper_text(row.get('seal_no')) or '-'}",
					f"Label No: {product_base['label_no'] or '-'}",
					f"LRF: {doc.get('name') or '-'}",
				]
			)
			qr = _qr_data_uri(qr_text)
			for _ in range(max(1, cint(settings["product_labels_per_drum"]))):
				product_labels.append(
					{"kind": "product", **product_base, "batch_no": batch_no, "mfg": mfg, "retest": retest,
					 "drum": drum, "qr": qr, **weights}
				)

	# Printed as two groups: every Identification sticker first (as many per A4 page as
	# fit), then the Product stickers, a fixed number per A4 page.
	per_page = max(1, cint(settings["product_labels_per_page"]))
	product_pages = [product_labels[i : i + per_page] for i in range(0, len(product_labels), per_page)]

	warnings = []
	if cint(doc.get("docstatus")) != 1:
		warnings.append("NOT APPROVED - DO NOT USE")
	for kinds, ml in ((("all", "product"), product_ml), (("all", "identification"), ident_ml)):
		if which not in kinds:
			continue
		if not ml:
			warnings.append("NO MASTER LABEL")
		elif ml.status != "Approved":
			warnings.append("MASTER LABEL NOT APPROVED")

	return {
		"labels": ident_labels + product_labels,
		"identification_labels": ident_labels,
		"product_labels": product_labels,
		"product_pages": product_pages,
		"watermark": " / ".join(dict.fromkeys(warnings)),
		"settings": settings,
		**_manufacturer(settings),
	}
