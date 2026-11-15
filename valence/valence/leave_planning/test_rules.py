import unittest
from datetime import date

from valence.valence.leave_planning import rules as R

EL = dict(notice_days=7, allow_after=False, grace_days=0)
CL = dict(notice_days=1, allow_after=True, grace_days=2)
SL = dict(notice_days=0, allow_after=True, grace_days=2)


def d(n):
	return date(2026, 11, n)


class TestLeaveRules(unittest.TestCase):
	def test_el_needs_seven_days(self):
		self.assertEqual(R.classify(d(1), d(8), d(10), **EL), R.IN_ADVANCE)
		self.assertEqual(R.classify(d(3), d(8), d(10), **EL), R.LATE)
		self.assertEqual(R.classify(d(12), d(8), d(10), **EL), R.LATE)

	def test_cl_before_day_or_two_days_after(self):
		self.assertEqual(R.classify(d(7), d(8), d(8), **CL), R.IN_ADVANCE)
		self.assertEqual(R.classify(d(8), d(8), d(8), **CL), R.ON_TIME_AFTER)
		self.assertEqual(R.classify(d(10), d(8), d(8), **CL), R.ON_TIME_AFTER)
		self.assertEqual(R.classify(d(11), d(8), d(8), **CL), R.LATE)

	def test_sl_within_two_days_of_return(self):
		self.assertEqual(R.classify(d(8), d(8), d(9), **SL), R.IN_ADVANCE)
		self.assertEqual(R.classify(d(11), d(8), d(9), **SL), R.ON_TIME_AFTER)
		self.assertEqual(R.classify(d(12), d(8), d(9), **SL), R.LATE)

	def test_hod_deadline(self):
		self.assertEqual(R.hod_deadline(d(1), d(20), 3), d(4))  # well ahead: 3 days
		self.assertEqual(R.hod_deadline(d(1), d(2), 3), d(2))  # leave starts sooner
		self.assertEqual(R.hod_deadline(d(10), d(8), 3), d(13))  # after the fact
		self.assertEqual(R.super_hod_deadline(d(1), d(20), 3), d(7))


if __name__ == "__main__":
	unittest.main()
