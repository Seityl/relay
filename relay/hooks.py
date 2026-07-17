app_name = "relay"
app_title = "Relay"
app_publisher = "Pharmcare"
app_description = "Multi-channel business messaging for Frappe"
app_email = "dev@pharmcare.kn"
app_license = "mit"

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "relay",
# 		"logo": "/assets/relay/logo.png",
# 		"title": "Relay",
# 		"route": "/relay",
# 		"has_permission": "relay.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/relay/css/relay.css"
# app_include_js = "/assets/relay/js/relay.js"

# include js, css files in header of web template
# web_include_css = "/assets/relay/css/relay.css"
# web_include_js = "/assets/relay/js/relay.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "relay/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "relay/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# automatically load and sync documents of this doctype from downstream apps
# importable_doctypes = [doctype_1]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "relay.utils.jinja_methods",
# 	"filters": "relay.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "relay.install.before_install"
after_install = "relay.install.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "relay.uninstall.before_uninstall"
# after_uninstall = "relay.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "relay.utils.before_app_install"
# after_app_install = "relay.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "relay.utils.before_app_uninstall"
# after_app_uninstall = "relay.utils.after_app_uninstall"

# Build
# ------------------
# To hook into the build process

# after_build = "relay.build.after_build"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "relay.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# Document Events
# ---------------
# Hook on document methods and events

doc_events = {
	"*": {
		"after_insert": "relay.scheduler.notification_engine.run_doc_event_notification",
		"on_update": "relay.scheduler.notification_engine.run_doc_event_notification",
		"after_submit": "relay.scheduler.notification_engine.run_doc_event_notification",
		"after_cancel": "relay.scheduler.notification_engine.run_doc_event_notification",
		"after_delete": "relay.scheduler.notification_engine.run_doc_event_notification",
	}
}

# Scheduled Tasks
# ---------------

scheduler_events = {
	"all": [
		"relay.queue.outbound_queue.process_queue",
	],
	"hourly": [
		"relay.scheduler.notification_engine.hourly",
	],
	"hourly_long": [
		"relay.scheduler.notification_engine.hourly_long",
	],
	"daily": [
		"relay.scheduler.notification_engine.daily",
	],
	"daily_long": [
		"relay.scheduler.notification_engine.daily_long",
	],
	"weekly": [
		"relay.scheduler.notification_engine.weekly",
	],
	"weekly_long": [
		"relay.scheduler.notification_engine.weekly_long",
	],
	"monthly": [
		"relay.scheduler.notification_engine.monthly",
	],
	"monthly_long": [
		"relay.scheduler.notification_engine.monthly_long",
	],
	"yearly": [
		"relay.scheduler.notification_engine.yearly",
	],
}

# Testing
# -------

# before_tests = "relay.install.before_tests"

# Extend DocType Class
# ------------------------------
#
# Specify custom mixins to extend the standard doctype controller.
# extend_doctype_class = {
# 	"Task": "relay.custom.task.CustomTaskMixin"
# }

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "relay.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "relay.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["relay.utils.before_request"]
# after_request = ["relay.utils.after_request"]

# Job Events
# ----------
# before_job = ["relay.utils.before_job"]
# after_job = ["relay.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"relay.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

default_log_clearing_doctypes = {
	"Relay Webhook Log": 30,
	"Relay Outbound Queue": 30,
}

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []

