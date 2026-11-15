frappe.query_reports["Leave Planning Score"] = {
	filters: [
		{ fieldname: "from_date", label: __("From"), fieldtype: "Date", default: frappe.datetime.month_start(frappe.datetime.add_months(frappe.datetime.get_today(), -1)) },
		{ fieldname: "to_date", label: __("To"), fieldtype: "Date", default: frappe.datetime.month_end(frappe.datetime.add_months(frappe.datetime.get_today(), -1)) },
		{ fieldname: "department", label: __("Department"), fieldtype: "Link", options: "Department" },
	],
};
