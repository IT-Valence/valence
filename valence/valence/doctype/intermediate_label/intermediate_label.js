// Copyright (c) 2026, finbyz tech and contributors
// For license information, please see license.txt
//
// Intermediate Status Tag: the drums are worked out from Total Qty and Drum Size
// (412 kg in 25 kg drums = 16 x 25 kg + 1 x 12 kg); everything stays editable.

const IL_METHODS = "valence.valence.labelling.intermediate_tags";

frappe.ui.form.on("Intermediate Label", {
	setup(frm) {
		frm.set_query("stock_entry", () => ({ filters: { purpose: "Manufacture", docstatus: 1 } }));
		// item group from Labelling Settings, final-stage items left out (they get an LRF)
		frm.__il_item_filters = { item_group: "Intermediates", custom_is_final_stage: 0 };
		frappe.call(`${IL_METHODS}.item_query_filters`).then((r) => r.message && (frm.__il_item_filters = r.message));
		frm.set_query("item_code", () => ({ filters: frm.__il_item_filters }));
		frm.set_query("batch_no", () => (frm.doc.item_code ? { filters: { item: frm.doc.item_code } } : {}));
	},

	refresh(frm) {
		if (!frm.is_new()) {
			frm.add_custom_button(__("Print Tags"), () => frm.print_doc()).addClass("btn-primary");
		}
		il_remember_split(frm);
		il_remember_basis(frm);
		il_show_check(frm);
		// Opened from the Stock Entry's "Make Intermediate Tag" button: fetch its values.
		if (frm.is_new() && frm.doc.stock_entry && !frm.doc.item_code) frm.trigger("stock_entry");
	},

	async stock_entry(frm) {
		if (!frm.doc.stock_entry) return;
		const values = await frappe.xcall(`${IL_METHODS}.values_from_stock_entry`, { stock_entry: frm.doc.stock_entry });
		await il_apply(frm, values);
	},

	async batch_no(frm) {
		if (!frm.doc.batch_no || frm.__il_applying) return;
		const values = await frappe.xcall(`${IL_METHODS}.values_from_batch`, { batch_no: frm.doc.batch_no });
		delete values.batch_no;
		await il_apply(frm, values);
	},

	total_qty(frm) { il_resplit(frm); },
	drum_size(frm) { il_resplit(frm); },
	split_drums(frm) { il_resplit(frm, true); },

	tare_per_drum(frm) {
		const was_split = il_untouched(frm);
		(frm.doc.containers || []).forEach((row) => {
			row.tare_weight = flt(frm.doc.tare_per_drum, 3);
			row.gross_weight = flt(flt(row.net_weight) + row.tare_weight, 3);
		});
		if (was_split) frm.__il_sig = il_signature(frm);
		frm.refresh_field("containers");
		il_totals(frm);
	},

	containers_remove(frm) { il_totals(frm); },
});

frappe.ui.form.on("Intermediate Label Container", {
	net_weight(frm, cdt, cdn) { il_row_gross(frm, cdt, cdn); },
	tare_weight(frm, cdt, cdn) { il_row_gross(frm, cdt, cdn); },
	gross_weight(frm, cdt, cdn) {
		// Typing what the scale shows: Net = Gross - Tare.
		const row = locals[cdt][cdn];
		const net = flt(flt(row.gross_weight) - flt(row.tare_weight), 3);
		if (net !== flt(row.net_weight, 3)) frappe.model.set_value(cdt, cdn, "net_weight", net);
	},
	containers_add(frm, cdt, cdn) {
		frappe.model.set_value(cdt, cdn, "tare_weight", flt(frm.doc.tare_per_drum, 3));
	},
});

function il_row_gross(frm, cdt, cdn) {
	const row = locals[cdt][cdn];
	const gross = flt(flt(row.net_weight) + flt(row.tare_weight), 3);
	if (gross !== flt(row.gross_weight, 3)) frappe.model.set_value(cdt, cdn, "gross_weight", gross);
	il_totals(frm);
}

async function il_apply(frm, values) {
	frm.__il_applying = true;
	try {
		for (const [field, value] of Object.entries(values)) {
			if (frm.fields_dict[field] && value !== undefined && value !== null) await frm.set_value(field, value);
		}
	} finally {
		frm.__il_applying = false;
	}
	il_resplit(frm);
}

// The drums exactly as the last split made them, to tell whether anyone edited them since.
function il_signature(frm) {
	return JSON.stringify((frm.doc.containers || []).map((r) => [flt(r.net_weight, 3), flt(r.tare_weight, 3)]));
}

function il_remember_split(frm) {
	// A saved record opened again: its drums count as a split if all but the last are full.
	const rows = frm.doc.containers || [];
	const size = flt(frm.doc.drum_size, 3);
	const looks_split = rows.every((r, i) =>
		flt(r.tare_weight, 3) === flt(frm.doc.tare_per_drum, 3) && (i === rows.length - 1 || flt(r.net_weight, 3) === size));
	frm.__il_sig = looks_split ? il_signature(frm) : null;
}

function il_untouched(frm) {
	return frm.__il_sig !== undefined && frm.__il_sig === il_signature(frm);
}

async function il_resplit(frm, asked) {
	if (frm.__il_applying) return;
	const total = flt(frm.doc.total_qty), size = flt(frm.doc.drum_size);
	if (total <= 0 || size <= 0) return;
	const rows = frm.doc.containers || [];
	const go = async () => {
		const drums = await frappe.xcall(`${IL_METHODS}.split_into_drums`, {
			total_qty: total, drum_size: size, tare: flt(frm.doc.tare_per_drum),
		});
		frm.clear_table("containers");
		drums.forEach((d) => frm.add_child("containers", d));
		frm.__il_sig = il_signature(frm);
		il_remember_basis(frm);
		frm.refresh_field("containers");
		il_totals(frm);
	};
	if (!rows.length || (!asked && il_untouched(frm))) return go();
	frappe.confirm(
		__("Replace the {0} drums below with {1} kg split into {2} kg drums? Weights typed by hand will be lost.", [rows.length, total, size]),
		go,
		// "No": keep the drums and put Total Qty / Drum Size back to what they were split from.
		() => il_restore_basis(frm)
	);
}

function il_remember_basis(frm) {
	frm.__il_basis = { total_qty: frm.doc.total_qty, drum_size: frm.doc.drum_size };
}

async function il_restore_basis(frm) {
	if (!frm.__il_basis) return;
	frm.__il_applying = true;
	try {
		for (const [field, value] of Object.entries(frm.__il_basis)) {
			if (flt(frm.doc[field]) !== flt(value)) await frm.set_value(field, value);
		}
	} finally {
		frm.__il_applying = false;
	}
	il_show_check(frm);
}

function il_totals(frm) {
	const rows = frm.doc.containers || [];
	rows.forEach((r, i) => (r.container_no = i + 1));
	frm.doc.no_of_containers = rows.length;
	frm.doc.net_total = flt(rows.reduce((sum, r) => sum + flt(r.net_weight), 0), 3);
	frm.refresh_field("containers");
	frm.refresh_fields(["no_of_containers", "net_total"]);
	il_show_check(frm);
}

function il_show_check(frm) {
	frm.dashboard.clear_headline();
	const rows = frm.doc.containers || [];
	if (!rows.length || !flt(frm.doc.total_qty)) return;
	const diff = flt(flt(frm.doc.net_total) - flt(frm.doc.total_qty), 3);
	if (Math.abs(diff) > 0.0005) {
		frm.dashboard.set_headline_alert(
			__("The drums add up to {0} kg, the batch Total Qty is {1} kg ({2} kg difference).", [
				flt(frm.doc.net_total, 3), flt(frm.doc.total_qty, 3), diff,
			]),
			"orange"
		);
	} else {
		const last = rows[rows.length - 1];
		frm.dashboard.set_headline_alert(
			__("{0} drums, {1} kg in total{2}.", [
				rows.length, flt(frm.doc.total_qty, 3),
				rows.length > 1 && flt(last.net_weight) !== flt(frm.doc.drum_size) ? __(" (last drum {0} kg)", [flt(last.net_weight, 3)]) : "",
			]),
			"green"
		);
	}
}
