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
		self.lock_production_columns()
		self.warn_on_dates()

	def before_submit(self):
		self.check_dates()
		self.check_released_qty()
		self.check_drums()
		self.check_qa()
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

	def labels_per_drum(self):
		s = frappe.get_cached_doc("Labelling Settings")
		return cint(s.product_labels_per_drum or 1) + cint(s.identification_labels_per_drum or 1)

	def calculate_weights(self):
		"""Gross = net + tare per drum, QA's observed weight, and header totals.

		Observed Weight starts equal to Gross (while production is still filling the
		form it simply follows Gross). QA may change it; if it no longer matches Gross
		the container is marked Not Ok. Ok is never set automatically: QA decides.
		"""
		tolerance = flt(frappe.db.get_single_value("Labelling Settings", "container_weight_tolerance_kg"))
		total_net = total_gross = 0
		per_drum = self.labels_per_drum()
		with_qa = self.past_draft()
		for row in self.label_details:
			if not cint(row.no_of_labels_required):
				row.no_of_labels_required = per_drum
			row.net_weight_kg = flt(row.net_weight_kg, WEIGHT_PRECISION)
			row.tare_weight_kg = flt(row.tare_weight_kg, WEIGHT_PRECISION)
			row.gross_weight_kg = flt(row.net_weight_kg + row.tare_weight_kg, WEIGHT_PRECISION)
			if not with_qa or not flt(row.observed_weight_kg):
				row.observed_weight_kg = row.gross_weight_kg
			row.observed_weight_kg = flt(row.observed_weight_kg, WEIGHT_PRECISION)
			if row.gross_weight_kg and abs(row.observed_weight_kg - row.gross_weight_kg) > tolerance + 1e-9:
				row.container_condition = "Not Ok"
			if self.production_item:
				row.item_code = self.production_item
			total_net += row.net_weight_kg
			total_gross += row.gross_weight_kg
		self.total_net_weight = flt(total_net, WEIGHT_PRECISION)
		self.total_gross_weight = flt(total_gross, WEIGHT_PRECISION)

	# ---------------------------------------------------------- QA checks
	# One table, as before. Production fills Drum No. / Net / Tare (Gross is worked out)
	# while the form is a Draft. Once it is sent for approval those columns lock and QA
	# fills its columns on the same rows: Observed Wt. (starts equal to Gross), Container,
	# Seal No. and Seal. Final approval needs QA's columns complete.

	def past_draft(self):
		"""True once the approval workflow has moved the form past Draft."""
		state = self.get("workflow_state")
		return bool(state) and state != "Draft"

	def lock_production_columns(self):
		before = self.get_doc_before_save()
		if not before or not self.past_draft() or not before.get("workflow_state") or before.workflow_state == "Draft":
			return

		def rows(doc):
			return [
				(r.drum_no, flt(r.net_weight_kg, WEIGHT_PRECISION), flt(r.tare_weight_kg, WEIGHT_PRECISION))
				for r in doc.get("label_details") or []
			]

		if rows(before) != rows(self):
			frappe.throw(
				_("Drum No., Net and Tare Weight are production's entries and are locked once the form is sent for approval."),
				title=_("Production entries are locked"),
			)

	def check_qa(self):
		rows = self.label_details
		missing = {}
		for r in rows:
			gaps = [
				label
				for label, ok in (
					(_("Observed Wt."), flt(r.observed_weight_kg) > 0),
					(_("Container"), bool(r.container_condition)),
					(_("Seal No."), bool(r.seal_no)),
					(_("Seal"), bool(r.seal_condition)),
				)
				if not ok
			]
			if gaps:
				missing[r.drum_no or str(r.idx)] = gaps
		if missing:
			frappe.throw(
				_("QA has not finished these drums:")
				+ "<br>"
				+ "<br>".join(f"{drum}: {', '.join(gaps)}" for drum, gaps in missing.items()),
				title=_("QA check incomplete"),
			)
		seals = [r.seal_no for r in rows]
		dupes = sorted({s for s in seals if seals.count(s) > 1})
		if dupes:
			frappe.throw(_("Seal No. used on more than one drum: {0}.").format(", ".join(dupes)))
		not_ok = [r.drum_no or str(r.idx) for r in rows if "Not Ok" in (r.container_condition, r.seal_condition)]
		if not_ok and not (self.remarks_qa or "").strip():
			frappe.throw(
				_("Drum(s) {0} are marked Not Ok. Write the reason in Remarks QA before approving.").format(", ".join(not_ok))
			)

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

