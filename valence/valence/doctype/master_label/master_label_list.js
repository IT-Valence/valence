frappe.listview_settings["Master Label"] = {
	add_fields: ["status", "import_notes"],
	get_indicator(doc) {
		if (doc.status === "Approved") return [__("Approved"), "green", "status,=,Approved"];
		if (doc.status === "Retired") return [__("Retired"), "gray", "status,=,Retired"];
		if (doc.status === "Pending") return [__("Pending approval"), "yellow", "status,=,Pending"];
		if (doc.import_notes) return [__("Draft - review"), "orange", "status,=,Draft"];
		return [__("Draft"), "blue", "status,=,Draft"];
	},
};
