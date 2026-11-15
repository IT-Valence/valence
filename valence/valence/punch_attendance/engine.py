"""Mark attendance from biometric punches without shift rosters.

Runs daily (see hooks.py) for the last LOOKBACK_DAYS days, up to the last
day whose punches have fully synced (see last_complete_day),
from "Punch attendance from" in Attendance Settings. It only creates
attendance where none exists, so leave, On Duty and HR's corrections are
never overwritten, and punches that reach ERP late are picked up on a later
run. Days without punches are left unmarked (payroll treats them as absent).

Each record carries its status in Final Status, so the Valence status logic
keeps it, plus hours, OT (days), the punches used and any mispunch reason
for HR's Mispunch List.
"""
from collections import defaultdict
from datetime import datetime, timedelta

import frappe
from frappe.utils import add_days, get_datetime, getdate, today

from valence.valence.punch_attendance import rules as R

LOOKBACK_DAYS = 10


def _codes(text):
	return {c.strip() for c in (text or "").replace(",", "\n").split() if c.strip()}


def _settings():
	s = frappe.get_single("Attendance Settings")
	return frappe._dict(
		enabled=s.get("punch_attendance_enabled"),
		start=getdate(s.get("punch_attendance_from")) if s.get("punch_attendance_from") else None,
		exempt=_codes(s.get("attendance_exempt_employees")),
		security=_codes(s.get("security_employees")),
	)


def run_daily():
	s = _settings()
	if not s.enabled or not s.start:
		return
	yesterday = getdate(add_days(today(), -1))
	process(max(s.start, getdate(add_days(yesterday, -(LOOKBACK_DAYS - 1)))), yesterday)


@frappe.whitelist()
def run_for_dates(from_date, to_date, dry_run=1):
	"""Backfill or preview a date range. dry_run=1 returns what would be created."""
	frappe.only_for("System Manager")
	to_date = min(getdate(to_date), getdate(add_days(today(), -1)))
	return process(getdate(from_date), to_date, dry_run=frappe.utils.cint(dry_run))


def last_complete_day():
	"""Latest day whose punches have fully reached ERP.

	A day counts as complete once the sync has brought in punches up to 08:00
	the next morning (after night shifts end). This stops a half-synced day
	from being marked with missing punches.
	"""
	last = frappe.db.sql(
		"select max(time) from `tabEmployee Checkin` where ifnull(device_id, '') != 'HR-CORRECTION'"
	)[0][0]
	if not last:
		return getdate("1900-01-01")
	last = get_datetime(last)
	day = last.date() - timedelta(days=1)
	if last.time() < datetime.strptime("08:00", "%H:%M").time():
		day -= timedelta(days=1)
	return day


def _holidays(lists, from_date, to_date):
	out = defaultdict(set)
	if lists:
		for h in frappe.get_all(
			"Holiday",
			filters={"parent": ["in", list(lists)], "holiday_date": ["between", [from_date, to_date]]},
			fields=["parent", "holiday_date"],
		):
			out[h.parent].add(getdate(h.holiday_date))
	return out


def _employees(company_holiday_list, from_date, to_date):
	emps = frappe.get_all(
		"Employee",
		filters={"status": ["in", ["Active", "Left"]]},
		fields=["name", "employee_name", "company", "holiday_list", "date_of_joining", "relieving_date", "status"],
	)
	out = []
	for e in emps:
		if e.date_of_joining and getdate(e.date_of_joining) > to_date:
			continue
		if e.relieving_date and getdate(e.relieving_date) < from_date:
			continue
		if e.status == "Left" and not e.relieving_date:
			continue
		e.holiday_list = e.holiday_list or company_holiday_list.get(e.company)
		out.append(e)
	return out


def process(from_date, to_date, dry_run=False):
	s = _settings()
	if s.start:
		from_date = max(from_date, s.start)
	to_date = min(to_date, last_complete_day())
	if from_date > to_date:
		return {"created": 0, "skipped": "nothing to do"}

	company_hl = {c.name: c.default_holiday_list for c in frappe.get_all("Company", fields=["name", "default_holiday_list"])}
	emps = _employees(company_hl, from_date, to_date)
	holidays = _holidays({e.holiday_list for e in emps if e.holiday_list}, from_date, to_date)

	existing = {
		(a.employee, getdate(a.attendance_date))
		for a in frappe.get_all(
			"Attendance",
			filters={"docstatus": ["<", 2], "attendance_date": ["between", [from_date, to_date]]},
			fields=["employee", "attendance_date"],
		)
	}

	punches = defaultdict(list)
	for c in frappe.get_all(
		"Employee Checkin",
		filters={
			"time": ["between", [datetime.combine(add_days(from_date, -1), datetime.min.time()), datetime.combine(add_days(to_date, 1), datetime.min.time()) + timedelta(hours=12)]],
			"skip_auto_attendance": 0,
		},
		fields=["employee", "time"],
	):
		punches[c.employee].append(get_datetime(c.time))

	# short leaves already used this month, counted from earlier attendance
	month_start = from_date.replace(day=1)
	short_used = defaultdict(int)
	for a in frappe.get_all(
		"Attendance",
		filters={"docstatus": 1, "status": "Present", "attendance_date": ["between", [month_start, add_days(from_date, -1)]], "working_hours": [">", 0], "custom_ot_units": 0},
		fields=["employee", "attendance_date", "working_hours"],
	):
		r = R.rules_for(a.employee in s.security)
		if r["short"] <= a.working_hours < r["full"]:
			short_used[(a.employee, getdate(a.attendance_date).strftime("%Y-%m"))] += 1

	plan = []
	for e in emps:
		days = R.duties_by_day(punches.get(e.name, []))
		off_days = holidays.get(e.holiday_list, set())
		d = from_date
		while d <= to_date:
			cur, d = d, getdate(add_days(d, 1))
			if (e.name, cur) in existing:
				continue
			if e.date_of_joining and cur < getdate(e.date_of_joining):
				continue
			if e.relieving_date and cur > getdate(e.relieving_date):
				continue
			off = cur in off_days
			if e.name in s.exempt:
				if not off:
					plan.append(dict(employee=e.name, date=cur, status="Present", hours=0, ot=0, flags=["Exempt from attendance"], punches=[]))
				continue
			day = days.get(cur)
			if not day:
				continue
			key = (e.name, cur.strftime("%Y-%m"))
			status, hours, ot, used, flags = R.evaluate(day, e.name in s.security, short_used[key], off)
			short_used[key] += used
			if used:
				flags.append("Short leave")
			plan.append(dict(employee=e.name, date=cur, status=status, hours=hours, ot=ot, flags=flags, punches=[t for du in day for t in du], company=e.company))

	if dry_run:
		return {"would_create": len(plan), "rows": [
			{**p, "date": str(p["date"]), "punches": [t.strftime("%d %H:%M") for t in p["punches"]]} for p in plan
		]}

	created, failed = 0, 0
	for p in plan:
		try:
			_create(p)
			created += 1
			if created % 50 == 0:
				frappe.db.commit()
		except Exception:
			failed += 1
			frappe.db.rollback()
			frappe.log_error(frappe.get_traceback(), f"Punch attendance failed: {p['employee']} {p['date']}")
	frappe.db.commit()
	return {"created": created, "failed": failed}


def _create(p):
	notes = [f for f in p["flags"] if f not in ("Short leave", "Exempt from attendance")]
	doc = frappe.get_doc(
		{
			"doctype": "Attendance",
			"employee": p["employee"],
			"attendance_date": p["date"],
			"company": p.get("company") or frappe.db.get_value("Employee", p["employee"], "company"),
			"status": p["status"],
			"custom_final_status": p["status"],
			"half_day_status": "Absent" if p["status"] == "Half Day" else None,
			"working_hours": p["hours"],
			"custom_ot_units": p["ot"],
			"custom_mispunch_reason": "; ".join(notes) or None,
			"custom_punches": ", ".join(t.strftime("%d-%m %H:%M") for t in p["punches"]) or "; ".join(p["flags"]) or None,
		}
	)
	if len(p["punches"]) > 1:
		doc.in_time, doc.out_time = p["punches"][0], p["punches"][-1]
	doc.flags.ignore_permissions = True
	doc.insert()
	doc.submit()
