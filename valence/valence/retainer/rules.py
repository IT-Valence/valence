"""Paid days for a retainer (contractor) from one employee's attendance.

No leave rules apply. Every calendar day is paid except working days the person
was away: no attendance, Absent or On Leave count 1 day off, Half Day counts
1/2. Present, On Duty and Work From Home are paid. Weekly offs and holidays are
paid.
"""
OFF_FULL = {None, "Absent", "On Leave"}


def paid_days(days_in_month: int, working_days, status_by_day: dict) -> float:
	off = 0.0
	for d in working_days:
		st = status_by_day.get(d)
		if st in OFF_FULL:
			off += 1
		elif st == "Half Day":
			off += 0.5
	return max(days_in_month - off, 0.0)


def amount(monthly_fee: float, paid: float, days_in_month: int) -> float:
	return round(monthly_fee * paid / days_in_month)
