// Manufacture Stock Entry of an intermediate: open (or make) its Intermediate Status Tag.
// Kept apart from stock_entry.js so the existing Stock Entry script is untouched.
frappe.ui.form.on("Stock Entry", {
	async refresh(frm) {
		if (frm.doc.docstatus !== 1 || frm.doc.purpose !== "Manufacture") return;
		const finished = (frm.doc.items || []).filter((r) => r.is_finished_item);
		if (!finished.length) return;
		const tags = await frappe.db.get_list("Intermediate Label", {
			filters: { stock_entry: frm.doc.name }, fields: ["name"], limit: 5,
		});
		if (tags.length) {
			frm.add_custom_button(__("Intermediate Tag"), () => {
				if (tags.length === 1) frappe.set_route("Form", "Intermediate Label", tags[0].name);
				else frappe.set_route("List", "Intermediate Label", { stock_entry: frm.doc.name });
			}, __("Labels"));
			return;
		}
		const groups = await Promise.all(finished.map((r) => frappe.db.get_value("Item", r.item_code, "item_group")));
		if (!groups.some((g) => g.message && g.message.item_group === "Intermediates")) return;
		frm.add_custom_button(__("Make Intermediate Tag"), () => {
			frappe.new_doc("Intermediate Label", { stock_entry: frm.doc.name });
		}, __("Labels"));
	},
});
