"""One-time import of QA's Master Label Record register into Master Label.

Reads:
  * the register (Master Label Record.xlsx): Label No., product, label type,
    pharmacopoeia -> the authoritative list of label numbers;
  * optionally the folder of Word master labels, to pick up the printed title
    and storage text for each label number.

Everything is created as Draft. Anything that looks wrong (Word file using a
label number for another product, two files disagreeing, product not found in
Item) is written to the record's Review Notes for QA to settle before approval.

Run on a site:
  bench --site <site> execute valence.valence.labelling.master_label_import.run \
    --kwargs "{'register': '/path/Master Label Record.xlsx', 'masters_folder': '/path/Master label'}"
"""
import difflib
import os
import re
import zipfile
from html import unescape

import frappe

from valence.valence.labelling.normalize import (
	clean_text,
	letters_only,
	normalize_grade,
	normalize_label_no,
	upper_text,
)

LABEL_NO_IN_TEXT = re.compile(r"\bL\s*0*\d{1,3}\s*-\s*\d{2}\b", re.I)


# ------------------------------------------------------------------ register
def read_register(path):
	"""Rows of the register as dicts. Sr./Product cells are merged in Excel, so
	blank ones carry down from the row above."""
	from openpyxl import load_workbook

	ws = load_workbook(path, read_only=True, data_only=True).worksheets[0]
	rows = ws.iter_rows(values_only=True)
	header = [upper_text(h) or "" for h in next(rows)]

	def col(*names):
		for n in names:
			for i, h in enumerate(header):
				if h.startswith(n):
					return i
		raise ValueError(f"Register has no column {names[0]!r}; columns are {header}")

	c_sr, c_product, c_type, c_no, c_ref = (
		col("SR"),
		col("PRODUCT"),
		col("LABEL TYPE"),
		col("LABEL NO"),
		col("PHARMACOPOEIAL", "PHARMACOPOEIA"),
	)
	c_remarks = next((i for i, h in enumerate(header) if h.startswith("REMARK")), None)

	out, sr, product = [], None, None
	for r in rows:
		if not r or all(v in (None, "") for v in r):
			continue
		sr = r[c_sr] if r[c_sr] not in (None, "") else sr
		product = clean_text(r[c_product]) or product
		label_no = normalize_label_no(r[c_no])
		if not label_no:
			continue
		out.append(
			{
				"sr": int(sr) if isinstance(sr, (int, float)) else None,
				"product": product,
				"label_type_raw": clean_text(r[c_type]),
				"label_no": label_no,
				"reference_raw": clean_text(r[c_ref]),
				"remarks": clean_text(r[c_remarks]) if c_remarks is not None else None,
			}
		)
	return out


def classify(row):
	"""Label type, grade and variant from the register's free text."""
	ref = (row["reference_raw"] or "").upper()
	label_type = "Identification" if "IDENT" in (row["label_type_raw"] or "").upper() else "Product"
	neutral = "NEUTRAL" in ref or "NEUTRAL" in (row["product"] or "").upper()
	variant = "Neutral" if neutral else "Standard"
	grade = normalize_grade(ref) or "NA"
	return label_type, grade, variant


# -------------------------------------------------------------- word masters
def _docx_tables(path):
	"""Plain text of each table in a .docx (each printed label is one table)."""
	with zipfile.ZipFile(path) as z:
		xml = z.read("word/document.xml").decode("utf8", "ignore")
	tables = []
	for tbl in re.findall(r"<w:tbl>.*?</w:tbl>", xml, flags=re.S):
		cells = []
		for tc in re.findall(r"<w:tc>.*?</w:tc>", tbl, flags=re.S):
			paras = []
			for p in re.findall(r"<w:p[ >].*?</w:p>", tc, flags=re.S):
				t = "".join(re.findall(r"<w:t(?: [^>]*)?>(.*?)</w:t>", p, flags=re.S))
				t = clean_text(unescape(t))
				if t:
					paras.append(t)
			if paras:
				cells.append(paras)
		if cells:
			tables.append(cells)
	return tables


def scan_word_masters(folder):
	"""{label_no: [ {file, title, storage}, ... ]} from every .docx under folder."""
	found = {}
	for root, _dirs, files in os.walk(folder):
		for fn in sorted(files):
			if not fn.lower().endswith(".docx") or fn.startswith("~$"):
				continue
			path = os.path.join(root, fn)
			rel = os.path.relpath(path, folder)
			try:
				tables = _docx_tables(path)
			except Exception:
				continue
			for cells in tables:
				flat = [p for c in cells for p in c]
				text = " | ".join(flat)
				numbers = {normalize_label_no(m) for m in LABEL_NO_IN_TEXT.findall(text)} - {None}
				if not numbers:
					continue
				# the header cell can hold two lines (chemical name + common name)
				title = clean_text(" ".join(cells[0]))
				storage = None
				m = re.search(r"Storage\s*:\s*(.+?)(?:\s*\||$)", text, flags=re.I)
				if m:
					storage = clean_text(m.group(1))
				layout = "Product" if re.search(r"Gross\s*Wt", text, flags=re.I) else "Identification"
				for no in numbers:
					found.setdefault(no, []).append(
						{"file": rel, "title": title, "storage": storage, "layout": layout}
					)
	return found


def _title_without_grade(title):
	return clean_text(re.sub(r"\((IP|BP|USP|EP|IH|NA|IN-?HOUSE)\)\s*$", "", title or "", flags=re.I))


def _product_key(name):
	"""Letters of a product name without the word NEUTRAL (the register adds it to the name)."""
	return letters_only(re.sub(r"\bneutral\b", "", name or "", flags=re.I))


def _partial_ratio(short, long):
	"""Best similarity of `short` against any same-length slice of `long`."""
	if len(short) > len(long):
		short, long = long, short
	if not short:
		return 0
	best = 0
	for i in range(len(long) - len(short) + 1):
		best = max(best, difflib.SequenceMatcher(None, short, long[i : i + len(short)]).ratio())
		if best == 1:
			break
	return best


def _same_product(word_title, register_product):
	a, b = _product_key(word_title), _product_key(register_product)
	if not a or not b:
		return True
	return a in b or b in a or _partial_ratio(a, b) >= 0.8


# ------------------------------------------------------------------- items
def _item_index():
	return [(i.name, letters_only(i.item_name)) for i in frappe.get_all("Item", fields=["name", "item_name"])]


def match_item(product, index):
	"""Item code whose name matches the register product, if exactly one does."""
	key = _product_key(product)
	if not key:
		return None
	exact = [code for code, name in index if name == key]
	if len(exact) == 1:
		return exact[0]
	close = [code for code, name in index if name and difflib.SequenceMatcher(None, key, name).ratio() >= 0.9]
	return close[0] if len(close) == 1 else None


# ---------------------------------------------------------------------- run
def build_plan(register, masters_folder=None):
	"""What the import would create, with review notes. No database writes."""
	rows = read_register(register)
	words = scan_word_masters(masters_folder) if masters_folder else {}
	index = _item_index()
	seen = {}
	plan = []
	for row in rows:
		label_type, grade, variant = classify(row)
		notes = []
		if row["label_no"] in seen:
			notes.append(f"Label No. also listed at register Sr. {seen[row['label_no']]}.")
		seen[row["label_no"]] = row["sr"]
		if row["remarks"]:
			notes.append(f"Register remark: {row['remarks']}.")
		if not row["reference_raw"]:
			notes.append("Register has no pharmacopoeia for this label; set to NA.")

		title, storage, source = None, None, None
		matches = words.get(row["label_no"], [])
		good = [m for m in matches if _same_product(_title_without_grade(m["title"]), row["product"])]
		bad = [m for m in matches if m not in good]
		for m in bad:
			notes.append(f"Word file '{m['file']}' uses this label number for '{m['title']}'.")
		if good:
			title = _title_without_grade(good[0]["title"])
			source = good[0]["file"]
			if _product_key(title) != _product_key(row["product"]) and _product_key(row["product"]) not in _product_key(title):
				notes.append(
					f"Spelling differs: register '{row['product']}', Word master '{title}'. Confirm which is right."
				)
			layouts = {m["layout"] for m in good}
			if label_type not in layouts:
				notes.append(
					f"Register says {label_type} label, but the Word master is laid out as {'/'.join(sorted(layouts))}."
				)
			storages = sorted({m["storage"] for m in good if m["storage"]})
			if storages:
				storage = storages[0]
			if len(storages) > 1:
				notes.append("Word files disagree on storage text: " + " / ".join(storages))
		elif masters_folder:
			notes.append("No Word master found for this label number.")
		if label_type == "Product" and not storage:
			notes.append("Storage condition needed before approval.")

		item = match_item(row["product"], index)
		if not item:
			notes.append(f"Link the ERP Item for '{row['product']}'.")

		plan.append(
			{
				"label_no": row["label_no"],
				"register_sr_no": row["sr"],
				"product_name": row["product"],
				"label_type": label_type,
				"grade": grade,
				"variant": variant,
				"item": item,
				"printed_title": upper_text(title or row["product"]),
				"storage_condition": storage,
				"show_manufacturer": 0 if variant == "Neutral" else 1,
				"neutral_label_no": "PB/DRUGS/2014-OSP" if variant == "Neutral" else None,
				"source_file": source,
				"import_notes": "\n".join(notes) or None,
			}
		)

	# a product's Identification and Product labels should print the same title
	groups = {}
	for p in plan:
		groups.setdefault((_product_key(p["product_name"]), p["grade"], p["variant"]), []).append(p)
	for members in groups.values():
		titles = {p["printed_title"] for p in members}
		if len(titles) > 1:
			msg = "Titles differ between this product's labels: " + " / ".join(
				f"{p['label_no']} '{p['printed_title']}'" for p in members
			)
			for p in members:
				p["import_notes"] = "\n".join(filter(None, [p["import_notes"], msg]))
	return plan


def run(register, masters_folder=None, dry_run=0):
	"""Create (or refresh Draft) Master Labels from the register. Approved ones are never touched."""
	plan = build_plan(register, masters_folder)
	created, updated, skipped = [], [], []
	for p in plan:
		if frappe.db.exists("Master Label", p["label_no"]):
			doc = frappe.get_doc("Master Label", p["label_no"])
			if doc.status != "Draft":
				skipped.append(p["label_no"])
				continue
			if not int(dry_run):
				doc.update({k: v for k, v in p.items() if k != "label_no"})
				doc.save()
			updated.append(p["label_no"])
		else:
			if not int(dry_run):
				frappe.get_doc({"doctype": "Master Label", "status": "Draft", **p}).insert()
			created.append(p["label_no"])
	if not int(dry_run):
		frappe.db.commit()
	summary = {
		"created": len(created),
		"updated": len(updated),
		"skipped_not_draft": skipped,
		"needs_review": sum(1 for p in plan if p["import_notes"]),
		"dry_run": bool(int(dry_run)),
	}
	print(summary)
	return summary
