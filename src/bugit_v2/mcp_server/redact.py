"""Best-effort PII masking applied to every log string returned to the LLM"""

import getpass
import ipaddress
import re

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
_MAC = re.compile(
    r"(?<![0-9A-Fa-f:-])(?:[0-9A-Fa-f]{2}([:-]))(?:[0-9A-Fa-f]{2}\1){4}"
    r"[0-9A-Fa-f]{2}(?![0-9A-Fa-f:-])"
)
_IPV6_CANDIDATE = re.compile(
    r"(?<![0-9A-Za-z:])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?![0-9A-Za-z:])"
)
_IPV4_CANDIDATE = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?!\d|\.\d)")


def _mask_if_valid(version: int, token: str) -> str:
    try:
        ipaddress.ip_address(token)
    except ValueError:
        return token
    return "<IPv4>" if version == 4 else "<IPv6>"


class Redactor:
    def __init__(self, username: str | None = None) -> None:
        self._username_re = (
            re.compile(rf"(?<![A-Za-z0-9_]){re.escape(username)}(?![A-Za-z0-9_])")
            if username
            else None
        )

    @classmethod
    def for_current_user(cls) -> "Redactor":
        try:
            return cls(getpass.getuser())
        except (KeyError, OSError):
            return cls(None)

    def redact(self, text: str) -> str:
        text = _EMAIL.sub("<EMAIL>", text)
        text = _MAC.sub("<MAC>", text)
        text = _IPV6_CANDIDATE.sub(lambda m: _mask_if_valid(6, m.group(0)), text)
        text = _IPV4_CANDIDATE.sub(lambda m: _mask_if_valid(4, m.group(0)), text)
        if self._username_re:
            text = self._username_re.sub("<USER>", text)
        return text
