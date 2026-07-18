frappe.ui.form.on("Relay Thread", {
	refresh(frm) {
		if (frm.is_new()) {
			return;
		}

		frm.add_custom_button(__('Reply'), () => {
			const d = new frappe.ui.Dialog({
				title: __('Reply to Thread'),
				fields: [
					{
						fieldname: 'message_body',
						fieldtype: 'Text Editor',
						label: __('Message'),
						reqd: 1,
					},
					{
						fieldname: 'template',
						fieldtype: 'Link',
						label: __('Or use Template'),
						options: 'Relay Template',
					},
				],
				primary_action_label: __('Send'),
				primary_action(values) {
					frappe.call({
						method: 'relay.relay.doctype.relay_thread.relay_thread.send_reply',
						args: {
							thread: frm.doc.name,
							message_body: values.message_body,
							template: values.template,
						},
						btn: d.get_primary_btn(),
						callback(r) {
							if (r.message && r.message.success) {
								frappe.show_alert({
									message: __('Reply queued for delivery'),
									indicator: 'green',
								});
								d.hide();
								frm.reload_doc();
							}
						},
					});
				},
			});
			d.show();
		}, __('Actions'));

		if (frm.doc.unread_count) {
			frm.add_custom_button(__('Mark Read'), () => {
				frappe.call({
					method: 'relay.api.messages.mark_thread_read',
					args: { thread: frm.doc.name },
					callback() {
						frm.reload_doc();
					},
				});
			}, __('Actions'));
		}

		frm.add_custom_button(__('Resolve'), () => {
			frm.set_value('status', 'Resolved');
			frm.save();
		}, __('Actions'));
	},
});
