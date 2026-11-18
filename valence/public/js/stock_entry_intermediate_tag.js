// Manufacture Stock Entry of an intermediate: open (or make) its Intermediate Status Tag.
// Kept apart from stock_entry.js so the existing Stock Entry script is untouched.
// The server decides which items get a tag (item group from Labelling Settings, final stage left out).
frappe.ui.form.on("Stock Entry", {
	async refresh(frm) {
		if (frm.doc.docstatus !== 1 || frm.doc.purpose !== "Manufacture") return;
		if (!(frm.doc.items || []).some((r) => r.is_finished_item)) return;
		const r = await frappe.call({
			method: "valence.valence.labelling.intermediate_tags.tag_buttons",
			args: { stock_entry: frm.doc.name },
		});
		const { tags = [], can_make = false } = r.message || {};
		if (tags.length) {
			frm.add_custom_button(__("Intermediate Tag"), () => {
				if (tags.length === 1) frappe.set_route("Form", "Intermediate Label", tags[0]);
				else frappe.set_route("List", "Intermediate Label", { stock_entry: frm.doc.name });
			}, __("Labels"));
		} else if (can_make) {
			frm.add_custom_button(__("Make Intermediate Tag"), () => {
				frappe.new_doc("Intermediate Label", { stock_entry: frm.doc.name });
			}, __("Labels"));
		}
	},
});
