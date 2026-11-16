"""Upload weekly offs from Excel into each employee's own holiday list.

Saving checks the file and shows what will happen; submitting applies it:
- each employee gets their own list "WO-<code>-<year>" if they don't have
  one, copied from their current list (so months not in the file keep their
  old weekly offs);
- with "Replace" ticked, weekly offs already in the list for the months in
  the file are removed first (festival holidays stay);
- the dates are added as weekly offs, and the employees' attendance is
  re-marked from punches so holidays and OT are right.
"""
from collections import defaultdict
from datetime import date, datetime

import frappe
from frappe.model.document import Document
from frappe.utils import cint, getdate

DATE_FORMATS = ("%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%Y-%m-%d", "%d-%m-%y", "%d/%m/%y", "%d-%b-%Y", "%d %b %Y")


class WeeklyOffUpload(Document):
	def validate(self):
		rows, errors = read_rows(self.upload_file)
		self.employees_count = len({e for e, _ in rows})
		self.days_count = len(rows)
		self.has_errors = 1 if errors else 0
		lines = [f"{self.days_count} weekly offs for {self.employees_count} employees."]
		if errors:
			lines += ["", "Fix these rows in the file, attach it again and save:"] + errors[:200]
		else:
			lines.append("No errors. Submit to add them.")
		self.result = "\n".join(lines)

	def before_submit(self):
		if self.has_errors:
			frappe.throw("The file has errors (see Check). Fix them before submitting.")
		if not self.days_count:
			frappe.throw("The file has no weekly offs.")

	def on_submit(self):
		rows, _ = read_rows(self.upload_file)
		log = apply(rows, cint(self.replace_existing))
		self.db_set("result", "\n".join(log))


def read_rows(file_url):
	"""Return ([(employee, date)], [error lines]) from an attached xlsx or csv."""
	if not file_url:
		return [], ["Attach a file."]
	f = frappe.get_doc("File", {"file_url": file_url})
	name = (f.file_name or file_url).lower()
	content = f.get_content()
	if name.endswith(".xlsx"):
		from frappe.utils.xlsxutils import read_xlsx_file_from_attached_file

		data = read_xlsx_file_from_attached_file(fcontent=content)
	elif name.endswith(".csv"):
		from frappe.utils.csvutils import read_csv_content

		data = read_csv_content(content)
	else:
		return [], ["The file must be .xlsx or .csv."]

	data = [list(r) for r in data if r and any(v not in (None, "") for v in r)]
	if not data:
		return [], ["The file is empty."]
	header = [str(v or "").strip().lower() for v in data[0]]
	emp_col = next((i for i, h in enumerate(header) if h in ("employee", "employee code", "emp code", "code", "employee id")), None)
	date_col = next((i for i, h in enumerate(header) if "date" in h or "weekly off" in h), None)
	if emp_col is None or date_col is None:
		return [], ["The first row must have the headings Employee and Weekly Off Date."]

	known = set(frappe.get_all("Employee", pluck="name"))
	rows, errors, seen = [], [], set()
	for n, r in enumerate(data[1:], start=2):
		emp = _code(r[emp_col] if emp_col < len(r) else None)
		raw = r[date_col] if date_col < len(r) else None
		if raw in (None, ""):
			continue
		d = _date(raw)
		if not emp:
			errors.append(f"Row {n}: employee code missing.")
		elif emp not in known:
			errors.append(f"Row {n}: employee {emp} not found.")
		elif not d:
			errors.append(f"Row {n}: '{raw}' is not a date (use DD-MM-YYYY).")
		elif (emp, d) not in seen:
			seen.add((emp, d))
			rows.append((emp, d))
	return rows, errors


def _code(v):
	if v in (None, ""):
		return None
	if isinstance(v, float) and v.is_integer():
		v = int(v)
	return str(v).strip()


def _date(v):
	if isinstance(v, datetime):
		return v.date()
	if isinstance(v, date):
		return v
	s = str(v).strip()
	for fmt in DATE_FORMATS:
		try:
			return datetime.strptime(s, fmt).date()
		except ValueError:
			pass
	return None


def apply(rows, replace=True):
	by_emp = defaultdict(set)
	for emp, d in rows:
		by_emp[emp].add(d)
	log = []
	for emp in sorted(by_emp):
		dates = by_emp[emp]
		hl = frappe.get_doc("Holiday List", _own_list(emp, min(dates).year))
		months = {(d.year, d.month) for d in dates}
		removed = 0
		if replace:
			keep = []
			for h in hl.holidays:
				hd = getdate(h.holiday_date)
				if cint(h.weekly_off) and (hd.year, hd.month) in months and hd not in dates:
					removed += 1
					continue
				keep.append(h)
			hl.set("holidays", keep)
		have = {getdate(h.holiday_date) for h in hl.holidays}
		added = 0
		for d in sorted(dates - have):
			hl.append("holidays", {"holiday_date": d, "description": "Weekly Off", "weekly_off": 1})
			added += 1
		if max(dates) > getdate(hl.to_date):
			hl.to_date = date(max(dates).year, 12, 31)
		if min(dates) < getdate(hl.from_date):
			hl.from_date = date(min(dates).year, 1, 1)
		hl.flags.ignore_permissions = True
		hl.save()
		log.append(f"{emp} ({hl.name}): {added} added, {removed} removed, {len(dates) - added} already there")

	frappe.enqueue(
		"valence.valence.punch_attendance.engine.reprocess_employees",
		queue="long",
		employees=sorted(by_emp),
		from_date=str(min(d for _, d in rows)),
		enqueue_after_commit=True,
	)
	log.append("")
	log.append("Attendance for these employees is being re-marked from punches (takes a minute or two).")
	return log


def _own_list(emp, year):
	"""The employee's own weekly-off list, created from their current list if needed."""
	e = frappe.db.get_value("Employee", emp, ["holiday_list", "company"], as_dict=True)
	current = e.holiday_list or frappe.db.get_value("Company", e.company, "default_holiday_list")
	if current and current.startswith(f"WO-{emp}-") and frappe.db.count("Employee", {"holiday_list": current}) <= 1:
		return current
	name = f"WO-{emp}-{year}"
	if not frappe.db.exists("Holiday List", name):
		hl = frappe.new_doc("Holiday List")
		hl.holiday_list_name = name
		hl.from_date = date(year, 1, 1)
		hl.to_date = date(year, 12, 31)
		if current:
			for h in frappe.get_all(
				"Holiday",
				filters={"parent": current, "holiday_date": ["between", [hl.from_date, hl.to_date]]},
				fields=["holiday_date", "description", "weekly_off"],
			):
				hl.append("holidays", {"holiday_date": h.holiday_date, "description": h.description, "weekly_off": h.weekly_off})
		hl.flags.ignore_permissions = True
		hl.insert()
	frappe.db.set_value("Employee", emp, "holiday_list", name)
	return name
