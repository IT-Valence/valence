# Copyright (c) 2026, finbyz tech and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import nowdate

from valence.valence.labelling.normalize import (
	clean_text,
	normalize_grade,
	normalize_label_no,
	upper_text,
)


class MasterLabel(Document):
	def autoname(self):
		self.label_no = self._checked_label_no()
		self.name = self.label_no

	def validate(self):
		self.label_no = self._checked_label_no()
		self.revision = self.label_no.split("-")[1]
		grade = normalize_grade(self.grade)
		if not grade:
			frappe.throw(_("Grade {0} is not one of IP, BP, USP, EP, IH, NA.").format(self.grade))
		self.grade = grade
		self.printed_title = upper_text(self.printed_title) or self._default_title()
		self.storage_condition = clean_text(self.storage_condition)
		self.licence_no = upper_text(self.licence_no)
		self.neutral_label_no = upper_text(self.neutral_label_no)
		if self.variant == "Neutral" and self.is_new() and self.show_manufacturer:
			# neutral labels never name Valence
			self.show_manufacturer = 0
		if self.status == "Approved":
			self._validate_ready_to_approve()

	# ------------------------------------------------------------------ helpers
	def _checked_label_no(self):
		label_no = normalize_label_no(self.label_no)
		if not label_no:
			frappe.throw(_("Label No. {0} must look like L002-00.").format(frappe.bold(self.label_no)))
		return label_no

	def _default_title(self):
		name = None
		if self.item:
			name = frappe.db.get_value("Item", self.item, "item_name")
		return upper_text(name or self.product_name)

	def _validate_ready_to_approve(self):
		missing = []
		if not self.item:
			missing.append(_("Item"))
		if not self.printed_title:
			missing.append(_("Printed Title"))
		if self.label_type == "Product" and not self.storage_condition:
			missing.append(_("Storage Condition"))
		if self.variant == "Neutral" and not self.neutral_label_no:
			missing.append(_("Neutral Label No."))
		if missing:
			frappe.throw(_("Fill these before approving: {0}").format(", ".join(missing)))
		if not self.effective_date:
			self.effective_date = nowdate()
		if not self.approved_by:
			self.approved_by = frappe.session.user

		clash = frappe.db.get_value(
			"Master Label",
			{
				"name": ("!=", self.name),
				"status": "Approved",
				"item": self.item,
				"grade": self.grade,
				"label_type": self.label_type,
				"variant": self.variant,
				"customer": self.customer or ("is", "not set"),
			},
		)
		if clash:
			frappe.throw(
				_("{0} is already the approved {1} label for this item and grade. Retire it first.").format(
					frappe.bold(clash), self.label_type
				)
			)


def find_master_label(item, grade, label_type, variant="Standard", customer=None):
	"""Approved master label for one drum label, or None.

	A customer-specific label wins over the general one for the same item/grade.
	"""
	grade = normalize_grade(grade)
	if not (item and grade and label_type):
		return None
	base = {
		"status": "Approved",
		"item": item,
		"grade": grade,
		"label_type": label_type,
		"variant": variant or "Standard",
	}
	if customer:
		name = frappe.db.get_value("Master Label", {**base, "customer": customer})
		if name:
			return frappe.get_cached_doc("Master Label", name)
	name = frappe.db.get_value("Master Label", {**base, "customer": ("is", "not set")})
	return frappe.get_cached_doc("Master Label", name) if name else None


@frappe.whitelist()
def get_master_labels(item, grade, variant="Standard", customer=None):
	"""Label numbers for the Product and Identification labels of one item/grade.

	Used by the Label Requisition Form to show which master labels will print.
	"""
	frappe.has_permission("Master Label", "read", throw=True)
	out = {}
	for label_type in ("Product", "Identification"):
		doc = find_master_label(item, grade, label_type, variant, customer)
		out[label_type.lower()] = doc.label_no if doc else None
	return out
