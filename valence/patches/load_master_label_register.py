"""Load QA's Master Label register (shipped with the app) into Master Label.

Source: valence/valence/labelling/data/master_label_register.json, frozen from QA's
"Master Label Record.xlsx" and their Word master labels. Clean rows load as Approved
so label numbers fill in on LRFs straight away; rows QA must still settle load as Draft
with review notes. Masters already Approved or Retired on the site are never touched.
Re-running is safe.
"""
import frappe

from valence.valence.labelling.master_label_import import load_register


def execute():
	frappe.reload_doc("valence", "doctype", "master_label")
	print("Master Label register:", load_register())
