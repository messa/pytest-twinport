import sys
from pathlib import Path

import pytest

from pytest_twinport import lease_port
from pytest_twinport._ranges import (
    candidate_guards,
    parse_bsd_ranges,
    parse_port_list,
)

MACOS_SYSCTL = """\
net.inet.ip.portrange.lowfirst: 1023
net.inet.ip.portrange.lowlast: 600
net.inet.ip.portrange.first: 49152
net.inet.ip.portrange.last: 65535
net.inet.ip.portrange.hifirst: 49152
net.inet.ip.portrange.hilast: 65535
"""


@pytest.mark.skipif(sys.platform != "linux", reason="reads /proc")
def test_no_candidate_is_inside_the_ephemeral_range():
    text = Path("/proc/sys/net/ipv4/ip_local_port_range").read_text()
    low, high = map(int, text.split())
    for guard in candidate_guards():
        assert not low <= guard <= high
        assert not low <= guard + 1 <= high


def test_candidates_are_whole_pairs_inside_the_range():
    assert candidate_guards((20001, 20006)) == [20002, 20004]


def test_range_can_be_a_single_pair():
    assert candidate_guards((20000, 20001)) == [20000]


@pytest.mark.parametrize("port_range", [(0, 100), (2000, 1000), (1000, 70000)])
def test_invalid_range_is_rejected(port_range):
    with pytest.raises(ValueError, match="Invalid port range"):
        candidate_guards(port_range)


def test_range_from_environment(monkeypatch):
    monkeypatch.setenv("TWINPORT_RANGE", "20000-20003")
    assert candidate_guards() == [20000, 20002]


def test_explicit_range_beats_environment(monkeypatch):
    monkeypatch.setenv("TWINPORT_RANGE", "20000-20003")
    assert candidate_guards((21000, 21001)) == [21000]


def test_lease_uses_range_from_environment(monkeypatch):
    monkeypatch.setenv("TWINPORT_RANGE", "20000-20099")
    with lease_port() as port:
        assert 20000 <= port <= 20099


@pytest.mark.parametrize("value", ["20000", "a-b", "1-2-3"])
def test_malformed_environment_range_is_rejected(monkeypatch, value):
    monkeypatch.setenv("TWINPORT_RANGE", value)
    with pytest.raises(ValueError, match="TWINPORT_RANGE"):
        candidate_guards()


def test_parse_bsd_ranges_ignores_inverted_low_range():
    assert parse_bsd_ranges(MACOS_SYSCTL) == [(49152, 65535), (49152, 65535)]


def test_parse_bsd_ranges_without_data():
    assert parse_bsd_ranges("") == []


@pytest.mark.parametrize(
    "value, expected",
    [
        ("\n", set()),
        ("8080\n", {8080}),
        ("8080,9148-9150\n", {8080, 9148, 9149, 9150}),
    ],
)
def test_parse_port_list(value, expected):
    assert parse_port_list(value) == expected
