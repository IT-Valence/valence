"""Mark attendance from biometric punches without shift rosters.

Runs daily (see hooks.py) over the last REVIEW_DAYS days, up to the last
day that can be finalised (see last_complete_day: two days after), from
"Punch attendance from" in Attendance Settings.

Each day is worked out again from the punches on every run:
- no attendance yet: it is created (days without punches stay unmarked;
  payroll treats them as absent);
- attendance this engine made ("Marked from punches"): it is cancelled and
  amended if the result changed, for example after HR adds a missed punch
  (device "HR-CORRECTION"), a late punch syncs, or a weekly off is added;
- anything else is never touched: leave, On Duty, and records HR made or
  amended by hand. Days in a submitted salary slip are never changed.

A punch HR adds or deletes re-marks that employee within a minute (see
on_checkin_change), so HR only fixes the punch, not the attendance.

Each record carries its status in Final Status, so the Valence status logic
keeps it, plus hours, OT (days), the punches used and any mispunch reason
for HR's Mispunch List.
"""
from collections import defaultdict
from datetime import datetime, timedelta

import frappe
from frappe.utils import add_days, cint, get_datetime, getdate, today

from valence.valence.punch_attendance import rules as R

REVIEW_DAYS = 40
CONTEXT_DAYS = 2
CUTOFF = datetime.strptime("08:00", "%H:%M").time()
AUTO_FIELD = "custom_auto_marked"
SYNC_DEVICES = ("essl",)


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
	process(max(s.start, getdate(add_days(yesterday, -(REVIEW_DAYS - 1)))), yesterday)


@frappe.whitelist()
def run_for_dates(from_date, to_date, dry_run=1, employee=None):
	"""Backfill or preview a date range. dry_run=1 returns what would change."""
	frappe.only_for("System Manager")
	to_date = min(getdate(to_date), getdate(add_days(today(), -1)))
	return process(getdate(from_date), to_date, dry_run=cint(dry_run), employees=[employee] if employee else None)


def reprocess_employees(employees, from_date):
	"""Re-mark employees from the start of from_date's month (so short leaves count in order)."""
	frappe.set_user("Administrator")
	s = _settings()
	if not s.enabled or not s.start:
		return
	start = getdate(from_date).replace(day=1)
	return process(start, getdate(add_days(today(), -1)), employees=list(employees))


def on_checkin_change(doc, method=None):
	"""A punch added, edited or deleted by hand re-marks that employee.

	Punches from the sync tool are picked up by the daily run instead, so a
	sync of thousands of punches does not start thousands of jobs.
	"""
	if (doc.device_id or "") in SYNC_DEVICES or not doc.employee or not doc.time:
		return
	s = _settings()
	if not s.enabled or not s.start:
		return
	day = getdate(add_days(getdate(get_datetime(doc.time)), -1))
	if day < s.start.replace(day=1):
		return
	frappe.enqueue(
		"valence.valence.punch_attendance.engine.reprocess_employees",
		queue="short",
		employees=[doc.employee],
		from_date=str(day),
		job_id=f"punch-attendance-{doc.employee}",
		deduplicate=True,
		enqueue_after_commit=True,
	)


def last_complete_day():
	"""Latest day that can be finalised.

	Punches are grouped using two days of context on each side, so a day is
	finalised once punches up to 08:00 two days later have reached ERP (for
	example, Monday after Wednesday morning's sync). Without that context a
	next-morning punch could be attached to the wrong day, and the result
	would change as more punches synced.
	"""
	last = frappe.db.sql(
		"select max(time) from `tabEmployee Checkin` where ifnull(device_id, '') != 'HR-CORRECTION'"
	)[0][0]
	if not last:
		return getdate("1900-01-01")
	last = get_datetime(last)
	day = last.date() - timedelta(days=CONTEXT_DAYS)
	if last.time() < CUTOFF:
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


def _employees(company_holiday_list, from_date, to_date, only=None):
	filters = {"status": ["in", ["Active", "Left"]]}
	if only:
		filters["name"] = ["in", list(only)]
	emps = frappe.get_all(
		"Employee",
		filters=filters,
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


def _record(p):
	"""The fields this engine writes, as stored on Attendance."""
	notes = [f for f in p["flags"] if f not in ("Short leave", "Exempt from attendance")]
	return dict(
		status=p["status"],
		working_hours=p["hours"],
		custom_ot_units=p["ot"],
		custom_mispunch_reason="; ".join(notes) or None,
		custom_punches=", ".join(t.strftime("%d-%m %H:%M") for t in p["punches"]) or "; ".join(p["flags"]) or None,
	)


def _counts_as_short(a, r):
	return a.status == "Present" and not a.custom_ot_units and r["short"] <= (a.working_hours or 0) < r["full"]


def process(from_date, to_date, dry_run=False, employees=None):
	s = _settings()
	if s.start:
		from_date = max(from_date, s.start)
	to_date = min(to_date, last_complete_day())
	if from_date > to_date:
		return {"created": 0, "skipped": "nothing to do"}

	company_hl = {c.name: c.default_holiday_list for c in frappe.get_all("Company", fields=["name", "default_holiday_list"])}
	emps = _employees(company_hl, from_date, to_date, employees)
	if not emps:
		return {"created": 0, "skipped": "no employees"}
	names = [e.name for e in emps]
	holidays = _holidays({e.holiday_list for e in emps if e.holiday_list}, from_date, to_date)
	has_auto = frappe.get_meta("Attendance").has_field(AUTO_FIELD)

	month_start = from_date.replace(day=1)
	existing = {}
	short_before = []
	for a in frappe.get_all(
		"Attendance",
		filters={"docstatus": ["<", 2], "employee": ["in", names], "attendance_date": ["between", [month_start, to_date]]},
		fields=["name", "employee", "attendance_date", "docstatus", "status", "working_hours", "custom_ot_units",
			"custom_punches", "custom_mispunch_reason", "leave_application", "attendance_request"]
			+ ([AUTO_FIELD] if has_auto else []),
	):
		a.attendance_date = getdate(a.attendance_date)
		if a.attendance_date < from_date:
			short_before.append(a)
		else:
			existing[(a.employee, a.attendance_date)] = a

	# days in a submitted salary slip are final
	locked = {}
	for ss in frappe.get_all(
		"Salary Slip",
		filters={"docstatus": 1, "employee": ["in", names], "end_date": [">=", from_date]},
		fields=["employee", "end_date"],
	):
		locked[ss.employee] = max(locked.get(ss.employee, getdate("1900-01-01")), getdate(ss.end_date))

	punches = defaultdict(list)
	for c in frappe.get_all(
		"Employee Checkin",
		filters={
			"employee": ["in", names],
			"time": [
				"between",
				[
					datetime.combine(getdate(add_days(from_date, -CONTEXT_DAYS)), datetime.min.time()),
					datetime.combine(getdate(add_days(to_date, CONTEXT_DAYS)), CUTOFF),
				],
			],
			"skip_auto_attendance": 0,
		},
		fields=["employee", "time"],
	):
		punches[c.employee].append(get_datetime(c.time))

	# short leaves already used this month, before from_date
	short_used = defaultdict(int)
	for a in short_before:
		if a.docstatus == 1 and _counts_as_short(a, R.rules_for(a.employee in s.security)):
			short_used[(a.employee, a.attendance_date.strftime("%Y-%m"))] += 1

	actions = []
	for e in emps:
		days = R.duties_by_day(punches.get(e.name, []))
		off_days = holidays.get(e.holiday_list, set())
		security = e.name in s.security
		r = R.rules_for(security)
		d = from_date
		while d <= to_date:
			cur, d = d, getdate(add_days(d, 1))
			if e.date_of_joining and cur < getdate(e.date_of_joining):
				continue
			if e.relieving_date and cur > getdate(e.relieving_date):
				continue
			key = (e.name, cur.strftime("%Y-%m"))
			off = cur in off_days
			ex = existing.get((e.name, cur))
			ours = bool(
				ex and has_auto and ex.get(AUTO_FIELD) and ex.docstatus == 1
				and not ex.leave_application and not ex.attendance_request
			)
			if ex and (not ours or cur <= locked.get(e.name, getdate("1900-01-01"))):
				if ex.docstatus == 1 and not off and _counts_as_short(ex, r):
					short_used[key] += 1
				continue

			p = None
			if e.name in s.exempt:
				if not off:
					p = dict(employee=e.name, date=cur, status="Present", hours=0, ot=0, flags=["Exempt from attendance"], punches=[], company=e.company)
			elif days.get(cur):
				status, hours, ot, used, flags = R.evaluate(days[cur], security, short_used[key], off)
				short_used[key] += used
				if used:
					flags.append("Short leave")
				p = dict(employee=e.name, date=cur, status=status, hours=hours, ot=ot, flags=flags,
					punches=[t for du in days[cur] for t in du], company=e.company)

			action = R.reconcile(ex, _record(p) if p else None)
			if action:
				actions.append((action, ex.name if ex else None, p))

	if dry_run:
		return {
			"would_create": sum(1 for a in actions if a[0] == "create"),
			"would_replace": sum(1 for a in actions if a[0] == "replace"),
			"would_cancel": sum(1 for a in actions if a[0] == "cancel"),
			"rows": [
				dict(action=act, attendance=name, employee=p["employee"] if p else existing_emp(existing, name),
					**({**p, "date": str(p["date"]), "punches": [t.strftime("%d %H:%M") for t in p["punches"]]} if p else {}))
				for act, name, p in actions
			],
		}

	done = defaultdict(int)
	failed = 0
	for act, name, p in actions:
		try:
			if act in ("replace", "cancel"):
				old = frappe.get_doc("Attendance", name)
				old.flags.ignore_permissions = True
				old.cancel()
			if act in ("create", "replace"):
				_create(p, amended_from=name if act == "replace" else None)
			done[act] += 1
			if sum(done.values()) % 50 == 0:
				frappe.db.commit()
		except Exception:
			failed += 1
			frappe.db.rollback()
			who = p["employee"] if p else name
			frappe.log_error(frappe.get_traceback(), f"Punch attendance failed: {who} {p['date'] if p else ''}")
	frappe.db.commit()
	return {"created": done["create"], "replaced": done["replace"], "cancelled": done["cancel"], "failed": failed}


def existing_emp(existing, name):
	for a in existing.values():
		if a.name == name:
			return a.employee


def _create(p, amended_from=None):
	doc = frappe.get_doc(
		{
			"doctype": "Attendance",
			"employee": p["employee"],
			"attendance_date": p["date"],
			"company": p.get("company") or frappe.db.get_value("Employee", p["employee"], "company"),
			"custom_final_status": p["status"],
			"half_day_status": "Absent" if p["status"] == "Half Day" else None,
			"amended_from": amended_from,
			**_record(p),
		}
	)
	if frappe.get_meta("Attendance").has_field(AUTO_FIELD):
		doc.set(AUTO_FIELD, 1)
	if len(p["punches"]) > 1:
		doc.in_time, doc.out_time = p["punches"][0], p["punches"][-1]
	doc.flags.ignore_permissions = True
	doc.insert()
	doc.submit()
