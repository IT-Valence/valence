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
		if self.sent_back():
			self.reopen_for_production()
		self.make_drum_rows()
		self.clean_label_text()
		self.calculate_weights()
		self.set_label_numbers()
		self.lock_production_columns()
		if self.leaving_draft():
			self.check_production_complete()
		if self.entering_state("Sealed"):
			self.check_seals()
		self.warn_on_dates()
		self.warn_other_lrfs()

	def before_submit(self):
		self.check_production_complete()
		self.check_label_header()
		self.check_qa()
		self.check_master_label()

	def on_update(self):
		if self.flags.sent_back_reason:
			self.add_comment(
				"Comment", _("Sent back to production by QA. Reason: {0}").format(self.flags.sent_back_reason)
			)

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

	def make_drum_rows(self):
		"""One row per container (01/05, 02/05, ...) when the form has none yet.

		Covers LRFs made by the Stock Entry hook, data import or API: the batch gives
		No. of Containers but nobody opened the form to trigger the screen's row maker.
		"""
		total = cint(self.no_of_packages)
		if self.docstatus != 0 or self.past_draft() or self.label_details or total <= 0:
			return
		width = max(2, len(str(total)))
		for i in range(1, total + 1):
			self.append("label_details", {"drum_no": f"{i:0{width}d}/{total:0{width}d}"})

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

	def leaving_draft(self):
		"""True on the save that sends the form from Draft to QA (first Approve)."""
		if not self.past_draft():
			return False
		before = self.get_doc_before_save()
		return not before or before.get("workflow_state") in (None, "", "Draft")

	def sent_back(self):
		"""True on the save where QA sends the form back to Draft ("Send Back")."""
		if self.past_draft() or not self.get("workflow_state"):
			return False
		before = self.get_doc_before_save()
		return bool(before) and before.get("workflow_state") not in (None, "", "Draft")

	def reopen_for_production(self):
		"""Production may correct its entries again; QA re-checks the containers afterwards."""
		reason = (self.remarks_qa or "").strip()
		if not reason:
			frappe.throw(
				_("Write what production must correct in Remarks QA, save, then press Send Back."),
				title=_("Reason needed"),
			)
		for row in self.label_details:
			row.container_condition = None  # weights may change: QA checks again
		self.flags.sent_back_reason = reason

	def entering_state(self, state):
		"""True on the save that moves the workflow into `state`."""
		if self.get("workflow_state") != state:
			return False
		before = self.get_doc_before_save()
		return not before or before.get("workflow_state") != state

	def check_seals(self):
		"""A form cannot be marked Sealed while drums have no seal recorded."""
		missing = [r.drum_no or str(r.idx) for r in self.label_details if not r.seal_no or not r.seal_condition]
		if missing:
			frappe.throw(
				_("Enter Seal No. and Seal (Ok / Not Ok) for drum(s) {0} before marking the form Sealed.").format(
					", ".join(missing)
				),
				title=_("Seals not recorded"),
			)

	def check_production_complete(self):
		"""Everything production owes before QA gets the form (and again at final approval).

		Production's columns lock once the form leaves Draft and the workflow has no
		"send back" step, so anything missing here could not be fixed later.
		"""
		problems = []
		if not self.manufacturing_date:
			problems.append(_("Manufacturing Date is empty."))
		if not self.retestexpiry_date:
			problems.append(_("Retest/Expiry Date is empty."))
		elif self._bad_dates():
			problems.append(self._date_message())
		rows = self.label_details
		if not rows:
			problems.append(_("Label Details has no drums."))
		zero = [r.drum_no or str(r.idx) for r in rows if flt(r.net_weight_kg) <= 0]
		if zero:
			problems.append(_("Net Weight is missing for drum(s) {0}.").format(", ".join(zero)))
		containers = cint(self.no_of_packages)
		if containers and rows and containers != len(rows):
			problems.append(
				_("No. of Containers is {0} but Label Details has {1} drums.").format(containers, len(rows))
			)
		released, net = flt(self.released_qty_kgs, WEIGHT_PRECISION), flt(self.total_net_weight, WEIGHT_PRECISION)
		if rows and released != net:
			problems.append(_("Released Qty ({0} kg) must equal Total Net Weight ({1} kg).").format(released, net))
		if problems:
			frappe.throw(
				_("Production has not finished this form:") + "<br>• " + "<br>• ".join(problems),
				title=_("Cannot send for approval"),
			)
		if flt(self.batch_size_kgs) and net > flt(self.batch_size_kgs) + 1e-9:
			frappe.msgprint(
				_("Total Net Weight {0} kg is more than the batch size {1} kg.").format(net, flt(self.batch_size_kgs)),
				indicator="orange",
				alert=True,
			)

	def check_label_header(self):
		"""Grade and the label batch number print on every sticker.

		On the live site they are usually filled while the form is with QA (release
		number, e.g. PHP006426), so they are required at final approval, not earlier.
		"""
		missing = []
		if not self.grade:
			missing.append(_("Grade"))
		if not self.released_b_no:
			missing.append(_("Batch No. (on labels)"))
		if missing:
			frappe.throw(
				_("{0} must be filled before final approval: printed on every drum sticker.").format(
					_(" and ").join(missing)
				),
				title=_("Label details missing"),
			)

	def warn_other_lrfs(self):
		if not self.production_b_no or self.flags.in_test:
			return
		others = frappe.get_all(
			"Label Requisition Form",
			filters={"production_b_no": self.production_b_no, "docstatus": ["<", 2], "name": ["!=", self.name or ""]},
			pluck="name",
		)
		if others:
			frappe.msgprint(
				_("Batch {0} already has LRF {1}. Check this is not a duplicate.").format(
					self.production_b_no, ", ".join(others)
				),
				indicator="orange",
				alert=True,
			)

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
			# numbers come from QA's register even while a master is still in review (Draft);
			# such labels print watermarked until QA approves the master
			product = find_master_label(*args, "Product", self.label_variant, self.customer, include_drafts=True)
			ident = find_master_label(*args, "Identification", self.label_variant, self.customer, include_drafts=True)
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

