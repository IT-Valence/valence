"""Leave notice and approval-deadline rules (no Frappe, so they can be unit tested).

Timing of an application, from the leave type's settings:
- "In advance": applied at least `notice_days` before the leave starts
  (for notice_days 0, on or before the start day).
- "After the fact (on time)": the leave type allows applying afterwards and the
  application came no later than `grace_days` after the leave ended.
- "Late": anything else. Only HR can approve or reject a late application.

HOD deadline: `decision_days` after the application, or the leave's start date
if that is sooner and still in the future. Super HOD gets `decision_days` more.
After a deadline only HR can approve or reject.
"""
from datetime import date, timedelta

IN_ADVANCE = "In advance"
ON_TIME_AFTER = "After the fact (on time)"
LATE = "Late"


def classify(applied_on: date, from_date: date, to_date: date, notice_days: int, allow_after: bool, grace_days: int) -> str:
	if applied_on <= from_date - timedelta(days=max(notice_days, 0)):
		return IN_ADVANCE
	if allow_after and applied_on <= to_date + timedelta(days=max(grace_days, 0)):
		return ON_TIME_AFTER
	return LATE


def hod_deadline(applied_on: date, from_date: date, decision_days: int) -> date:
	deadline = applied_on + timedelta(days=decision_days)
	if applied_on < from_date < deadline:
		deadline = from_date
	return deadline


def super_hod_deadline(applied_on: date, from_date: date, decision_days: int) -> date:
	return hod_deadline(applied_on, from_date, decision_days) + timedelta(days=decision_days)


def is_after_the_fact(applied_on: date, from_date: date) -> bool:
	return applied_on > from_date
