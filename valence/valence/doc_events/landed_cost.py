"""Import freight / clearing bills into stock cost (Landed Cost Voucher from the bill).

Settings (Accounts Settings, blank = feature off):
  custom_landed_cost_account_1 – charge account that needs a goods receipt (e.g. Import Expenses)
  custom_landed_cost_account_2 – optional second account (e.g. Freight inward)

Flow:
  A Purchase Invoice line booked to one of these accounts must name the goods receipt it belongs to
  (Purchase Invoice Item → custom_landed_cost_receipt), unless the line is ticked
  custom_landed_cost_not_applicable (samples, services, freight on goods not received through a receipt).
  On submit, one Landed Cost Voucher per receipt is created and submitted, with the lines' amount (company
  currency, net of tax) as a charge on the same account, spread over the receipt's items by amount. The charge
  account is credited and the receipt's stock (or the asset, for a capital receipt) is debited, exactly like the
  customs-duty vouchers. Debit notes create a negative voucher. Cancelling the bill cancels its vouchers.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt

SETTINGS_FIELDS = ("custom_landed_cost_account_1", "custom_landed_cost_account_2")


def get_charge_accounts():
	meta = frappe.get_meta("Accounts Settings")
	if not meta.has_field(SETTINGS_FIELDS[0]):
		return set()
	values = frappe.db.get_value("Accounts Settings", "Accounts Settings", list(SETTINGS_FIELDS), as_dict=True) or {}
	return {values.get(f) for f in SETTINGS_FIELDS if values.get(f)}


def _needs_receipt(row, accounts):
	return row.get("expense_account") in accounts and not cint(row.get("custom_landed_cost_not_applicable"))


def _lines_by_receipt(doc, accounts):
	grouped = {}
	for row in doc.get("items"):
		if _needs_receipt(row, accounts) and row.get("custom_landed_cost_receipt"):
			grouped.setdefault(row.custom_landed_cost_receipt, []).append(row)
	return grouped


def purchase_invoice_validate(doc, method=None):
	accounts = get_charge_accounts()
	if not accounts:
		return
	missing = []
	for row in doc.get("items"):
		if not _needs_receipt(row, accounts):
			if row.get("custom_landed_cost_receipt") and row.get("expense_account") not in accounts:
				row.custom_landed_cost_receipt = None
			continue
		if not row.get("custom_landed_cost_receipt"):
			missing.append(row.idx)
			continue
		docstatus, company = frappe.db.get_value(
			"Purchase Receipt", row.custom_landed_cost_receipt, ["docstatus", "company"]
		) or (None, None)
		if docstatus != 1:
			frappe.throw(
				_("Row {0}: Goods Receipt {1} must be submitted.").format(row.idx, frappe.bold(row.custom_landed_cost_receipt))
			)
		if company != doc.company:
			frappe.throw(
				_("Row {0}: Goods Receipt {1} belongs to another company.").format(
					row.idx, frappe.bold(row.custom_landed_cost_receipt)
				)
			)
	if missing:
		frappe.throw(
			_(
				"Row(s) {0}: this charge goes into stock cost. Select the Goods Receipt (Purchase Receipt) it belongs to, "
				"or tick 'No Goods Receipt (keep as expense)' if it is not for received material."
			).format(", ".join(str(i) for i in missing)),
			title=_("Goods Receipt needed"),
		)


def purchase_invoice_on_submit(doc, method=None):
	accounts = get_charge_accounts()
	if not accounts:
		return
	for receipt, rows in _lines_by_receipt(doc, accounts).items():
		_make_voucher(doc, receipt, rows)


def _make_voucher(doc, receipt, rows):
	pr = frappe.db.get_value("Purchase Receipt", receipt, ["supplier", "posting_date", "base_grand_total"], as_dict=True)
	lcv = frappe.new_doc("Landed Cost Voucher")
	lcv.company = doc.company
	lcv.posting_date = doc.posting_date
	lcv.distribute_charges_based_on = "Amount"
	lcv.custom_purchase_invoice = doc.name
	lcv.append(
		"purchase_receipts",
		{
			"receipt_document_type": "Purchase Receipt",
			"receipt_document": receipt,
			"supplier": pr.supplier,
			"posting_date": pr.posting_date,
			"grand_total": pr.base_grand_total,
		},
	)
	lcv.get_items_from_purchase_receipts()
	charges = {}
	for row in rows:
		charges[row.expense_account] = charges.get(row.expense_account, 0) + flt(row.base_net_amount)
	for account, amount in charges.items():
		lcv.append(
			"taxes",
			{
				"expense_account": account,
				"description": _("{0} {1} ({2})").format(doc.name, doc.supplier_name or doc.supplier, doc.bill_no or ""),
				"amount": flt(amount, 2),
				"base_amount": flt(amount, 2),
				"exchange_rate": 1,
			},
		)
	lcv.flags.ignore_permissions = True
	lcv.insert()
	lcv.submit()
	frappe.msgprint(
		_("Landed Cost Voucher {0} added {1} to the stock cost of {2}.").format(
			frappe.bold(lcv.name), frappe.format_value(lcv.total_taxes_and_charges, {"fieldtype": "Currency"}), receipt
		),
		alert=True,
	)
	return lcv


def purchase_invoice_on_cancel(doc, method=None):
	for name in frappe.get_all(
		"Landed Cost Voucher", filters={"custom_purchase_invoice": doc.name, "docstatus": 1}, pluck="name"
	):
		lcv = frappe.get_doc("Landed Cost Voucher", name)
		lcv.flags.ignore_permissions = True
		lcv.cancel()


def purchase_invoice_before_cancel(doc, method=None):
	# our vouchers are cancelled in on_cancel; don't let the back-link check stop the bill's cancel
	ignored = list(doc.get("ignore_linked_doctypes") or [])
	if "Landed Cost Voucher" not in ignored:
		doc.ignore_linked_doctypes = tuple(ignored + ["Landed Cost Voucher"])
