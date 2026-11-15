frappe.query_reports["Leave Plan"] = {
	filters: [
		{ fieldname: "from_date", label: __("From"), fieldtype: "Date", default: frappe.datetime.get_today(), reqd: 1 },
		{ fieldname: "days", label: __("Days"), fieldtype: "Int", default: 14 },
		{ fieldname: "department", label: __("Department"), fieldtype: "Link", options: "Department" },
		{ fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company" },
		{ fieldname: "alert_percent", label: __("Alert above % away"), fieldtype: "Float", default: 15 },
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "alert" && data && data.alert) value = `<span style="color: var(--red-600); font-weight: 600">${value}</span>`;
		return value;
	},
};
