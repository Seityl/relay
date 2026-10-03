# Copyright (c) 2026, Jeriel Francis (trading as Seityl) and contributors
# For license information, please see license.txt

"""An attachment whose bytes arrived but cannot be stored does not kill the webhook (#28).

The download-failure shape already exists: Twilio's _download returns b"" on a
request failure and the handler keeps the provider URL (handler.py:316). But a
download that returns 200 with bytes frappe cannot store raised out of the
per-attachment File creation with no try/except: frappe's File.save_file strips
EXIF from image content when strip_exif_metadata_from_uploaded_images is on
(frappe/core/doctype/file/file.py:821-826 -- the content type is guessed from
the file's NAME, so this fires for a Meta document message whose patient-named
file is "prescription.jpg"), and PIL raises OSError "Truncated File Read" for
bytes it cannot decode. The message insert is not committed until
handler.py:337, so _handle_post's except (108-114) rolled the whole persist
back and answered 500: the provider's retry re-persists the same payload into
the same refusal, and every other message in the batch was lost with it.

These tests drive _handle_post the way Meta does -- a signed JSON POST -- with
the media download patched at its own seam (download_media returns
(content, mime_type)), which is where "downloaded" ends and "storable" begins.
_CORRUPT is the exact byte string the dev probe proved PIL refuses on this
machine; _good_jpeg is a real decodable image made with PIL.
"""

import hashlib
import hmac
import io
import json
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from relay.webhooks.handler import _handle_post

APP_SECRET = "persist-test-app-secret"
ACCOUNT = "Meta-Persist-Test"
CHANNEL = "Meta Persist Test Channel"
ORIGIN = "https://relay.test"
PATH = "/api/method/relay.webhooks.handler.receive"
QUERY = f"account={ACCOUNT}"
PHONE = "15550007777"

#: A JPEG magic header followed by non-JPEG bytes: what the dev probe run of
#: 2026-10-03 proved PIL refuses with OSError "Truncated File Read"
#: (relay#28's lab reproduction).
_CORRUPT = b"\xff\xd8\xff\xe0probe-not-a-jpeg"


def _good_jpeg() -> bytes:
	buf = io.BytesIO()
	from PIL import Image

	Image.new("RGB", (8, 8), (255, 255, 255)).save(buf, format="JPEG")
	return buf.getvalue()


class _Headers(dict):
	def get(self, key, default=""):
		for k, v in self.items():
			if k.lower() == key.lower():
				return v
		return default


class _Form(dict):
	def to_dict(self):
		return dict(self)


class _JsonRequest:
	method = "POST"

	def __init__(self, body: bytes):
		self.data = body
		self.headers = _Headers({})
		self.url = f"{ORIGIN}{PATH}?{QUERY}"
		self.path = PATH
		self.query_string = QUERY.encode()

	@property
	def form(self):
		return _Form({})

	@property
	def args(self):
		return _Form({"account": ACCOUNT})


class TestAnUndecodableImageAttachment(IntegrationTestCase):
	def setUp(self):
		"""Fresh channel + account per test, not per class.

		Today's failing drive reaches _handle_post's except, which calls
		frappe.db.rollback() -- inside the test transaction that holds the
		class fixtures. A per-class fixture would be wiped for every test
		that runs after the first failing one (the m5c class-transaction
		lesson), which is exactly what the first red run showed: the batch
		test "failed" because its account no longer existed, not because of
		the attachment.
		"""
		super().setUp()

		if not frappe.db.exists("Relay Channel", CHANNEL):
			frappe.get_doc(
				{
					"doctype": "Relay Channel",
					"channel_name": CHANNEL,
					"provider": "Meta Cloud API",
					"enabled": 1,
				}
			).insert(ignore_permissions=True)

		if not frappe.db.exists("Relay Account", {"account_name": ACCOUNT}):
			self.account = frappe.get_doc(
				{
					"doctype": "Relay Account",
					"account_name": ACCOUNT,
					"status": "Active",
					"channel": CHANNEL,
					"app_secret": APP_SECRET,
					"access_token": "persist-test-access-token",
					"phone_number_id": "persist-test-phone-id",
					"api_url": "https://graph.facebook.com",
					"api_version": "v21.0",
				}
			).insert(ignore_permissions=True)
		else:
			self.account = frappe.get_doc("Relay Account", {"account_name": ACCOUNT})

	def _post_meta(self, payload: dict, download_returns: list):
		"""Drive the webhook the way Meta does: a signed JSON POST. The media
		download is patched at its own seam; `frappe.db.commit` is suppressed
		(the handler commits three places; a real commit would defeat the
		class rollback -- see test_signature_gate.py's _post). `frappe.log_error`
		is captured rather than allowed to run: it commits, which would poison
		the suite's open transaction (the 2026-09-28 side-row lesson); the
		tests assert on the captured calls, and the dev probe shows the real
		Error Log row on the deployed build."""
		body = json.dumps(payload).encode()
		signature = "sha256=" + hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
		request = _JsonRequest(body)
		with (
			patch("frappe.request", request),
			patch("frappe.db.commit"),
			patch("frappe.utils.get_url", return_value=ORIGIN),
			patch("frappe.log_error") as log_error,
			patch(
				"relay.integrations.meta_cloud_api.MetaCloudAPIAdapter.download_media",
				side_effect=download_returns,
			),
		):
			frappe.local.form_dict = frappe._dict()
			try:
				response = _handle_post()
			finally:
				frappe.local.form_dict = frappe._dict()
		self.log_error = log_error
		return response

	def _document_payload(self, filename: str, media_id: str) -> dict:
		return {
			"entry": [
				{
					"changes": [
						{
							"field": "messages",
							"value": {
								"metadata": {"phone_number_id": "persist-test-phone-id"},
								"contacts": [{"wa_id": PHONE, "profile": {"name": "persist test patient"}}],
								"messages": [
									{
										"from": PHONE,
										"id": f"wamid.{media_id}",
										"type": "document",
										"document": {
											"id": media_id,
											"filename": filename,
											"mime_type": "image/jpeg",
										},
									}
								],
							},
						}
					]
				}
			]
		}

	def _inbound(self, media_id: str):
		return frappe.db.get_value("Relay Message", {"message_id": f"wamid.{media_id}", "direction": "Incoming"}, "name")

	def _webhook_log_status(self, media_id: str):
		return frappe.db.get_value(
			"Relay Webhook Log", {"payload": ["like", f"%{media_id}%"]}, "status", order_by="creation desc"
		)

	def test_a_webhook_with_an_undecodable_image_answers_200_and_keeps_the_message(self):
		"""#28: the bytes arrived but frappe cannot store them (PIL refuses to
		decode during the EXIF strip). The webhook still answers 200, the
		message and its attachment row persist, and the failure is recorded
		naming the message -- instead of a 500 whose rollback threw the whole
		persist away and invited the provider's retry into the same refusal."""
		media_id = frappe.generate_hash(length=12)
		response = self._post_meta(
			self._document_payload("prescription.jpg", media_id),
			[(_CORRUPT, "image/jpeg")],
		)

		self.assertEqual(response.status_code, 200, "an unstorable attachment answered 500")
		name = self._inbound(media_id)
		self.assertTrue(name, "the message did not survive an unstorable attachment")
		doc = frappe.get_doc("Relay Message", name)
		self.assertEqual(len(doc.attachments), 1, "the attachment row was not kept")
		self.assertEqual(self._webhook_log_status(media_id), "Processed")
		named = [c for c in self.log_error.call_args_list
				 if c.kwargs.get("reference_name") == name]
		self.assertTrue(
			named,
			f"the unstorable attachment was not logged naming the message: {self.log_error.call_args_list}",
		)

	def test_one_bad_attachment_does_not_lose_the_other_messages_in_the_batch(self):
		"""#28: today the first unstorable attachment rolled the whole persist
		back -- every message in the payload was lost, not just the bad one."""
		bad_id, good_id = frappe.generate_hash(length=12), frappe.generate_hash(length=12)
		payload = self._document_payload("prescription.jpg", bad_id)
		payload["entry"][0]["changes"][0]["value"]["messages"].append(
			{
				"from": PHONE,
				"id": f"wamid.{good_id}",
				"type": "document",
				"document": {"id": good_id, "filename": "scan.jpg", "mime_type": "image/jpeg"},
			}
		)
		response = self._post_meta(payload, [(_CORRUPT, "image/jpeg"), (_good_jpeg(), "image/jpeg")])

		self.assertEqual(response.status_code, 200)
		self.assertTrue(self._inbound(bad_id), "the message with the bad attachment was lost")
		good = self._inbound(good_id)
		self.assertTrue(good, "the batch's good message was lost behind the bad one")
		good_doc = frappe.get_doc("Relay Message", good)
		self.assertEqual(len(good_doc.attachments), 1)
		self.assertTrue(good_doc.attachments[0].file_url, "the good attachment lost its File URL")

	def test_a_valid_image_still_lands_its_file(self):
		"""#28 control: a storable image goes through the same strip path and
		lands its File row and its URL -- the guard must not catch good media."""
		media_id = frappe.generate_hash(length=12)
		response = self._post_meta(
			self._document_payload("prescription.jpg", media_id),
			[(_good_jpeg(), "image/jpeg")],
		)

		self.assertEqual(response.status_code, 200)
		name = self._inbound(media_id)
		self.assertTrue(name, "a valid image message did not persist")
		doc = frappe.get_doc("Relay Message", name)
		self.assertEqual(len(doc.attachments), 1)
		file_url = doc.attachments[0].file_url
		self.assertTrue(file_url, "the valid image has no File URL")
		self.assertTrue(
			frappe.db.exists("File", {"file_url": file_url, "attached_to_doctype": "Relay Message",
									  "attached_to_name": name}),
			f"the File row for {file_url} did not land on {name}",
		)
