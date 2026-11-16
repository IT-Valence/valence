# Copyright (c) 2025, finbyz tech and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt, formatdate, get_url_to_form, getdate

from valence.valence.doctype.master_label.master_label import find_master_label
from valence.valence.labelling.normalize import normalize_grade, upper_text

WEIGHT_PRECISION = 3


class LabelRequisitionForm(Document):
	# Rules for drum labels live here (not in site Client Scripts) so they hold
	# however the record is saved: form, data import, API or Stock Entry hook.

	def validate(self):
		self.fill_from_batch()
		self.clean_label_text()
		self.calculate_weights()
		self.set_label_numbers()
		self.warn_on_dates()

	def before_submit(self):
		self.check_dates()
		self.check_released_qty()
		self.check_drums()
		self.check_master_label()

	def on_submit(self):
		# self.create_quality_inspection()
		# self.material_transfer_stock_entry()
		# self.update_sample_size()
		pass

	# ------------------------------------------------------------- drum labels
	def fill_from_batch(self):
		"""Fill empty header fields from the production batch (what the old Client Script did on load)."""
		if not self.production_b_no:
			return
		meta = frappe.get_meta("Batch")
		fields = ["batch_qty", "manufacturing_date", "expiry_date"]
		fields += [f for f in ("retest_date", "lot_no", "no_of_packages") if meta.has_field(f)]
		batch = frappe.db.get_value("Batch", self.production_b_no, fields, as_dict=True) or {}
		if not self.batch_size_kgs and batch.get("batch_qty"):
			self.batch_size_kgs = batch.batch_qty
		if not self.manufacturing_date and batch.get("manufacturing_date"):
			self.manufacturing_date = batch.manufacturing_date
		if not self.retestexpiry_date and (batch.get("retest_date") or batch.get("expiry_date")):
			self.retestexpiry_date = batch.get("retest_date") or batch.get("expiry_date")
		if not self.lot_no and batch.get("lot_no"):
			self.lot_no = batch.lot_no
		if not cint(self.no_of_packages) and cint(batch.get("no_of_packages")):
			self.no_of_packages = str(cint(batch.no_of_packages))

	def clean_label_text(self):
		"""Anything printed on the drum is stored trimmed and in CAPITALS."""
		if self.grade:
			grade = normalize_grade(self.grade)
			if not grade:
				frappe.throw(_("Grade {0} is not one of IP, BP, USP, EP, IH, NA.").format(frappe.bold(self.grade)))
			self.grade = grade
		self.released_b_no = upper_text(self.released_b_no)
		if flt(self.released_qty_kgs) < 0:
			frappe.throw(_("Released Qty (Kgs) cannot be negative."))
		for row in self.label_details:
			row.drum_no = upper_text(row.drum_no)
			row.seal_no = upper_text(row.seal_no)

	def calculate_weights(self):
		"""Gross = net + tare per drum, container check, and header totals."""
		tolerance = flt(frappe.db.get_single_value("Labelling Settings", "container_weight_tolerance_kg"))
		total_net = total_gross = 0
		for row in self.label_details:
			row.net_weight_kg = flt(row.net_weight_kg, WEIGHT_PRECISION)
			row.tare_weight_kg = flt(row.tare_weight_kg, WEIGHT_PRECISION)
			row.gross_weight_kg = flt(row.net_weight_kg + row.tare_weight_kg, WEIGHT_PRECISION)
			if flt(row.observed_weight_kg) and row.gross_weight_kg:
				diff = abs(flt(row.observed_weight_kg, WEIGHT_PRECISION) - row.gross_weight_kg)
				row.container_condition = "Ok" if diff <= tolerance + 1e-9 else "Not Ok"
			if self.production_item:
				row.item_code = self.production_item
			total_net += row.net_weight_kg
			total_gross += row.gross_weight_kg
		self.total_net_weight = flt(total_net, WEIGHT_PRECISION)
		self.total_gross_weight = flt(total_gross, WEIGHT_PRECISION)

	def set_label_numbers(self):
		"""Product / Identification label numbers from the approved Master Labels."""
		self.label_variant = self.label_variant or "Standard"
		product = ident = None
		if self.production_item and self.grade:
			args = (self.production_item, self.grade)
			product = find_master_label(*args, "Product", self.label_variant, self.customer)
			ident = find_master_label(*args, "Identification", self.label_variant, self.customer)
		self.product_label_no = product.name if product else None
		self.identification_label_no = ident.name if ident else None

	def _bad_dates(self):
		return (
			self.manufacturing_date
			and self.retestexpiry_date
			and getdate(self.retestexpiry_date) <= getdate(self.manufacturing_date)
		)

	def _date_message(self):
		return _("Retest/Expiry Date {0} must be after Manufacturing Date {1}.").format(
			frappe.bold(formatdate(self.retestexpiry_date)), frappe.bold(formatdate(self.manufacturing_date))
		)

	def warn_on_dates(self):
		if self._bad_dates() and not self.flags.ignore_date_warning:
			frappe.msgprint(self._date_message() + " " + _("This LRF cannot be approved until it is fixed."), indicator="orange", alert=True)

	def check_dates(self):
		if self._bad_dates():
			frappe.throw(self._date_message())

	def check_released_qty(self):
		released = flt(self.released_qty_kgs, WEIGHT_PRECISION)
		net = flt(self.total_net_weight, WEIGHT_PRECISION)
		if released != net:
			frappe.throw(
				_("Released Qty ({0} kg) must equal Total Net Weight ({1} kg).").format(released, net)
			)

	def check_drums(self):
		if not self.label_details:
			frappe.throw(_("Add one row per drum in Label Details."))
		empty = [row.idx for row in self.label_details if flt(row.net_weight_kg) <= 0]
		if empty:
			frappe.throw(_("Enter Net Weight for drum row(s) {0}.").format(", ".join(map(str, empty))))
		not_ok = [row.drum_no or str(row.idx) for row in self.label_details if row.container_condition == "Not Ok"]
		if not_ok:
			frappe.msgprint(
				_("Container condition is Not Ok for drum(s) {0}.").format(", ".join(not_ok)),
				indicator="orange",
				alert=True,
			)

	def check_master_label(self):
		if not cint(frappe.db.get_single_value("Labelling Settings", "require_master_label")):
			return
		if not self.product_label_no:
			frappe.throw(
				_("No approved Product Master Label for {0} grade {1}. Ask QA to approve it in Master Label.").format(
					frappe.bold(self.production_item), frappe.bold(self.grade or "-")
				)
			)

	# def update_sample_size(self):
		# qi_name = frappe.get_doc("Quality Inspection",{'custom_lrf_reference_name':self.name})
		# qi_name.db_set('sample_size',self.custom_sample_size)
	
	def create_quality_inspection(self):
		# Create a new Quality Inspection document
		
		qi = frappe.new_doc("Quality Inspection")
		qi.inspection_type = "Incoming"  # or "In Process" or "Final" depending on your case
		qi.reference_type = "Stock Entry"
		qi.reference_name = self.stock_entry_reference_name
		# qi.custom_lrf_reference_name = self.name
		qi.item_code = self.production_item
		qi.batch_no = self.batch_no 
		qi.sample_size = self.batch_size_kgs
		qi.lot_no = self.lot_no
		qi.ar_no = self.ar_no
		qi.expiry_date = self.retestexpiry_date
		qi.manufacturing_date = self.manufacturing_date
		qi.inspected_by = self.inspected_by
		qi.report_date = frappe.utils.nowdate()

		qi.save(ignore_permissions=True)
		frappe.db.commit()
		frappe.msgprint(f"""Quality Inspection <a href="/app/quality-inspection/{qi.name}" target="_blank">{qi.name}</a> created automatically.""")
		
	def material_transfer_stock_entry(self):
		
		ref_doc = frappe.get_doc("Stock Entry",self.stock_entry_reference_name)

		default_quality_analysis_warehouse, default_quality_inspection_warehouse = frappe.db.get_value(
		"Company", ref_doc.company, ['custom_default_quality_analysis_warehouse', 'default_quality_inspection_warehouse'])
		
		se = frappe.new_doc("Stock Entry")
		se.fg_completed_qty = 0
		se.posting_date = self.date
		se.purpose = "Material Transfer"
		se.stock_entry_type = "Material Transfer"
		se.company = ref_doc.company
		
		for row in ref_doc.items:
			if row.t_warehouse and row.is_finished_item and row.item_code == self.production_item:
				
				se.append("items", {
					'item_code': row.item_code,
					# 'quality_inspection': self.name,
					's_warehouse': default_quality_analysis_warehouse,	
					't_warehouse': default_quality_inspection_warehouse,
					'qty': flt(self.custom_sample_size),
					'batch_no': row.batch_no,
					'basic_rate': row.basic_rate,  # Stock Entry uses basic_rate
					'lot_no': row.lot_no,
					'ar_no': row.ar_no,
					'use_serial_batch_fields': 1,
				})

		se.save()
		se.submit()
		url = get_url_to_form("Stock Entry", se.name)
		# self.db_set("stock_entry",se.name)
		frappe.msgprint("New Stock Entry - <a href='{url}'>{doc}</a> created for Material Transfer".format(
			url=url, doc=frappe.bold(se.name)))

