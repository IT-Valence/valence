"""LRF workflow: let QA send a form back to production.

Adds a "Send Back" action to the active Label Requisition Form workflow, from every
state between Draft and the submitted (approved) state, back to Draft, for the role
that gives final approval (QA). The form itself asks for the reason
in Remarks QA (label_requisition_form.py). Existing transitions are not touched.
Idempotent; removing the added transitions in the Workflow is the undo.
"""
import frappe

ACTION = "Send Back"


def execute():
	name = frappe.db.get_value("Workflow", {"document_type": "Label Requisition Form", "is_active": 1})
	if not name:
		return
	wf = frappe.get_doc("Workflow", name)
	draft = next((s.state for s in wf.states if str(s.doc_status) == "0"), None)  # first saved state
	if not draft:
		return
	if not frappe.db.exists("Workflow Action Master", ACTION):
		frappe.get_doc(doctype="Workflow Action Master", workflow_action_name=ACTION).insert(ignore_permissions=True)

	# Sending back is QA's call: the role that gives final approval (on Valence's
	# sites: Stock Manager, the same role as their own "Reject").
	submitted = {s.state for s in wf.states if str(s.doc_status) == "1"}
	final = next((t for t in wf.transitions if t.next_state in submitted), None)
	approver = final.allowed if final else "System Manager"

	in_progress = [s.state for s in wf.states if str(s.doc_status) == "0" and s.state != draft]
	changed = False
	for row in wf.transitions:
		if row.action == ACTION and row.allowed != approver:
			row.allowed = approver
			changed = True
	existing = {(t.state, t.action) for t in wf.transitions}
	for state in in_progress:
		if (state, ACTION) in existing:
			continue
		wf.append(
			"transitions",
			{"state": state, "action": ACTION, "next_state": draft, "allowed": approver, "allow_self_approval": 1},
		)
		changed = True
	if changed:
		wf.flags.ignore_permissions = True
		wf.save()
	frappe.clear_cache(doctype="Label Requisition Form")
