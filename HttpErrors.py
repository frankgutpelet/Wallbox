"""Short, stable descriptions for HTTP client errors.

requests/urllib3 error texts contain object addresses (``object at 0x7f...``)
that change on every attempt. Stable texts allow callers to log a failure only
once and to report its recovery.
"""
import re

import requests

_OBJECT_ADDRESS = re.compile(r"<[^<>]* at 0x[0-9a-fA-F]+>|\s*at 0x[0-9a-fA-F]+")


def describe_request_error(exc):
    """@brief Liefert eine kurze, zwischen Wiederholungen gleichbleibende Fehlerbeschreibung.

    @param exc Ausnahme aus requests oder der JSON-Dekodierung.
    @return Fehlertext ohne Objektadressen.
    """
    if isinstance(exc, requests.ConnectTimeout):
        return "connect timeout"
    if isinstance(exc, requests.ReadTimeout):
        return "read timeout"
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return "HTTP {}".format(exc.response.status_code)
    if isinstance(exc, requests.ConnectionError):
        text = str(exc)
        if "NameResolution" in text or "Name or service not known" in text or "getaddrinfo" in text:
            return "DNS lookup failed"
        if "refused" in text.lower():
            return "connection refused"
        return "connection failed"
    if isinstance(exc, ValueError):
        return "invalid response: {}".format(_OBJECT_ADDRESS.sub("", str(exc)))
    return _OBJECT_ADDRESS.sub("", str(exc)) or type(exc).__name__
