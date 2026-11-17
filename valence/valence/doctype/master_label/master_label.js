// Copyright (c) 2026, finbyz tech and contributors
// For license information, please see license.txt

// Same approval chain as the LRF: production prepares and sends for approval,
// QA (Stock Manager) approves, sends back or retires. The server repeats every check.
const valence_ml = {
	is_approver: () => frappe.user.has_role(["Stock Manager", "System Manager"]),

	act(frm, action, ask_reason) {
		const run = (reason) =>
			frappe
				.call("valence.valence.doctype.master_label.master_label.change_status", {
					name: frm.doc.name,
					action,
					reason: reason || null,
				})
				.then(() => frm.reload_doc());
		if (!ask_reason) return run();
		frappe.prompt(
			{ fieldname: "reason", fieldtype: "Small Text", label: __("Reason"), reqd: 1 },
			(v) => run(v.reason),
			ask_reason
		);
	},

	buttons(frm) {
		if (frm.is_new() || frm.is_dirty()) return;
		const s = frm.doc.status;
		const group = __("Actions");
		if (s === "Draft" && frm.perm[0]?.write) {
			frm.add_custom_button(__("Send for Approval"), () => valence_ml.act(frm, "send_for_approval"), group);
		}
		if (!valence_ml.is_approver()) return;
		if (s === "Pending") {
			frm.add_custom_button(__("Approve"), () => valence_ml.act(frm, "approve"), group);
			frm.add_custom_button(__("Send Back"), () => valence_ml.act(frm, "send_back", __("Send back to production")), group);
		}
		if (s === "Approved") {
			frm.add_custom_button(__("Retire"), () => valence_ml.act(frm, "retire", __("Retire this master label")), group);
		}
	},
};

frappe.ui.form.on("Master Label", {
	refresh(frm) {
		// Review notes from the register import sit at the bottom of the form; surface them on top.
		if (frm.doc.import_notes && ["Draft", "Pending"].includes(frm.doc.status)) {
			const items = frm.doc.import_notes
				.split("\n")
				.filter(Boolean)
				.map((n) => `<li>${frappe.utils.escape_html(n)}</li>`)
				.join("");
			frm.set_intro(`<b>${__("Check before approving")}</b><ul style="margin:4px 0 0 16px">${items}</ul>`, "orange");
		} else if (frm.doc.status === "Pending") {
			frm.set_intro(__("Waiting for QA approval."), "blue");
		} else if (frm.doc.status === "Retired") {
			frm.set_intro(__("Retired: this label no longer prints."), "red");
		}
		// approved masters are QA's to change
		if (["Approved", "Retired"].includes(frm.doc.status) && !valence_ml.is_approver()) {
			frm.disable_form();
		}
		valence_ml.buttons(frm);
	},
});
