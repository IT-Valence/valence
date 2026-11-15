"""Custom fields and defaults for leave planning (run after migrate, idempotent)."""
import frappe
from frappe.utils import getdate

FIELDS = {
	"Leave Application": [
		dict(fieldname="custom_applied_on", label="Applied On", fieldtype="Date", read_only=1, insert_after="custom_working_leave_days"),
		dict(fieldname="custom_leave_timing", label="Applied", fieldtype="Select", read_only=1, in_list_view=1, in_standard_filter=1,
			options="\nIn advance\nAfter the fact (on time)\nLate", insert_after="custom_applied_on",
			description="In advance, after the fact within the allowed days, or late (only HR can approve)."),
		dict(fieldname="custom_hod_deadline", label="HOD Decision By", fieldtype="Date", read_only=1, insert_after="custom_leave_timing",
			description="After this date only HR can approve or reject. Super HOD gets the same number of days more."),
	],
	"Leave Type": [
		dict(fieldname="custom_leave_notice_section", label="Advance notice", fieldtype="Section Break", insert_after="custom_not_for_esi_covered"),
		dict(fieldname="custom_notice_days", label="Apply at least (days before)", fieldtype="Int", insert_after="custom_leave_notice_section",
			description="Applications with less notice are Late and need HR. 0 = on or before the first day."),
		dict(fieldname="custom_allow_apply_after", label="Can be applied after the leave", fieldtype="Check", default="1", insert_after="custom_notice_days"),
		dict(fieldname="custom_apply_after_grace_days", label="Days after the leave to apply", fieldtype="Int", default="2",
			insert_after="custom_allow_apply_after", depends_on="custom_allow_apply_after"),
	],
}

LEAVE_TYPE_DEFAULTS = {
	"Earned Leave": dict(custom_notice_days=7, custom_allow_apply_after=0, custom_apply_after_grace_days=0),
	"Casual Leave": dict(custom_notice_days=1, custom_allow_apply_after=1, custom_apply_after_grace_days=2),
	"Sick Leave": dict(custom_notice_days=0, custom_allow_apply_after=1, custom_apply_after_grace_days=2),
}


def after_migrate():
	created = False
	for dt, fields in FIELDS.items():
		for f in fields:
			if frappe.db.exists("Custom Field", {"dt": dt, "fieldname": f["fieldname"]}):
				continue
			if dt == "Leave Type" and f["fieldname"] == "custom_leave_notice_section" and not frappe.get_meta("Leave Type").has_field("custom_not_for_esi_covered"):
				f = dict(f, insert_after="custom_working_days_per_leave")
			frappe.get_doc(dict(doctype="Custom Field", dt=dt, module="Valence", **f)).insert(ignore_permissions=True)
			created = created or dt == "Leave Type"
	frappe.clear_cache(doctype="Leave Application")
	frappe.clear_cache(doctype="Leave Type")
	if created:
		other = dict(custom_notice_days=1, custom_allow_apply_after=1, custom_apply_after_grace_days=2)
		for lt in frappe.get_all("Leave Type", pluck="name"):
			frappe.db.set_value("Leave Type", lt, LEAVE_TYPE_DEFAULTS.get(lt, other), update_modified=False)
	backfill()
	frappe.db.commit()


def backfill(since="2026-04-01"):
	"""Tag existing applications once, using their creation date as the application date."""
	from valence.valence.leave_planning.application import compute

	for name in frappe.get_all(
		"Leave Application",
		filters={"from_date": [">=", since], "custom_leave_timing": ["is", "not set"]},
		pluck="name",
	):
		doc = frappe.get_doc("Leave Application", name)
		applied_on, timing, deadline, _ = compute(doc, applied_on=getdate(doc.creation))
		frappe.db.set_value(
			"Leave Application", name,
			dict(custom_applied_on=applied_on, custom_leave_timing=timing, custom_hod_deadline=deadline),
			update_modified=False,
		)
