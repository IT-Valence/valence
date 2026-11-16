// Copyright (c) 2026, finbyz tech and contributors
// For license information, please see license.txt

frappe.ui.form.on("Master Label", {
	refresh(frm) {
		// Review notes from the register import sit at the bottom of the form; surface them on top.
		if (frm.doc.import_notes && frm.doc.status === "Draft") {
			const items = frm.doc.import_notes
				.split("\n")
				.filter(Boolean)
				.map((n) => `<li>${frappe.utils.escape_html(n)}</li>`)
				.join("");
			frm.set_intro(`<b>${__("Check before approving")}</b><ul style="margin:4px 0 0 16px">${items}</ul>`, "orange");
		} else if (frm.doc.status === "Retired") {
			frm.set_intro(__("Retired: this label no longer prints."), "red");
		}
	},
});
