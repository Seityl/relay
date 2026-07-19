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
		this.pending_attachments = [];
		this.template_params = {};
		this.make_layout();
		this.bind_events();
		this.load_threads();
	}

	make_layout() {
		this.wrapper.find(".page-content").html(`
			<div class="relay-inbox row" style="height: calc(100vh - 180px); margin: 0;">
				<div class="col-md-4 thread-list" style="border-right: 1px solid var(--border-color); overflow-y: auto; padding: 0;">
					<div class="p-2" style="border-bottom: 1px solid var(--border-color);">
						<input type="text" class="form-control thread-search" placeholder="${__("Search contacts...")}" />
						<select class="form-control thread-filter mt-2">
							<option value="all">${__("All threads")}</option>
							<option value="unread">${__("Unread")}</option>
							<option value="prescription">${__("Prescription-related")}</option>
						</select>
					</div>
					<div class="thread-items"></div>
				</div>
				<div class="col-md-8 message-view" style="display: flex; flex-direction: column; padding: 0;">
					<div class="messages" style="flex: 1; overflow-y: auto; padding: 1rem;"></div>
					<div class="composer" style="border-top: 1px solid var(--border-color); padding: 1rem; display: none;">
						<div class="reference-banner mb-2" style="display: none;"></div>
						<textarea class="form-control reply-body" rows="3" placeholder="${__("Type a reply...")}"></textarea>
						<div class="template-params mt-2" style="display: none;"></div>
						<div class="d-flex justify-content-between align-items-center mt-2">
							<div class="d-flex align-items-center">
								<select class="form-control template-select mr-2" style="max-width: 200px;">
									<option value="">${__("No template")}</option>
								</select>
								<input type="file" class="attachment-input" style="display: none;" multiple />
								<button class="btn btn-default btn-attach mr-2">${__("Attach")}</button>
								<span class="attachment-preview small text-muted"></span>
							</div>
							<button class="btn btn-primary btn-send">${__("Send")}</button>
						</div>
					</div>
				</div>
			</div>
		`);

		this.thread_list = this.wrapper.find(".thread-items");
		this.messages = this.wrapper.find(".messages");
		this.composer = this.wrapper.find(".composer");
		this.template_select = this.wrapper.find(".template-select");
		this.template_params_area = this.wrapper.find(".template-params");
		this.attachment_input = this.wrapper.find(".attachment-input");
		this.attachment_preview = this.wrapper.find(".attachment-preview");
		this.reference_banner = this.wrapper.find(".reference-banner");

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

		this.wrapper.find(".thread-search").on("input", function () {
			me.load_threads();
		});

		this.wrapper.find(".thread-filter").on("change", function () {
			me.load_threads();
		});

		this.template_select.on("change", function () {
			me.on_template_change();
		});

		this.wrapper.find(".btn-attach").on("click", function () {
			me.attachment_input.trigger("click");
		});

		this.attachment_input.on("change", function () {
			me.handle_attachments(this.files);
		});
	}

	load_threads() {
		const search = this.wrapper.find(".thread-search").val() || "";
		const filter = this.wrapper.find(".thread-filter").val() || "all";

		frappe.call({
			method: "relay.api.messages.get_threads",
			args: {
				search: search,
				unread_only: filter === "unread" ? 1 : 0,
				prescription_only: filter === "prescription" ? 1 : 0,
				limit: 50,
			},
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
			const prescription_icon = t.reference_doctype === "RxFlow Prescription"
				? `<span class="badge badge-info ml-1">RX</span>`
				: "";
			const last_at = t.last_message_at
				? frappe.datetime.prettyDate(t.last_message_at)
				: "";
			const preview = frappe.escape_html(t.last_message || "").replace(/\n/g, " ");

			this.thread_list.append(`
				<div class="thread-item p-3" data-name="${t.name}" style="cursor: pointer; border-bottom: 1px solid var(--border-color);">
					<div class="d-flex justify-content-between">
						<strong>${frappe.escape_html(t.contact_name || t.contact)}</strong>
						<div>${unread_badge} ${prescription_icon}</div>
					</div>
					<div class="text-muted small">${__(t.status)} · ${t.phone_number || t.email || ""}</div>
					<div class="text-muted small" style="white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${preview}</div>
					<div class="text-muted small text-right">${last_at}</div>
				</div>
			`);
		});
	}

	open_thread(thread) {
		this.current_thread = thread;
		this.composer.show();
		this.pending_attachments = [];
		this.attachment_preview.text("");
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
		this.load_thread_reference(thread);
	}

	load_thread_reference(thread) {
		this.reference_banner.hide().empty();
		frappe.db.get_doc("Relay Thread", thread).then((doc) => {
			if (doc.reference_doctype === "RxFlow Prescription" && doc.reference_name) {
				this.reference_banner.show().html(`
					<a href="#Form/RxFlow Prescription/${doc.reference_name}" class="badge badge-info">
						${__("Prescription")} ${doc.reference_name}
					</a>
				`);
			}
		});
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
			let body = "";

			if (m.content_type === "html") {
				body = m.html_body || m.message_body || "";
			} else {
				body = frappe.escape_html(m.message_body || "").replace(/\n/g, "<br>");
			}

			if (m.attachments && m.attachments.length) {
				body += `<div class="mt-1">${m.attachments.map((a) => this.render_attachment(a)).join("")}</div>`;
			}

			if (m.reference_doctype === "RxFlow Prescription" && m.reference_name) {
				body += `<div class="mt-1"><a href="#Form/RxFlow Prescription/${m.reference_name}" class="badge badge-info">RX ${m.reference_name}</a></div>`;
			}

			const time = m.creation ? frappe.datetime.prettyDate(m.creation) : "";
			const status_icon = is_incoming ? "" : this.status_icon(m.status);

			this.messages.append(`
				<div style="text-align: ${align}; margin: 0.5rem 0;">
					<span style="display: inline-block; padding: 0.5rem 1rem; border-radius: 8px; background: ${bg}; border: 1px solid var(--border-color); max-width: 70%; text-align: left;">
						${body}
					</span>
					<div class="text-muted small mt-1">${time} ${status_icon}</div>
				</div>
			`);
		});

		this.messages.scrollTop(this.messages[0].scrollHeight);
	}

	render_attachment(a) {
		const is_image = (a.mime_type || "").startsWith("image/");
		if (is_image && a.file_url) {
			return `<a href="${a.file_url}" target="_blank"><img src="${a.file_url}" style="max-width: 200px; max-height: 150px; border-radius: 4px;" /></a>`;
		}
		return `<a href="${a.file_url || "#"}" target="_blank" class="badge badge-light">${frappe.escape_html(a.file_name || "Attachment")}</a>`;
	}

	status_icon(status) {
		if (status === "Read") {
			return `<span title="${__("Read")}" style="color: #0066ff;">✓✓</span>`;
		}
		if (status === "Delivered") {
			return `<span title="${__("Delivered")}">✓✓</span>`;
		}
		if (["Sent", "Accepted"].includes(status)) {
			return `<span title="${__("Sent")}">✓</span>`;
		}
		return `<span title="${__(status || "Pending")}">·</span>`;
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

	on_template_change() {
		const template = this.template_select.val();
		this.template_params_area.empty().hide();
		this.template_params = {};
		if (!template) {
			return;
		}

		frappe.call({
			method: "relay.api.messages.get_template_parameters",
			args: { template: template },
			callback: (r) => {
				const params = r.message || [];
				if (!params.length) {
					return;
				}
				this.template_params_area.show();
				params.forEach((p) => {
					this.template_params[p.parameter_index] = "";
					this.template_params_area.append(`
						<input type="text" class="form-control param-input mb-1"
							data-index="${p.parameter_index}"
							placeholder="${frappe.escape_html(p.parameter_name)}" />
					`);
				});
			},
		});
	}

	handle_attachments(files) {
		if (!files || !files.length) {
			return;
		}

		Array.from(files).forEach((file) => {
			const reader = new FileReader();
			reader.onload = (e) => {
				const dataurl = e.target.result;
				frappe.call({
					method: "upload_file",
					args: {
						filedata: dataurl,
						filename: file.name,
						is_private: 0,
					},
					callback: (r) => {
						if (r.message) {
							this.pending_attachments.push({
								file_url: r.message.file_url,
								file_name: r.message.file_name || file.name,
								mime_type: file.type,
							});
							this.attachment_preview.text(
								this.pending_attachments.map((a) => a.file_name).join(", ")
							);
						}
					},
				});
			};
			reader.readAsDataURL(file);
		});
	}

	send_reply() {
		const body = this.wrapper.find(".reply-body").val().trim();
		const template = this.template_select.val();

		if (!body && !template && !this.pending_attachments.length) {
			frappe.show_alert({ message: __("Enter a message, choose a template, or attach a file"), indicator: "orange" });
			return;
		}
		if (!this.current_thread) {
			return;
		}

		const template_parameters = {};
		this.wrapper.find(".param-input").each(function () {
			const index = $(this).data("index");
			template_parameters[index] = $(this).val() || "";
		});

		const args = {
			thread: this.current_thread,
			message_body: body,
			template: template,
			template_parameters: template_parameters,
		};
		if (this.pending_attachments.length) {
			args.attachments = this.pending_attachments;
		}

		frappe.call({
			method: "relay.relay.doctype.relay_thread.relay_thread.send_reply",
			args: args,
			callback: (r) => {
				if (r.message && r.message.success) {
					frappe.show_alert({ message: __("Reply queued"), indicator: "green" });
					this.wrapper.find(".reply-body").val("");
					this.template_select.val("");
					this.template_params_area.empty().hide();
					this.pending_attachments = [];
					this.attachment_preview.text("");
					this.open_thread(this.current_thread);
				}
			},
		});
	}

	show() {
		this.load_threads();
	}
}
