frappe.ui.form.on("Weekly Off Upload", {
	refresh(frm) {
		frm.set_intro(
			__("Attach the Excel (columns: Employee, Weekly Off Date), save to check it, then submit to add the weekly offs.")
		);
	},
});
