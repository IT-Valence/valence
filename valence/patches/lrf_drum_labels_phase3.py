"""Drum labels, phase 3: the app's "Drum Labels" print formats replace the site-only sticker.

* Disables (does not delete) the site Client Script "Label Sticker" (old Print > Labels
  button) and the site Print Format "Label Sticker" (external QR service, no Label No.,
  breaks in the printer-icon preview). Re-enabling both is the undo.
* Makes "Drum Labels" the default print format for Label Requisition Form, unless the
  site already chose another default.
Idempotent.
"""
import frappe
from frappe.custom.doctype.property_setter.property_setter import make_property_setter

DT = "Label Requisition Form"


def execute():
	if frappe.db.exists("Client Script", {"name": "Label Sticker", "dt": DT, "enabled": 1}):
		frappe.db.set_value("Client Script", "Label Sticker", "enabled", 0)

	if frappe.db.exists("Print Format", {"name": "Label Sticker", "doc_type": DT, "standard": "No", "disabled": 0}):
		frappe.db.set_value("Print Format", "Label Sticker", "disabled", 1)

	current = frappe.db.get_value(
		"Property Setter", {"doc_type": DT, "property": "default_print_format", "doctype_or_field": "DocType"}, "value"
	)
	if (not current or current == "Label Sticker") and frappe.db.exists("Print Format", "Drum Labels"):
		make_property_setter(DT, None, "default_print_format", "Drum Labels", "Data", for_doctype=True)
	frappe.clear_cache(doctype=DT)
