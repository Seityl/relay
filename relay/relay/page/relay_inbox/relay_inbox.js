// Copyright (c) 2026, Jeriel Francis (trading as Seityl) and contributors
// For license information, please see license.txt

frappe.provide("relay.inbox");

frappe.pages["relay-inbox"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Relay Inbox"),
		single_column: false,
	});

	relay.inbox.instance = new RelayInbox(wrapper);

	$(wrapper).bind("show", function () {
		relay.inbox.instance.show();
	});
};

class RelayInbox {
	constructor(wrapper) {
		this.wrapper = $(wrapper);
		this.page = wrapper.page;
		this.current_thread = null;
		this.make_layout();
		this.bind_events();
		this.load_threads();
	}

	make_layout() {
		this.wrapper.find(".page-content").html(`
			<div class="relay-inbox row" style="height: calc(100vh - 180px); margin: 0;">
				<div class="col-md-4 thread-list" style="border-right: 1px solid var(--border-color); overflow-y: auto; padding: 0;"></div>
				<div class="col-md-8 message-view" style="display: flex; flex-direction: column; padding: 0;">
					<div class="messages" style="flex: 1; overflow-y: auto; padding: 1rem;"></div>
					<div class="composer" style="border-top: 1px solid var(--border-color); padding: 1rem; display: none;">
						<textarea class="form-control reply-body" rows="3" placeholder="${__("Type a reply...")}"></textarea>
						<div class="d-flex justify-content-between align-items-center mt-2">
							<select class="form-control template-select mr-2" style="max-width: 250px;">
								<option value="">${__("No template")}</option>
							</select>
							<button class="btn btn-primary btn-send">${__("Send")}</button>
						</div>
					</div>
				</div>
			</div>
		`);

		this.thread_list = this.wrapper.find(".thread-list");
		this.messages = this.wrapper.find(".messages");
		this.composer = this.wrapper.find(".composer");
		this.template_select = this.wrapper.find(".template-select");

		this.page.set_secondary_action(__("Refresh"), () => this.load_threads());
	}

	bind_events() {
		const me = this;

		this.wrapper.on("click", ".thread-item", function () {
			const thread = $(this).data("name");
			me.open_thread(thread);
		});

		this.wrapper.find(".btn-send").on("click", () => this.send_reply());

		this.wrapper.find(".reply-body").on("keydown", function (e) {
			if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
				me.send_reply();
			}
		});
	}

	load_threads() {
		frappe.call({
			method: "relay.api.messages.get_threads",
			args: { limit: 50 },
			callback: (r) => this.render_threads(r.message || []),
		});
	}

	render_threads(threads) {
		this.thread_list.empty();
		if (!threads.length) {
			this.thread_list.html(`<p class="text-muted p-3">${__("No threads")}</p>`);
			return;
		}

		threads.forEach((t) => {
			const unread_badge = t.unread_count
				? `<span class="badge badge-primary">${t.unread_count}</span>`
				: "";
			this.thread_list.append(`
				<div class="thread-item p-3" data-name="${t.name}" style="cursor: pointer; border-bottom: 1px solid var(--border-color);">
					<div class="d-flex justify-content-between">
						<strong>${frappe.escape_html(t.contact_name || t.contact)}</strong>
						${unread_badge}
					</div>
					<div class="text-muted small">${__(t.status)} · ${t.phone_number || ""}</div>
				</div>
			`);
		});
	}

	open_thread(thread) {
		this.current_thread = thread;
		this.composer.show();
		this.wrapper.find(".thread-item").removeClass("bg-light");
		this.wrapper.find(`.thread-item[data-name="${thread}"]`).addClass("bg-light");

		frappe.call({
			method: "relay.api.messages.get_messages",
			args: { thread: thread, limit: 50 },
			callback: (r) => this.render_messages(r.message || []),
		});

		frappe.call({
			method: "relay.api.messages.mark_thread_read",
			args: { thread: thread },
		});

		this.load_templates();
	}

	render_messages(messages) {
		this.messages.empty();
		if (!messages.length) {
			this.messages.html(`<p class="text-muted text-center">${__("No messages yet")}</p>`);
			return;
		}

		messages.reverse().forEach((m) => {
			const is_incoming = m.direction === "Incoming";
			const align = is_incoming ? "left" : "right";
			const bg = is_incoming ? "var(--control-bg)" : "var(--green-100)";
			const body = frappe.escape_html(m.message_body || "").replace(/\n/g, "<br>");
			const time = m.creation
				? frappe.datetime.prettyDate(m.creation)
				: "";

			this.messages.append(`
				<div style="text-align: ${align}; margin: 0.5rem 0;">
					<span style="display: inline-block; padding: 0.5rem 1rem; border-radius: 8px; background: ${bg}; border: 1px solid var(--border-color); max-width: 70%; text-align: left;">
						${body}
					</span>
					<div class="text-muted small mt-1">${time} · ${__(m.status)}</div>
				</div>
			`);
		});

		this.messages.scrollTop(this.messages[0].scrollHeight);
	}

	load_templates() {
		frappe.call({
			method: "relay.api.messages.get_templates",
			callback: (r) => {
				const templates = r.message || [];
				this.template_select.find("option:not(:first)").remove();
				templates.forEach((t) => {
					this.template_select.append(
						`<option value="${t.name}">${frappe.escape_html(t.template_name)}</option>`
					);
				});
			},
		});
	}

	send_reply() {
		const body = this.wrapper.find(".reply-body").val().trim();
		const template = this.template_select.val();

		if (!body && !template) {
			frappe.show_alert({ message: __("Enter a message or choose a template"), indicator: "orange" });
			return;
		}
		if (!this.current_thread) {
			return;
		}

		frappe.call({
			method: "relay.relay.doctype.relay_thread.relay_thread.send_reply",
			args: {
				thread: this.current_thread,
				message_body: body,
				template: template,
			},
			callback: (r) => {
				if (r.message && r.message.success) {
					frappe.show_alert({ message: __("Reply queued"), indicator: "green" });
					this.wrapper.find(".reply-body").val("");
					this.template_select.val("");
					this.open_thread(this.current_thread);
				}
			},
		});
	}

	show() {
		this.load_threads();
	}
}
