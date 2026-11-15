"""Leave Plan: approved and pending leave per department per day, against headcount.

Lets HODs and shift in-charges see the coming days before approving more leave.
"""
from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate, today

PENDING = ("Pending HOD Approval", "Pending Super HOD Approval")


def execute(filters=None):
	f = frappe._dict(filters or {})
	start = getdate(f.from_date or today())
	days = min(max(cint(f.days) or 14, 1), 62)
	end = getdate(add_days(start, days - 1))
	alert = flt(f.alert_percent) or 15

	emp_filters = {"status": "Active"}
	if f.department:
		emp_filters["department"] = f.department
	if f.company:
		emp_filters["company"] = f.company
	emps = frappe.get_all("Employee", filters=emp_filters, fields=["name", "department", "holiday_list", "company", "date_of_joining"])
	company_hl = {c.name: c.default_holiday_list for c in frappe.get_all("Company", fields=["name", "default_holiday_list"])}
	lists = {e.holiday_list or company_hl.get(e.company) for e in emps} - {None}
	off = defaultdict(set)
	if lists:
		for h in frappe.get_all("Holiday", filters={"parent": ["in", list(lists)], "holiday_date": ["between", [start, end]]}, fields=["parent", "holiday_date"]):
			off[h.parent].add(getdate(h.holiday_date))

	dept_of = {e.name: e.department or _("(No department)") for e in emps}
	leaves = frappe.get_all(
		"Leave Application",
		filters={"employee": ["in", list(dept_of) or [""]], "docstatus": ["<", 2], "from_date": ["<=", end], "to_date": [">=", start],
			"status": ["not in", ["Rejected", "Cancelled"]]},
		fields=["employee", "from_date", "to_date", "half_day", "half_day_date", "workflow_state", "docstatus"],
	)

	head = defaultdict(float)
	approved = defaultdict(float)
	pending = defaultdict(float)
	for e in emps:
		hl = off[e.holiday_list or company_hl.get(e.company)]
		for i in range(days):
			d = getdate(add_days(start, i))
			if d in hl or (e.date_of_joining and d < getdate(e.date_of_joining)):
				continue
			head[(d, dept_of[e.name])] += 1
	for la in leaves:
		e = frappe._dict(next((x for x in emps if x.name == la.employee), {}))
		hl = off[e.get("holiday_list") or company_hl.get(e.get("company"))]
		d = max(getdate(la.from_date), start)
		while d <= min(getdate(la.to_date), end):
			if d not in hl:
				share = 0.5 if la.half_day and (not la.half_day_date or getdate(la.half_day_date) == d) else 1
				bucket = approved if la.docstatus == 1 else pending if la.workflow_state in PENDING else None
				if bucket is not None:
					bucket[(d, dept_of[la.employee])] += share
			d = getdate(add_days(d, 1))

	data = []
	for (d, dept), n in sorted(head.items()):
		a, p = approved[(d, dept)], pending[(d, dept)]
		away = round(100 * (a + p) / n, 1) if n else 0
		data.append({
			"date": d, "department": dept, "headcount": n, "approved": a, "pending": p,
			"available": n - a - p, "away_percent": away,
			"alert": _("Too many away") if away > alert else "",
		})
	columns = [
		{"label": _("Date"), "fieldname": "date", "fieldtype": "Date", "width": 100},
		{"label": _("Department"), "fieldname": "department", "fieldtype": "Link", "options": "Department", "width": 200},
		{"label": _("Scheduled"), "fieldname": "headcount", "fieldtype": "Float", "precision": 1, "width": 90},
		{"label": _("Approved leave"), "fieldname": "approved", "fieldtype": "Float", "precision": 1, "width": 110},
		{"label": _("Pending leave"), "fieldname": "pending", "fieldtype": "Float", "precision": 1, "width": 110},
		{"label": _("Available"), "fieldname": "available", "fieldtype": "Float", "precision": 1, "width": 90},
		{"label": _("% away"), "fieldname": "away_percent", "fieldtype": "Percent", "width": 80},
		{"label": _("Alert"), "fieldname": "alert", "fieldtype": "Data", "width": 120},
	]
	return columns, data
