"""Drum labels, phase 2: LRF rules moved from a site Client Script into the app.

* Disables (does not delete) the site Client Script "LRF Entry Automation" on
  Label Requisition Form: its drum-row / weight / total logic now lives in the
  app's label_requisition_form.js and is repeated on the server. Re-enabling it
  is the undo.
* Sites that re-ordered LRF fields with a field_order Property Setter get the new
  "Drum Labels" fields placed after Stock Entry Reference instead of at the end.
Idempotent.
"""
import json

import frappe

NEW_FIELDS = [
	"labels_section",
	"label_variant",
	"customer",
	"column_break_labels",
	"product_label_no",
	"identification_label_no",
]


def execute():
	for name in frappe.get_all(
		"Client Script",
		filters={"dt": "Label Requisition Form", "name": "LRF Entry Automation", "enabled": 1},
		pluck="name",
	):
		frappe.db.set_value("Client Script", name, "enabled", 0)

	ps = frappe.db.get_value(
		"Property Setter",
		{"doc_type": "Label Requisition Form", "property": "field_order", "doctype_or_field": "DocType"},
		["name", "value"],
		as_dict=True,
	)
	if ps and ps.value:
		order = json.loads(ps.value)
		missing = [f for f in NEW_FIELDS if f not in order]
		if missing:
			anchor = "stock_entry_reference_name"
			pos = order.index(anchor) + 1 if anchor in order else len(order)
			order[pos:pos] = missing
			frappe.db.set_value("Property Setter", ps.name, "value", json.dumps(order))
	frappe.clear_cache(doctype="Label Requisition Form")
