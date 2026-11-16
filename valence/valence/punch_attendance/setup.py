"""Attendance field for the punch engine (run after migrate, idempotent)."""
import frappe

from valence.valence.punch_attendance.engine import AUTO_FIELD


def after_migrate():
	if frappe.db.exists("Custom Field", {"dt": "Attendance", "fieldname": AUTO_FIELD}):
		return
	frappe.get_doc(
		dict(
			doctype="Custom Field",
			dt="Attendance",
			module="Valence",
			fieldname=AUTO_FIELD,
			label="Marked from punches",
			fieldtype="Check",
			read_only=1,
			no_copy=1,
			in_standard_filter=1,
			insert_after="custom_final_status",
			description="ERP re-marks this day by itself when its punches change. Records HR amends are left alone.",
		)
	).insert(ignore_permissions=True)
	frappe.clear_cache(doctype="Attendance")
	start = frappe.db.get_single_value("Attendance Settings", "punch_attendance_from")
	if start:
		# records the engine made before this field existed
		frappe.db.sql(
			f"""update `tabAttendance` set `{AUTO_FIELD}` = 1
			where docstatus = 1 and owner = 'Administrator' and attendance_date >= %s
			and ifnull(custom_punches, '') != '' and ifnull(amended_from, '') = ''
			and ifnull(leave_application, '') = '' and ifnull(attendance_request, '') = ''""",
			start,
		)
	frappe.db.commit()
