import unittest
from datetime import date

from valence.valence.retainer import rules as R


class TestRetainerRules(unittest.TestCase):
	def test_paid_days(self):
		wd = [date(2026, 11, d) for d in range(1, 8)]
		status = {wd[0]: "Present", wd[1]: "Half Day", wd[2]: "Absent", wd[3]: "On Duty", wd[5]: "Present", wd[6]: "On Leave"}
		# off: 0.5 (half) + 1 (absent) + 1 (no record, wd[4]) + 1 (leave) = 3.5
		self.assertEqual(R.paid_days(30, wd, status), 26.5)

	def test_amount(self):
		self.assertEqual(R.amount(150000, 30, 30), 150000)
		self.assertEqual(R.amount(150000, 29, 31), 140323)


if __name__ == "__main__":
	unittest.main()
