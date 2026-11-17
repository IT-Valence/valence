"""LRF workflow: let QA send a form back to production.

Adds a "Send Back" action to the active Label Requisition Form workflow, from every
state between Draft and the submitted (approved) state, back to Draft. Whoever may
approve a form at that stage may also send it back. The form itself asks for the reason
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

	in_progress = [s.state for s in wf.states if str(s.doc_status) == "0" and s.state != draft]
	existing = {(t.state, t.action) for t in wf.transitions}
	added = False
	for state in in_progress:
		if (state, ACTION) in existing:
			continue
		forward = next((t for t in wf.transitions if t.state == state and t.action != ACTION), None)
		wf.append(
			"transitions",
			{
				"state": state,
				"action": ACTION,
				"next_state": draft,
				"allowed": forward.allowed if forward else "All",
				"allow_self_approval": 1,
			},
		)
		added = True
	if added:
		wf.flags.ignore_permissions = True
		wf.save()
	frappe.clear_cache(doctype="Label Requisition Form")
