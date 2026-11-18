# Copyright (c) 2026, finbyz tech and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from valence.valence.labelling.intermediate_tags import (
	_batch_values,
	_item_values,
	split_into_drums,
	tag_settings,
)


class IntermediateLabel(Document):
	def validate(self):
		self.fill_missing()
		if not self.containers:
			self.make_drums()
		self.calculate_weights()
		self.warn_if_drums_do_not_add_up()

	def fill_missing(self):
		"""Only fills what is empty: every fetched value can be changed by hand."""
		fetched = _batch_values(self.batch_no)
		if not self.item_code and fetched.get("item_code"):
			self.item_code = fetched["item_code"]
		fetched.update(_item_values(self.item_code))
		for field in ("product_name", "stage", "mfg_date", "retest_date"):
			if not self.get(field) and fetched.get(field):
				self.set(field, fetched[field])
		if not flt(self.drum_size):
			self.drum_size = flt(tag_settings()["default_drum_size_kg"])

	def make_drums(self):
		self.set("containers", [])
		for row in split_into_drums(self.total_qty, self.drum_size, self.tare_per_drum):
			self.append("containers", row)

	def calculate_weights(self):
		for i, row in enumerate(self.containers, start=1):
			row.container_no = i
			row.net_weight = flt(row.net_weight, 3)
			row.tare_weight = flt(row.tare_weight, 3)
			row.gross_weight = flt(row.net_weight + row.tare_weight, 3)
			if row.net_weight <= 0:
				frappe.throw(_("Drum {0}: Net Wt. must be more than 0. Remove the drum if it is not used.").format(i))
		self.no_of_containers = len(self.containers)
		self.net_total = flt(sum(r.net_weight for r in self.containers), 3)

	def warn_if_drums_do_not_add_up(self):
		if self.containers and abs(self.net_total - flt(self.total_qty, 3)) > 0.0005:
			frappe.msgprint(
				_("The drums add up to {0} kg but the batch Total Qty is {1} kg. Check the Net Wt. of the drums.").format(
					f"{self.net_total:.3f}", f"{flt(self.total_qty, 3):.3f}"
				),
				title=_("Drums do not add up"),
				indicator="orange",
			)
