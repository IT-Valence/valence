"""Leave Planning Score: how much leave each department applied for in advance.

For a period, per department: applications in advance, after the fact on time,
late, the share in advance, and unplanned absence (working days with no
attendance, or marked Absent, and no leave application).
"""
from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import add_days, add_months, get_first_day, get_last_day, getdate, today


def execute(filters=None):
	f = frappe._dict(filters or {})
	last_month = add_months(getdate(today()), -1)
	start = getdate(f.from_date or get_first_day(last_month))
	end = getdate(f.to_date or get_last_day(last_month))
	try:
		from valence.valence.punch_attendance.engine import _settings, last_complete_day
		end = min(end, last_complete_day())
		exempt = _settings().exempt
	except Exception:
		exempt = set()

	emp_filters = {"status": "Active"}
	if f.department:
		emp_filters["department"] = f.department
	emps = [e for e in frappe.get_all("Employee", filters=emp_filters, fields=["name", "department", "holiday_list", "company", "date_of_joining"]) if e.name not in exempt]
	dept_of = {e.name: e.department or _("(No department)") for e in emps}
	company_hl = {c.name: c.default_holiday_list for c in frappe.get_all("Company", fields=["name", "default_holiday_list"])}
	lists = {e.holiday_list or company_hl.get(e.company) for e in emps} - {None}
	off = defaultdict(set)
	if lists and start <= end:
		for h in frappe.get_all("Holiday", filters={"parent": ["in", list(lists)], "holiday_date": ["between", [start, end]]}, fields=["parent", "holiday_date"]):
			off[h.parent].add(getdate(h.holiday_date))

	rows = defaultdict(lambda: defaultdict(float))
	apps = frappe.get_all(
		"Leave Application",
		filters={"employee": ["in", list(dept_of) or [""]], "docstatus": ["<", 2], "from_date": ["between", [start, getdate(f.to_date or end)]]},
		fields=["employee", "custom_leave_timing as timing", "from_date", "to_date"],
	)
	covered = set()
	for a in apps:
		r = rows[dept_of[a.employee]]
		r["applications"] += 1
		r[{"In advance": "advance", "After the fact (on time)": "after", "Late": "late"}.get(a.timing, "untagged")] += 1
	for a in frappe.get_all("Leave Application", filters={"employee": ["in", list(dept_of) or [""]], "docstatus": ["<", 2], "from_date": ["<=", end], "to_date": [">=", start]}, fields=["employee", "from_date", "to_date"]):
		d = getdate(a.from_date)
		while d <= getdate(a.to_date):
			covered.add((a.employee, d))
			d = getdate(add_days(d, 1))

	att = {}
	if start <= end:
		for x in frappe.get_all("Attendance", filters={"docstatus": 1, "attendance_date": ["between", [start, end]], "employee": ["in", list(dept_of) or [""]]}, fields=["employee", "attendance_date", "status"]):
			att[(x.employee, getdate(x.attendance_date))] = x.status
	for e in emps:
		hl = off[e.holiday_list or company_hl.get(e.company)]
		d = start
		while d <= end:
			if d not in hl and not (e.date_of_joining and d < getdate(e.date_of_joining)) and (e.name, d) not in covered:
				st = att.get((e.name, d))
				if st is None or st == "Absent":
					rows[dept_of[e.name]]["unplanned"] += 1
			d = getdate(add_days(d, 1))

	data = []
	for dept, r in sorted(rows.items()):
		n = r["applications"]
		data.append({
			"department": dept, "applications": n, "advance": r["advance"], "after": r["after"], "late": r["late"],
			"advance_percent": round(100 * r["advance"] / n, 1) if n else None, "unplanned": r["unplanned"],
		})
	columns = [
		{"label": _("Department"), "fieldname": "department", "fieldtype": "Link", "options": "Department", "width": 200},
		{"label": _("Applications"), "fieldname": "applications", "fieldtype": "Int", "width": 100},
		{"label": _("In advance"), "fieldname": "advance", "fieldtype": "Int", "width": 100},
		{"label": _("After, on time"), "fieldname": "after", "fieldtype": "Int", "width": 110},
		{"label": _("Late"), "fieldname": "late", "fieldtype": "Int", "width": 80},
		{"label": _("% in advance"), "fieldname": "advance_percent", "fieldtype": "Percent", "width": 110},
		{"label": _("Unplanned absence (days)"), "fieldname": "unplanned", "fieldtype": "Float", "precision": 1, "width": 170},
	]
	return columns, data
