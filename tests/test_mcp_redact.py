import pytest

from bugit_v2.mcp_server.redact import Redactor


@pytest.fixture
def r() -> Redactor:
    return Redactor(username="alice")


@pytest.mark.parametrize(
    "raw",
    ["aa:bb:cc:dd:ee:ff", "AA-BB-CC-DD-EE-FF", "00:1A:2b:3C:4d:5E"],
)
def test_mac_addresses(r: Redactor, raw: str):
    out = r.redact(f"link/ether {raw} brd ff")
    assert raw not in out
    assert "<MAC>" in out


def test_ipv4(r: Redactor):
    out = r.redact("connect from 192.168.1.42 to 10.0.0.1:443")
    assert "192.168.1.42" not in out and "10.0.0.1" not in out
    assert out.count("<IPv4>") == 2
    assert ":443" in out


def test_dotted_numbers_with_big_octets_are_not_ips(r: Redactor):
    assert r.redact("v999.1.1.1 and 1.2.3") == "v999.1.1.1 and 1.2.3"


@pytest.mark.parametrize(
    "raw",
    [
        "2001:db8:85a3::8a2e:370:7334",
        "fe80::1ff:fe23:4567:890a",
        "2001:0db8:0000:0000:0000:ff00:0042:8329",
    ],
)
def test_ipv6(r: Redactor, raw: str):
    out = r.redact(f"addr {raw}/64 scope")
    assert raw not in out
    assert "<IPv6>" in out


def test_ipv6_does_not_eat_timestamps_or_mac(r: Redactor):
    out = r.redact("12:34:56 kernel: aa:bb:cc:dd:ee:ff")
    assert out.startswith("12:34:56 kernel:")
    assert "<MAC>" in out and "<IPv6>" not in out


def test_email(r: Redactor):
    out = r.redact("Reported-by: Jane.Doe+x@example.co.uk, ok")
    assert "example.co.uk" not in out
    assert "<EMAIL>" in out


def test_username_in_paths_and_words(r: Redactor):
    out = r.redact("open /home/alice/.cache failed; user=alice; alice@host")
    assert "alice" not in out
    assert "/home/<USER>/.cache" in out


def test_username_is_word_bounded(r: Redactor):
    assert r.redact("malice in wonderland") == "malice in wonderland"


def test_empty_or_short_username_is_ignored():
    assert Redactor(username="").redact("root a b") == "root a b"
    assert Redactor(username=None).redact("x") == "x"


def test_no_pii_is_untouched(r: Redactor):
    line = "ACPI Error: AE_NOT_FOUND, While evaluating Sleep State [\\_S1_]"
    assert r.redact(line) == line


def test_multiline(r: Redactor):
    out = r.redact("a 1.2.3.4\nb aa:bb:cc:dd:ee:ff\nc x@y.io")
    assert out.splitlines() == ["a <IPv4>", "b <MAC>", "c <EMAIL>"]
