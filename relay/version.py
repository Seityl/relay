from relay import __version__


def add_version_header(response=None, request=None):
	"""after_request hook: every response says which relay build answered it."""
	if response is not None and hasattr(response, "headers"):
		response.headers["X-Relay-Version"] = __version__
