import unittest
from datetime import date, datetime

from valence.valence.punch_attendance import rules as R


def t(day, hm):
	h, m = map(int, hm.split(":"))
	return datetime(2026, 11, day, h, m)


class TestPunchRules(unittest.TestCase):
	def day(self, times, security=False, short_used=0, off=False, on=None):
		days = R.duties_by_day(times)
		return R.evaluate(days[on or times[0].date()], security, short_used, off)

	def test_full_half_absent(self):
		self.assertEqual(self.day([t(2, "09:00"), t(2, "16:55")])[0], "Present")
		self.assertEqual(self.day([t(2, "09:00"), t(2, "13:00")])[0], "Half Day")
		self.assertEqual(self.day([t(2, "09:00"), t(2, "11:00")])[0], "Absent")

	def test_short_leave_twice_a_month(self):
		times = [t(2, "09:00"), t(2, "15:30")]
		self.assertEqual(self.day(times, short_used=0)[:4:3], ("Present", True))
		self.assertEqual(self.day(times, short_used=2)[0], "Half Day")

	def test_security_cutoffs(self):
		times = [t(2, "07:00"), t(2, "13:55")]
		self.assertEqual(self.day(times, security=True)[0], "Present")
		self.assertEqual(self.day(times, security=False)[0], "Present")  # 6h55 is a short leave for 8-hour staff
		self.assertTrue(self.day(times, security=False)[3])

	def test_night_shift_belongs_to_start_day(self):
		days = R.duties_by_day([t(2, "22:00"), t(3, "06:05")])
		self.assertIn(date(2026, 11, 2), days)
		self.assertNotIn(date(2026, 11, 3), days)

	def test_double_shift_split_and_ot(self):
		times = [t(2, "06:00"), t(2, "14:00"), t(2, "14:05"), t(2, "22:00")]
		days = R.duties_by_day(times)
		self.assertEqual(len(days[date(2026, 11, 2)]), 2)
		status, hours, ot, _, _ = R.evaluate(days[date(2026, 11, 2)], False, 0)
		self.assertEqual(status, "Present")
		self.assertEqual(ot, 1.0)

	def test_single_punch_flagged(self):
		status, hours, ot, used, flags = self.day([t(2, "09:00")])
		self.assertEqual(status, "Absent")
		self.assertIn("Single punch", flags)

	def test_no_join_over_long_gap(self):
		# evening IN, next morning IN: two single punches, whatever comes later
		alone = R.duties_by_day([t(2, "16:10"), t(3, "08:55")])
		later = R.duties_by_day([t(2, "16:10"), t(3, "08:55"), t(3, "17:00")])
		for days in (alone, later):
			status, hours, ot, used, flags = R.evaluate(days[date(2026, 11, 2)], False, 0)
			self.assertEqual((status, hours), ("Absent", 0))
			self.assertIn("Single punch", flags)

	def test_weekly_off_counts_as_ot(self):
		status, hours, ot, used, flags = self.day([t(1, "09:00"), t(1, "17:00")], off=True)
		self.assertEqual((status, ot, used), ("Present", 1.0, False))

	def test_reconcile(self):
		rec = dict(status="Absent", working_hours=0, custom_ot_units=0, custom_punches="07-11 06:55", custom_mispunch_reason="Single punch")
		self.assertEqual(R.reconcile(None, rec), "create")
		self.assertIsNone(R.reconcile(None, None))
		self.assertIsNone(R.reconcile(dict(rec), dict(rec)))
		fixed = dict(status="Present", working_hours=7.17, custom_ot_units=0, custom_punches="07-11 06:55, 07-11 14:05", custom_mispunch_reason=None)
		self.assertEqual(R.reconcile(dict(rec), fixed), "replace")
		self.assertEqual(R.reconcile(dict(rec), None), "cancel")
		self.assertEqual(R.reconcile(dict(rec, custom_ot_units=0.5), rec), "replace")


if __name__ == "__main__":
	unittest.main()
