"""Draft a Purchase Invoice for each enabled Retainer on the 5th for the month before
(by then the last days' attendance has synced and been checked).

Amount = monthly fee x paid days / days in the month (see rules.py). TDS is
applied from the supplier's Tax Withholding Category. Invoices are left as
drafts for Accounts to check and submit. One invoice per retainer per month
(bill no RET/<supplier>/<YYYY-MM>).
"""
import calendar
from collections import defaultdict

import frappe
from frappe.utils import add_days, add_months, cint, get_first_day, get_last_day, getdate, today

from valence.valence.retainer import rules as R


def run_monthly():
	month_start = get_first_day(add_months(getdate(today()), -1))
	create_for_month(month_start)


@frappe.whitelist()
def run_for_month(month, dry_run=1):
	"""month: any date in the month. dry_run=1 returns the calculation only."""
	frappe.only_for(["System Manager", "Accounts Manager"])
	return create_for_month(get_first_day(getdate(month)), dry_run=cint(dry_run))


def _working_days(employee, start, end):
	emp = frappe.db.get_value("Employee", employee, ["holiday_list", "company"], as_dict=True)
	hl = emp.holiday_list or frappe.db.get_value("Company", emp.company, "default_holiday_list")
	off = set()
	if hl:
		off = {getdate(h) for h in frappe.get_all("Holiday", filters={"parent": hl, "holiday_date": ["between", [start, end]]}, pluck="holiday_date")}
	days, d = [], getdate(start)
	while d <= getdate(end):
		if d not in off:
			days.append(d)
		d = getdate(add_days(d, 1))
	return days


def create_for_month(month_start, dry_run=False):
	start, end = getdate(month_start), getdate(get_last_day(month_start))
	n = calendar.monthrange(start.year, start.month)[1]
	out = []
	cache = {}
	for r in frappe.get_all("Retainer", filters={"enabled": 1}, fields=["*"]):
		if r.attendance_employee not in cache:
			status = {
				getdate(a.attendance_date): a.status
				for a in frappe.get_all(
					"Attendance",
					filters={"employee": r.attendance_employee, "docstatus": 1, "attendance_date": ["between", [start, end]]},
					fields=["attendance_date", "status"],
				)
			}
			wd = _working_days(r.attendance_employee, start, end)
			cache[r.attendance_employee] = R.paid_days(n, wd, status)
		paid = cache[r.attendance_employee]
		amt = R.amount(r.monthly_fee, paid, n)
		bill_no = f"RET/{r.supplier}/{start.strftime('%Y-%m')}"
		row = dict(retainer=r.name, supplier=r.supplier, month=start.strftime("%b %Y"), paid_days=paid, days=n, amount=amt, bill_no=bill_no)
		if frappe.db.exists("Purchase Invoice", {"supplier": r.supplier, "bill_no": bill_no, "docstatus": ["<", 2]}):
			row["skipped"] = "already exists"
		elif not dry_run and amt > 0:
			pi = frappe.get_doc(
				{
					"doctype": "Purchase Invoice",
					"company": r.company,
					"supplier": r.supplier,
					"posting_date": getdate(today()),
					"set_posting_time": 1,
					"bill_no": bill_no,
					"bill_date": end,
					"apply_tds": 1,
					"remarks": f"{r.service_description} for {start.strftime('%B %Y')}: {paid:g} of {n} days paid, from attendance of {r.attendance_employee}.",
					"items": [
						{
							"item_code": r.item,
							"description": f"{r.service_description} – {start.strftime('%B %Y')} ({paid:g}/{n} days)",
							"qty": 1,
							"rate": amt,
							"expense_account": r.expense_account,
						}
					],
				}
			)
			pi.flags.ignore_permissions = True
			pi.insert()
			row["invoice"] = pi.name
		out.append(row)
	if not dry_run:
		frappe.db.commit()
	return out
