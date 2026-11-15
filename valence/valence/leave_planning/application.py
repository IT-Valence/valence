"""Leave Application: notice tag, approval deadlines and HR-only rules.

- Tags each application "In advance", "After the fact (on time)" or "Late"
  from the leave type's notice settings (see rules.py).
- Sets the HOD decision deadline.
- A late application, or one past its deadline, can only be approved or
  rejected by HR.
- Leave types that don't allow applying after the fact (Earned Leave) can only
  be entered after the leave started by HR.
"""
import frappe
from frappe import _
from frappe.utils import cint, getdate, today

from valence.valence.leave_planning import rules as R

HR_ROLES = {"HR Manager", "HR User", "System Manager", "Administrator"}
PENDING_HOD = "Pending HOD Approval"
PENDING_SUPER_HOD = "Pending Super HOD Approval"
DECISIONS = {"Approved", "Rejected", PENDING_SUPER_HOD}
DEFAULTS = dict(notice_days=1, allow_after=1, grace_days=2)


def _has_fields():
	return frappe.get_meta("Leave Application").has_field("custom_leave_timing")


def is_hr(user=None) -> bool:
	user = user or frappe.session.user
	return user == "Administrator" or bool(HR_ROLES & set(frappe.get_roles(user)))


def leave_type_rules(leave_type):
	meta = frappe.get_meta("Leave Type")
	if not meta.has_field("custom_notice_days"):
		return dict(DEFAULTS)
	v = frappe.db.get_value(
		"Leave Type", leave_type,
		["custom_notice_days", "custom_allow_apply_after", "custom_apply_after_grace_days"], as_dict=True,
	) or {}
	return dict(
		notice_days=cint(v.get("custom_notice_days")),
		allow_after=cint(v.get("custom_allow_apply_after")),
		grace_days=cint(v.get("custom_apply_after_grace_days")),
	)


def decision_days() -> int:
	return cint(frappe.db.get_single_value("Attendance Settings", "leave_hod_decision_days")) or 3


def compute(doc, applied_on=None):
	applied_on = getdate(applied_on or doc.get("custom_applied_on") or (doc.creation if doc.get("creation") else today()))
	r = leave_type_rules(doc.leave_type)
	timing = R.classify(applied_on, getdate(doc.from_date), getdate(doc.to_date), r["notice_days"], bool(r["allow_after"]), r["grace_days"])
	deadline = R.hod_deadline(applied_on, getdate(doc.from_date), decision_days())
	return applied_on, timing, deadline, r


def validate(doc, method=None):
	if not _has_fields() or not doc.leave_type or not doc.from_date or not doc.to_date:
		return
	applied_on, timing, deadline, r = compute(doc)
	doc.custom_applied_on = applied_on
	doc.custom_leave_timing = timing
	doc.custom_hod_deadline = deadline

	if doc.is_new() and not r["allow_after"] and R.is_after_the_fact(applied_on, getdate(doc.from_date)) and not is_hr():
		frappe.throw(
			_("{0} must be applied for before it starts. Please contact HR.").format(doc.leave_type),
			title=_("Apply in advance"),
		)

	enforce_deadline(doc, timing, applied_on)


def enforce_deadline(doc, timing, applied_on):
	before = doc.get_doc_before_save()
	if not before or is_hr():
		return
	old, new = before.get("workflow_state"), doc.get("workflow_state")
	if old not in (PENDING_HOD, PENDING_SUPER_HOD) or new == old or new not in DECISIONS:
		return
	if timing == R.LATE:
		frappe.throw(
			_("This leave was applied late, so only HR can approve or reject it."),
			title=_("HR decision needed"),
		)
	days = decision_days()
	deadline = (
		R.super_hod_deadline(applied_on, getdate(doc.from_date), days)
		if old == PENDING_SUPER_HOD
		else R.hod_deadline(applied_on, getdate(doc.from_date), days)
	)
	if getdate(today()) > deadline:
		frappe.throw(
			_("The decision deadline ({0}) has passed, so only HR can approve or reject this leave now.").format(
				frappe.format(deadline, "Date")
			),
			title=_("HR decision needed"),
		)
