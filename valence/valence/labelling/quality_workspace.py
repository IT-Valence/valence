"""Labelling buttons on the Quality page (Quality workspace shortcuts).

The Quality page belongs to ERPNext and each site keeps its own edited copy in the
database (on Valence's sites it already has an "LRF-Label Requisition Form" button next
to TRF and FPTN). After every migrate this makes sure the page has three labelling
buttons: the LRF one (kept under the site's own name), Master Label and Customize
Labelling. It only adds what is missing and never removes anything. Rows are written
directly so that developer mode does not export ERPNext's workspace into its app files.
"""
import json

import frappe

BUTTONS = (
	{"label": "LRF-Label Requisition Form", "link_to": "Label Requisition Form", "doc_view": "List", "stats_filter": "[]"},
	{
		"label": "Master Label",
		"link_to": "Master Label",
		"doc_view": "List",
		"stats_filter": json.dumps([["Master Label", "status", "=", "Approved", False]]),
		"format": "{} Approved",
	},
	{"label": "Customize Labelling", "link_to": "Labelling Settings", "doc_view": ""},
)


def ensure_quality_buttons():
	if not frappe.db.exists("Workspace", "Quality"):
		return
	_remove_old_labelling_section()
	ws = frappe.get_doc("Workspace", "Quality")
	try:
		content = json.loads(ws.content or "[]")
	except ValueError:
		return
	shortcut_blocks = [i for i, b in enumerate(content) if b.get("type") == "shortcut"]
	col = content[shortcut_blocks[-1]]["data"].get("col", 4) if shortcut_blocks else 4
	insert_at = (shortcut_blocks[-1] + 1) if shortcut_blocks else len(content)

	by_target = {s.link_to: s for s in ws.shortcuts}
	next_idx = max([s.idx for s in ws.shortcuts] or [0]) + 1
	changed = False
	for button in BUTTONS:
		row = by_target.get(button["link_to"])
		if not row:
			row = frappe.get_doc(
				{
					"doctype": "Workspace Shortcut",
					"parent": ws.name,
					"parenttype": "Workspace",
					"parentfield": "shortcuts",
					"idx": next_idx,
					"type": "DocType",
					"color": "Grey",
					**button,
				}
			)
			row.db_insert()
			next_idx += 1
			changed = True
		if not any(b.get("type") == "shortcut" and b["data"].get("shortcut_name") == row.label for b in content):
			content.insert(
				insert_at,
				{"id": frappe.generate_hash(length=10), "type": "shortcut", "data": {"shortcut_name": row.label, "col": col}},
			)
			insert_at += 1
			changed = True
	if changed:
		frappe.db.set_value("Workspace", ws.name, "content", json.dumps(content))  # also bumps modified
		frappe.clear_cache()


def _remove_old_labelling_section():
	"""An earlier build had a separate 'Labelling' sub-page under Quality; buttons replace it."""
	if frappe.db.get_value("Workspace", "Labelling", "module") == "Valence":
		frappe.delete_doc("Workspace", "Labelling", ignore_permissions=True, force=True)
