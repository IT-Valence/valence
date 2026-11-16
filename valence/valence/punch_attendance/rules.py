"""Roster-free attendance rules agreed with HR in November 2026.

Pure functions only (no Frappe), so they can be tested on their own.

How a day is decided:
- Punches are deduplicated (within 60 seconds) and grouped into duties
  without a roster. Grouping minimises a cost: single punches and very short
  spans are expensive; normal 8-hour spans are free. Two punches more than
  13.5 hours apart are never joined (that would depend on which later
  punches had synced), so a missed OUT shows as a single punch for HR.
- A duty with a double punch at a shift change (or one punch in the middle
  of two full shifts) is split into two duties.
- A duty belongs to the day it starts; duties starting before 04:00 belong
  to the previous day (end of a night shift).
- 8-hour staff: full day 7h50, short leave from 6h (twice a month), half day
  3h50, absent below. Security: full day 6h50, short leave from 5h, half day
  3h50.
- Overtime beyond the shift (8h, or 7h for security): half OT from 3h50
  extra, full OT from 7h50 extra (6h50 for security).
- Mispunch flags only highlight a day for HR; they do not change the status.
"""
from collections import defaultdict
from datetime import timedelta

H350 = 3 + 50 / 60
H650 = 6 + 50 / 60
H750 = 7 + 50 / 60
MAX_SPAN = 17.5
LONG_GAP = 13.5
CHANGE_GAP = 0.75


def rules_for(security: bool) -> dict:
	if security:
		return dict(full=H650, half=H350, short=5.0, shift=7.0, ot_half=H350, ot_full=H650)
	return dict(full=H750, half=H350, short=6.0, shift=8.0, ot_half=H350, ot_full=H750)


def _hours(a, b) -> float:
	return (b - a).total_seconds() / 3600


def dedupe(times):
	out = []
	for t in sorted(times):
		if not out or (t - out[-1]).total_seconds() > 60:
			out.append(t)
	return out


def pair(ts):
	"""Group sorted punch times into duties at the lowest total cost."""
	n = len(ts)
	best = [float("inf")] * (n + 1)
	prev = [0] * (n + 1)
	best[0] = 0
	for j in range(1, n + 1):
		for i in range(j - 1, -1, -1):
			span = _hours(ts[i], ts[j - 1])
			if span > MAX_SPAN:
				break
			gaps = [_hours(ts[k], ts[k + 1]) for k in range(i, j - 1)]
			if i == j - 1:
				cost = 10
			elif span < 2.5:
				cost = 6
			elif max(gaps) > LONG_GAP:
				cost = 25
			elif span > LONG_GAP:
				cost = 1
			elif span > 9.5:
				cost = 0.5
			else:
				cost = 0
			if best[i] + cost < best[j]:
				best[j] = best[i] + cost
				prev[j] = i
	out, j = [], n
	while j > 0:
		i = prev[j]
		out.append(ts[i:j])
		j = i
	return out[::-1]


def split_change(du):
	"""Split a duty at a shift-change double punch, or at a single middle punch
	with a full shift on both sides."""
	for k in range(1, len(du) - 1):
		if (
			_hours(du[k], du[k + 1]) < CHANGE_GAP
			and _hours(du[0], du[k]) >= H350
			and _hours(du[k + 1], du[-1]) >= H350
		):
			return split_change(du[: k + 1]) + split_change(du[k + 1 :])
	for k in range(1, len(du) - 1):
		if _hours(du[0], du[k]) >= H650 and _hours(du[k], du[-1]) >= H650:
			return split_change(du[: k + 1]) + split_change(du[k:])
	return [du]


def duty_date(du):
	start = du[0]
	return (start - timedelta(hours=4)).date() if start.hour < 4 else start.date()


def duties_by_day(times):
	res = defaultdict(list)
	for du0 in pair(dedupe(times)):
		for du in split_change(du0):
			res[duty_date(du)].append(du)
	return res


def flag(du):
	if len(du) == 1:
		return "Single punch"
	gaps = [_hours(a, b) for a, b in zip(du, du[1:])]
	span = _hours(du[0], du[-1])
	if max(gaps) > LONG_GAP:
		return "Over 13.5h without a punch"
	if span > LONG_GAP and not any(g < CHANGE_GAP for g in gaps[1:-1]):
		return "Long duty without double punch at shift change"
	if span < H350:
		return "Under 3h50"
	return None


def evaluate(day_duties, security: bool, short_used: int, off_day: bool = False):
	"""Return (status, hours, ot_units, used_short_leave, flags) for one day.

	On a holiday or weekly off the day itself is not counted; the hours worked
	are reported as OT (1 for a full day, 0.5 for a half day) and no short leave
	is used.
	"""
	r = rules_for(security)
	hours = 0.0
	flags = []
	for du in day_duties:
		f = flag(du)
		if f:
			flags.append(f)
		if len(du) > 1:
			hours += _hours(du[0], du[-1])
	used_short = False
	if hours >= r["full"]:
		status = "Present"
	elif hours >= r["short"] and short_used < 2 and not off_day:
		status, used_short = "Present", True
	elif hours >= r["half"]:
		status = "Half Day"
	else:
		status = "Absent"
	if off_day:
		ot = {"Present": 1.0, "Half Day": 0.5}.get(status, 0.0)
		extra = hours - r["shift"]
		if extra >= r["ot_full"]:
			ot += 1
		elif extra >= r["ot_half"]:
			ot += 0.5
	else:
		extra = hours - r["shift"]
		ot = 1.0 if extra >= r["ot_full"] else 0.5 if extra >= r["ot_half"] else 0.0
	return status, round(hours, 2), ot, used_short, flags


def reconcile(existing, planned):
	"""What to do with a day: "create", "replace", "cancel" or None.

	existing: the engine's current Attendance for the day (or None).
	planned: the fields the punches give now (see engine._record), or None
	when the day should have no attendance (no punches).
	"""
	if not existing:
		return "create" if planned else None
	if not planned:
		return "cancel"
	same = (
		existing.get("status") == planned["status"]
		and abs((existing.get("working_hours") or 0) - (planned["working_hours"] or 0)) < 0.01
		and abs((existing.get("custom_ot_units") or 0) - (planned["custom_ot_units"] or 0)) < 0.01
		and (existing.get("custom_punches") or None) == planned["custom_punches"]
		and (existing.get("custom_mispunch_reason") or None) == planned["custom_mispunch_reason"]
	)
	return None if same else "replace"
