// Label Requisition Form: drum rows, weights and label numbers.
// The server repeats every calculation on save (label_requisition_form.py);
// this script only gives users instant feedback while typing.

const valence_lrf = {
	PRECISION: 3,

	fill_from_batch(frm) {
		if (frm.doc.docstatus !== 0 || !frm.doc.production_b_no || cint(frm.doc.no_of_packages)) return;
		frappe.db.get_doc("Batch", frm.doc.production_b_no).then((batch) => {
			frm.set_value("batch_size_kgs", batch.batch_qty || 0);
			frm.set_value("manufacturing_date", batch.manufacturing_date || null);
			frm.set_value("lot_no", batch.lot_no || null);
			frm.set_value("retestexpiry_date", batch.retest_date || batch.expiry_date || null);
			if (cint(batch.no_of_packages) > 0) {
				frm.set_value("no_of_packages", String(cint(batch.no_of_packages)));
			}
		});
	},

	make_drum_rows(frm, total) {
		frm.clear_table("label_details");
		const width = Math.max(2, String(total).length);
		for (let i = 1; i <= total; i++) {
			const row = frm.add_child("label_details");
			row.drum_no = `${String(i).padStart(width, "0")}/${String(total).padStart(width, "0")}`;
			row.net_weight_kg = 0;
			row.tare_weight_kg = 0;
			row.gross_weight_kg = 0;
		}
		frm.refresh_field("label_details");
		valence_lrf.totals(frm);
	},

	row_weights(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		const old_gross = flt(row.gross_weight_kg, valence_lrf.PRECISION);
		const gross = flt(flt(row.net_weight_kg) + flt(row.tare_weight_kg), valence_lrf.PRECISION);
		frappe.model.set_value(cdt, cdn, "gross_weight_kg", gross);
		// Observed Wt. starts equal to Gross and follows it until QA changes it.
		if (!flt(row.observed_weight_kg) || flt(row.observed_weight_kg, valence_lrf.PRECISION) === old_gross) {
			frappe.model.set_value(cdt, cdn, "observed_weight_kg", gross);
		}
		valence_lrf.container(cdt, cdn);
		valence_lrf.totals(frm);
	},

	container(cdt, cdn) {
		const row = locals[cdt][cdn];
		const gross = flt(row.gross_weight_kg, valence_lrf.PRECISION);
		const observed = flt(row.observed_weight_kg, valence_lrf.PRECISION);
		if (!gross || !observed) return;
		const tolerance = flt(valence_lrf.tolerance || 0);
		// Only a mismatch is flagged automatically; Ok is for QA to decide.
		if (Math.abs(observed - gross) > tolerance + 1e-9) {
			frappe.model.set_value(cdt, cdn, "container_condition", "Not Ok");
		}
	},

	totals(frm) {
		let net = 0,
			gross = 0;
		(frm.doc.label_details || []).forEach((r) => {
			net += flt(r.net_weight_kg);
			gross += flt(r.gross_weight_kg);
		});
		frm.set_value("total_net_weight", flt(net, valence_lrf.PRECISION));
		frm.set_value("total_gross_weight", flt(gross, valence_lrf.PRECISION));
	},

	upper(frm, field) {
		const v = frm.doc[field];
		if (v && typeof v === "string") {
			const clean = v.replace(/\s+/g, " ").trim().toUpperCase();
			if (clean !== v) frm.set_value(field, clean);
		}
	},

	label_numbers(frm) {
		if (frm.doc.docstatus !== 0 || !frm.doc.production_item || !frm.doc.grade) return;
		frappe
			.call("valence.valence.doctype.master_label.master_label.get_master_labels", {
				item: frm.doc.production_item,
				grade: frm.doc.grade,
				variant: frm.doc.label_variant || "Standard",
				customer: frm.doc.customer || null,
			})
			.then((r) => {
				const m = r.message || {};
				frm.set_value("product_label_no", m.product || null);
				frm.set_value("identification_label_no", m.identification || null);
			});
	},

	print_buttons(frm) {
		if (frm.doc.docstatus !== 1) return;
		const open = (format) => {
			const url =
				`/printview?doctype=${encodeURIComponent(frm.doc.doctype)}&name=${encodeURIComponent(frm.doc.name)}` +
				`&format=${encodeURIComponent(format)}&no_letterhead=1&_lang=en`;
			window.open(url, "_blank");
		};
		frm.add_custom_button(__("All Labels"), () => open("Drum Labels"), __("Print"));
		frm.add_custom_button(__("Product Labels"), () => open("Drum Product Labels"), __("Print"));
		frm.add_custom_button(__("Identification Labels"), () => open("Drum Identification Labels"), __("Print"));
	},

	// ---- QA columns on the same table. Production fills Drum No. / Net / Tare while
	// the form is a Draft; after it is sent for approval those lock and QA's columns open.
	PRODUCTION_COLS: ["drum_no", "net_weight_kg", "tare_weight_kg"],
	QA_COLS: ["observed_weight_kg", "container_condition", "seal_no", "seal_condition"],

	qa_stage(frm) {
		if (frm.doc.docstatus !== 0) return false;
		if (frm.doc.workflow_state) return frm.doc.workflow_state !== "Draft";
		return !frm.is_new() && !frappe.model.has_workflow(frm.doctype); // no approval workflow: open once saved
	},

	qa_view(frm) {
		const grid = frm.fields_dict.label_details.grid;
		const qa = valence_lrf.qa_stage(frm);
		const draft = frm.doc.docstatus === 0 && !qa;
		valence_lrf.PRODUCTION_COLS.forEach((f) => grid.update_docfield_property(f, "read_only", qa ? 1 : 0));
		valence_lrf.QA_COLS.forEach((f) => grid.update_docfield_property(f, "read_only", draft ? 1 : 0));
		frm.set_df_property("no_of_packages", "read_only", qa || frm.doc.docstatus !== 0 ? 1 : 0);
		// The drum list itself is production's: QA cannot add or remove drums.
		frm.set_df_property("label_details", "cannot_add_rows", qa ? 1 : 0);
		frm.set_df_property("label_details", "cannot_delete_rows", qa ? 1 : 0);
		frm.set_df_property(
			"label_details",
			"description",
			qa
				? __("QA: check each drum. Tick drums and press Ok / Not Ok, or press with nothing ticked to set every drum.")
				: draft
				? __("Production fills Drum No., Net and Tare. QA's columns open once the form is sent for approval.")
				: ""
		);
		grid.clear_custom_buttons?.();
		if (!qa) return grid.refresh();
		const mark = (field, value) => () => {
			const picked = grid.get_selected_children();
			const rows = picked.length ? picked : frm.doc.label_details || [];
			rows.forEach((r) => frappe.model.set_value(r.doctype, r.name, field, value));
			frappe.show_alert({
				message: __("{0} set to {1} on {2} drum(s)", [__(field === "seal_condition" ? "Seal" : "Container"), __(value), rows.length]),
				indicator: value === "Ok" ? "green" : "red",
			});
		};
		grid.add_custom_button(__("Seal: Not Ok"), mark("seal_condition", "Not Ok")).addClass("btn-danger-light");
		grid.add_custom_button(__("Seal: Ok"), mark("seal_condition", "Ok"));
		grid.add_custom_button(__("Container: Not Ok"), mark("container_condition", "Not Ok"));
		grid.add_custom_button(__("Container: Ok"), mark("container_condition", "Ok"));
		grid.refresh();
	},

	// QA types the first seal number; empty rows below continue the sequence (10575, 10576, ...).
	seal_sequence(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		const m = /^(.*?)(\d+)$/.exec((row.seal_no || "").trim().toUpperCase());
		if (!m) return;
		const [, prefix, digits] = m;
		let n = parseInt(digits, 10);
		(frm.doc.label_details || [])
			.filter((r) => r.idx > row.idx)
			.sort((a, b) => a.idx - b.idx)
			.forEach((r) => {
				if (r.seal_no) return;
				n += 1;
				frappe.model.set_value(r.doctype, r.name, "seal_no", prefix + String(n).padStart(digits.length, "0"));
			});
	},

	show_label_status(frm) {
		// Sent back by QA: production sees why at the top of the form.
		if (frm.doc.docstatus === 0 && frm.doc.workflow_state === "Draft" && (frm.doc.remarks_qa || "").trim()) {
			frm.dashboard.add_comment(
				__("QA remarks: {0}", [frappe.utils.escape_html(frm.doc.remarks_qa)]),
				"red",
				true
			);
		}
		if (frm.is_new() || !frm.doc.grade) return;
		if (!frm.doc.product_label_no) {
			frm.dashboard.add_comment(
				__("No approved Master Label for {0} grade {1} yet. Labels will print without a Label No.", [
					frm.doc.production_item,
					frm.doc.grade,
				]),
				"orange",
				true
			);
		}
	},
};

frappe.ui.form.on("Label Requisition Form", {
	setup(frm) {
		frappe.db.get_single_value("Labelling Settings", "container_weight_tolerance_kg").then((v) => {
			valence_lrf.tolerance = flt(v);
		});
	},

	onload_post_render(frm) {
		valence_lrf.fill_from_batch(frm);
		// Forms made by the Stock Entry hook already know No. of Containers but have no rows yet.
		const total = cint(frm.doc.no_of_packages);
		if (frm.doc.docstatus === 0 && !valence_lrf.qa_stage(frm) && total > 0 && !(frm.doc.label_details || []).length) {
			valence_lrf.make_drum_rows(frm, total);
		}
		if (flt(frm.doc.released_qty_kgs) < 0) frm.set_value("released_qty_kgs", 0);
	},

	refresh(frm) {
		valence_lrf.show_label_status(frm);
		valence_lrf.print_buttons(frm);
		valence_lrf.qa_view(frm);
	},

	production_b_no(frm) {
		valence_lrf.fill_from_batch(frm);
	},

	no_of_packages(frm) {
		const total = cint(frm.doc.no_of_packages);
		if (frm.doc.docstatus !== 0 || total <= 0) return;
		if ((frm.doc.label_details || []).length) {
			frappe.confirm(
				__("Changing the number of containers will reset the existing label details. Do you want to continue?"),
				() => valence_lrf.make_drum_rows(frm, total),
				() => frm.reload_doc()
			);
		} else {
			valence_lrf.make_drum_rows(frm, total);
		}
	},

	grade(frm) {
		valence_lrf.label_numbers(frm);
	},
	production_item(frm) {
		valence_lrf.label_numbers(frm);
	},
	label_variant(frm) {
		valence_lrf.label_numbers(frm);
	},
	customer(frm) {
		valence_lrf.label_numbers(frm);
	},

	released_b_no(frm) {
		valence_lrf.upper(frm, "released_b_no");
	},

	released_qty_kgs(frm) {
		if (flt(frm.doc.released_qty_kgs) < 0) {
			frm.set_value("released_qty_kgs", 0);
			frappe.msgprint(__("Released Qty (kgs) was negative and has been reset to 0."));
		}
	},

	label_details_remove(frm) {
		valence_lrf.totals(frm);
	},
});

frappe.ui.form.on("Label Requisition Form Item", {
	net_weight_kg: (frm, cdt, cdn) => valence_lrf.row_weights(frm, cdt, cdn),
	tare_weight_kg: (frm, cdt, cdn) => valence_lrf.row_weights(frm, cdt, cdn),
	observed_weight_kg: (frm, cdt, cdn) => valence_lrf.container(cdt, cdn),
	seal_no: (frm, cdt, cdn) => valence_lrf.seal_sequence(frm, cdt, cdn),
});

