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
	product_key,
	product_similarity,
	upper_text,
)

SAME_PRODUCT = 0.86

# Same approval chain as the LRF on Valence's sites, but master labels are QA documents:
# QA (Stock Manager, who gives final LRF approval) creates, sends for approval and approves.
APPROVER_ROLES = ("Stock Manager", "System Manager")

# (from, to) -> who may make the change; status is only changed with the form's buttons
TRANSITIONS = {
	("Draft", "Pending"): "writer",  # Send for Approval
	("Pending", "Approved"): "approver",  # Approve
	("Pending", "Draft"): "approver",  # Send Back
	("Approved", "Retired"): "approver",  # Retire
	("Draft", "Approved"): "system",  # register import (System Manager only)
	("Pending", "Pending"): "writer",
}


def is_approver(user=None):
	return bool(set(frappe.get_roles(user)) & set(APPROVER_ROLES))


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
		self._check_status_change()
		if self.status == "Approved":
			self._validate_ready_to_approve()

	def _check_status_change(self):
		before = self.get_doc_before_save()
		old = before.status if before else None
		new = self.status or "Draft"
		system = "System Manager" in frappe.get_roles()
		if old in ("Approved", "Retired") and not is_approver() and self._changed_since(before):
			frappe.throw(_("Only QA (Stock Manager) can change an approved or retired master label."))
		if old == new or (old is None and new in ("Draft", "Pending")):
			return
		who = TRANSITIONS.get((old or "Draft", new))
		allowed = (
			who == "writer"
			or (who == "approver" and is_approver())
			or system  # System Manager may set any status (register import, corrections)
		)
		if not allowed:
			frappe.throw(
				_("{0} → {1} is not allowed for you. Production sends a master for approval; QA (Stock Manager) approves, sends back or retires it.").format(
					old or "New", new
				),
				title=_("Not allowed"),
			)

	def _changed_since(self, before):
		if not before:
			return True
		skip = {"modified", "modified_by", "_comments", "_liked_by", "_assign", "_user_tags"}
		return any(self.get(f) != before.get(f) for f in self.meta.get_valid_columns() if f not in skip)

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
		if not self.item and not self.product_name:
			missing.append(_("Item or Product (Register)"))
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

		# one approved label per product / grade / type / variant / customer
		mine = _names_of(self)
		clash = next(
			(
				r.name
				for r in _candidates(self.grade, self.label_type, self.variant, self.customer, ("Approved",))
				if r.name != self.name and _similarity(mine, _names_of(r)) >= SAME_PRODUCT
			),
			None,
		)
		if clash:
			frappe.throw(
				_("{0} is already the approved {1} label for this item and grade. Retire it first.").format(
					frappe.bold(clash), self.label_type
				)
			)


def _candidates(grade, label_type, variant, customer, statuses):
	return frappe.get_all(
		"Master Label",
		filters={
			"status": ("in", list(statuses)),
			"grade": grade,
			"label_type": label_type,
			"variant": variant or "Standard",
			"customer": customer if customer else ("is", "not set"),
		},
		fields=["name", "status", "item", "printed_title", "product_name"],
		order_by="name asc",
	)


def _names_of(row):
	"""Product names a master label answers to: its title, the register name, its item's name."""
	names = [row.get("printed_title"), row.get("product_name")]
	if row.get("item"):
		names.append(frappe.db.get_value("Item", row.get("item"), "item_name"))
	return [product_key(n) for n in names if n]


def _similarity(keys_a, keys_b):
	return max((product_similarity(a, b) for a in keys_a for b in keys_b), default=0)


def find_master_label(item, grade, label_type, variant="Standard", customer=None, include_drafts=False):
	"""Master label for one drum label, or None.

	Looks for, in order: an Approved label linked to this exact item, then an Approved
	label for the same product by name (one product has several ERP items: a general
	one and one per grade), then - with include_drafts - the same among Draft labels,
	so the label number from QA's register still fills in while QA reviews the master.
	A customer-specific label wins over the general one.
	"""
	grade = normalize_grade(grade)
	if not (item and grade and label_type):
		return None
	item_keys = _names_of({"item": item}) or [product_key(item)]
	passes = [("Approved",)] + ([("Pending", "Draft")] if include_drafts else [])
	for statuses in passes:
		for cust in ([customer, None] if customer else [None]):
			rows = _candidates(grade, label_type, variant, cust, statuses)
			exact = [r for r in rows if r.item == item]
			if exact:
				return frappe.get_cached_doc("Master Label", exact[0].name)
			scored = sorted(((_similarity(item_keys, _names_of(r)), r.name) for r in rows), reverse=True)
			if scored and scored[0][0] >= SAME_PRODUCT:
				return frappe.get_cached_doc("Master Label", scored[0][1])
	return None


@frappe.whitelist()
def change_status(name, action, reason=None):
	"""The Master Label buttons: send_for_approval, approve, send_back, retire."""
	targets = {"send_for_approval": "Pending", "approve": "Approved", "send_back": "Draft", "retire": "Retired"}
	if action not in targets:
		frappe.throw(_("Unknown action {0}").format(action))
	doc = frappe.get_doc("Master Label", name)
	doc.check_permission("write")
	if action in ("send_back", "retire") and not (reason or "").strip():
		frappe.throw(_("Give a reason."))
	doc.status = targets[action]
	doc.save()
	labels = {"send_for_approval": "Sent for approval", "approve": "Approved", "send_back": "Sent back", "retire": "Retired"}
	doc.add_comment("Comment", labels[action] + (f". Reason: {reason.strip()}" if reason and reason.strip() else "."))
	return doc.status


@frappe.whitelist()
def get_master_labels(item, grade, variant="Standard", customer=None):
	"""Label numbers for the Product and Identification labels of one item/grade.

	Used by the Label Requisition Form to show which master labels will print.
	"""
	frappe.has_permission("Master Label", "read", throw=True)
	out = {}
	for label_type in ("Product", "Identification"):
		doc = find_master_label(item, grade, label_type, variant, customer, include_drafts=True)
		out[label_type.lower()] = doc.label_no if doc else None
		out[label_type.lower() + "_status"] = doc.status if doc else None
	return out
