from pathlib import Path
from random import Random
from subprocess import CalledProcessError, CompletedProcess
from sys import platform

from pytest import fixture, mark, raises

from pytest_twinport import NoFreePortError, _lease, _ranges, lease_port
from pytest_twinport._ranges import (
    Candidates,
    candidate_guards,
    exclusions_for_platform,
    guard_blocks,
    merge_intervals,
    parse_bsd_ranges,
    parse_port_list,
    system_exclusions,
)


MACOS_SYSCTL = """\
net.inet.ip.portrange.lowfirst: 1023
net.inet.ip.portrange.lowlast: 600
net.inet.ip.portrange.first: 49152
net.inet.ip.portrange.last: 65535
net.inet.ip.portrange.hifirst: 49152
net.inet.ip.portrange.hilast: 65535
"""


@fixture
def no_system_exclusions(monkeypatch):
    """Make the candidates independent of the ephemeral range of this machine."""
    monkeypatch.setattr(_ranges, "system_exclusions", lambda: ())


def guards_by_brute_force(port_range, excluded):
    low, high = port_range
    ports = {port for excluded_low, excluded_high in excluded for port in range(excluded_low, excluded_high + 1)}
    return [port for port in range(low + low % 2, high, 2) if port not in ports and port + 1 not in ports]


def guards_from_blocks(blocks):
    return [first + 2 * i for first, count in blocks for i in range(count)]


@mark.skipif(platform != "linux", reason="reads /proc")
def test_no_candidate_is_inside_the_ephemeral_range():
    text = Path("/proc/sys/net/ipv4/ip_local_port_range").read_text()
    low, high = map(int, text.split())
    for guard in candidate_guards():
        assert not low <= guard <= high
        assert not low <= guard + 1 <= high


def test_default_range_leaves_out_kubernetes_node_ports(no_system_exclusions):
    assert candidate_guards().blocks == ((15000, 7500), (32768, 16384))


def test_explicit_range_keeps_kubernetes_node_ports(no_system_exclusions):
    assert list(candidate_guards((30000, 30003))) == [30000, 30002]


def test_environment_range_keeps_kubernetes_node_ports(no_system_exclusions, monkeypatch):
    monkeypatch.setenv("TWINPORT_RANGE", "30000-30003")
    assert list(candidate_guards()) == [30000, 30002]


def test_candidates_are_whole_pairs_inside_the_range(no_system_exclusions):
    assert list(candidate_guards((20001, 20006))) == [20002, 20004]


def test_range_can_be_a_single_pair(no_system_exclusions):
    assert list(candidate_guards((20000, 20001))) == [20000]


def test_candidates_can_be_indexed(monkeypatch):
    monkeypatch.setattr(_ranges, "system_exclusions", lambda: (("test", ((20004, 20007),)),))
    guards = candidate_guards((20000, 20011))
    assert len(guards) == 4
    assert [guards[i] for i in range(len(guards))] == list(guards) == [20000, 20002, 20008, 20010]
    with raises(IndexError):
        guards[4]


@mark.parametrize("port_range", [(0, 100), (2000, 1000), (1000, 70000)])
def test_invalid_range_is_rejected(port_range):
    with raises(ValueError, match="Invalid port range"):
        candidate_guards(port_range)


@mark.parametrize("port_range", [(20001, 20002), (65535, 65535), (20000, 20000)])
def test_range_without_a_whole_pair_is_rejected(port_range):
    with raises(ValueError, match="even port and the odd port after it"):
        candidate_guards(port_range)


@mark.parametrize("port_range", [(), (20000,), (20000, 20001, 20002)])
def test_range_of_wrong_length_is_rejected(port_range):
    with raises(ValueError, match=r"\(low, high\) tuple"):
        candidate_guards(port_range)


@mark.parametrize("port_range", ["20000-20001", (20000.0, 20001.0), (True, 3), (20000, "20001"), 20000])
def test_range_of_wrong_type_is_rejected(port_range):
    with raises(TypeError, match="port_range"):
        candidate_guards(port_range)


class Port:
    """An integer type that isn't int, like those of NumPy."""

    def __init__(self, value):
        self.value = value

    def __index__(self):
        return self.value


def test_range_can_use_other_integer_types(no_system_exclusions):
    assert list(candidate_guards((Port(20000), Port(20001)))) == [20000]


def test_range_can_be_a_list(no_system_exclusions):
    assert list(candidate_guards([20000, 20001])) == [20000]


def test_range_from_environment(no_system_exclusions, monkeypatch):
    monkeypatch.setenv("TWINPORT_RANGE", "20000-20003")
    assert list(candidate_guards()) == [20000, 20002]


def test_explicit_range_beats_environment(no_system_exclusions, monkeypatch):
    monkeypatch.setenv("TWINPORT_RANGE", "20000-20003")
    assert list(candidate_guards((21000, 21001))) == [21000]


def test_lease_uses_range_from_environment(monkeypatch, pair):
    guard_port, port = pair
    monkeypatch.setenv("TWINPORT_RANGE", f"{guard_port}-{port}")
    with lease_port() as leased_port:
        assert leased_port == port


@mark.parametrize("value", ["20000", "a-b", "1-2-3"])
def test_malformed_environment_range_is_rejected(monkeypatch, value):
    monkeypatch.setenv("TWINPORT_RANGE", value)
    with raises(ValueError, match="TWINPORT_RANGE"):
        candidate_guards()


@mark.parametrize("value", ["0-100", "20001-20002"])
def test_invalid_environment_range_names_the_variable(monkeypatch, value):
    monkeypatch.setenv("TWINPORT_RANGE", value)
    with raises(ValueError, match="Invalid port range .* in TWINPORT_RANGE"):
        candidate_guards()


def test_range_inside_the_ephemeral_ports_raises_no_free_port_error():
    exclusions = dict(system_exclusions())
    low, high = exclusions["ephemeral ports"][0]
    port_range = (low, min(low + 99, high))
    with raises(NoFreePortError, match=rf"{low}-\d+ \(port_range\) has no pair outside .*ephemeral ports {low}-"):
        lease_port(port_range=port_range)


def test_error_lists_only_exclusions_overlapping_the_range():
    exclusions = [
        ("ephemeral ports", ((40000, 60999), (40000, 60999))),
        ("reserved ports", tuple((port, port) for port in range(20000, 20020, 2))),
    ]
    assert Candidates((40000, 40001), "test", exclusions).describe_exclusions() == "ephemeral ports 40000-60999"
    assert Candidates((20000, 20009), "test", exclusions).describe_exclusions() == (
        "reserved ports 20000,20002,20004,20006,20008"
    )
    assert Candidates((20000, 20019), "test", exclusions).describe_exclusions() == (
        "reserved ports 20000,20002,20004,20006,20008 and 5 more"
    )


def test_guard_blocks_skip_excluded_ports():
    assert guard_blocks((20000, 20011), [(20005, 20006)]) == ((20000, 2), (20008, 2))


def test_guard_blocks_ignore_inverted_intervals():
    assert guard_blocks((0, 100), [(50, 40)]) == ((0, 50),)


def test_merge_intervals():
    assert merge_intervals([(10, 20), (1, 3), (4, 5), (15, 25), (9, 8)]) == ((1, 5), (10, 25))


def test_guard_blocks_match_a_brute_force_check():
    rng = Random(1)
    for _ in range(2000):
        low = rng.randint(1, 200)
        high = rng.randint(low, 260)
        excluded = []
        for _ in range(rng.randint(0, 4)):
            excluded_low = rng.randint(1, 270)
            excluded.append((excluded_low, excluded_low + rng.randint(0, 30)))
        blocks = guard_blocks((low, high), excluded)
        assert guards_from_blocks(blocks) == guards_by_brute_force((low, high), excluded)


@fixture
def linux_files(monkeypatch, tmp_path):
    """Point the Linux exclusions at files written by the test."""
    ephemeral = tmp_path / "ip_local_port_range"
    reserved = tmp_path / "ip_local_reserved_ports"
    monkeypatch.setattr(_ranges, "_LINUX_EPHEMERAL_FILE", str(ephemeral))
    monkeypatch.setattr(_ranges, "_LINUX_RESERVED_FILE", str(reserved))
    return ephemeral, reserved


def test_linux_reads_ephemeral_and_reserved_ports(linux_files):
    ephemeral, reserved = linux_files
    ephemeral.write_text("40000\t60999\n")
    reserved.write_text("20003,20006-20007\n")
    assert exclusions_for_platform("linux") == (
        ("ephemeral ports", ((40000, 60999),)),
        ("reserved ports", ((20003, 20003), (20006, 20007))),
    )


def test_linux_falls_back_without_proc(linux_files):
    assert exclusions_for_platform("linux") == (
        ("ephemeral ports", ((32768, 65535),)),
        ("reserved ports", ()),
    )


def test_linux_tolerates_malformed_files(linux_files):
    ephemeral, reserved = linux_files
    ephemeral.write_text("garbage\n")
    reserved.write_text("80,x\n")
    assert exclusions_for_platform("linux") == (
        ("ephemeral ports", ((32768, 65535),)),
        ("reserved ports", ()),
    )


def test_reserved_ports_are_left_out(monkeypatch, linux_files):
    ephemeral, reserved = linux_files
    ephemeral.write_text("40000 60999\n")
    reserved.write_text("20003,20006-20007\n")
    monkeypatch.setattr(_ranges, "system_exclusions", lambda: exclusions_for_platform("linux"))
    assert list(candidate_guards((20000, 20009))) == [20000, 20004, 20008]


def sysctl_returning(stdout):
    def fake_run(args, **kwargs):
        assert args == ["sysctl", "net.inet.ip.portrange"]
        return CompletedProcess(args, 0, stdout=stdout)

    return fake_run


@mark.parametrize("name", ["darwin", "freebsd14", "dragonfly6"])
def test_sysctl_platforms_read_the_range(monkeypatch, name):
    monkeypatch.setattr(_ranges, "run", sysctl_returning(MACOS_SYSCTL))
    assert exclusions_for_platform(name) == (("ephemeral ports", ((49152, 65535), (49152, 65535))),)


def failing_sysctl(error):
    def fake_run(args, **kwargs):
        raise error

    return fake_run


@mark.parametrize("error", [FileNotFoundError(), CalledProcessError(1, "sysctl")])
def test_sysctl_platforms_fall_back_when_sysctl_fails(monkeypatch, error):
    monkeypatch.setattr(_ranges, "run", failing_sysctl(error))
    assert exclusions_for_platform("darwin") == (("ephemeral ports", ((32768, 65535),)),)


def test_sysctl_platforms_fall_back_on_unknown_output(monkeypatch):
    monkeypatch.setattr(_ranges, "run", sysctl_returning("net.inet.ip.portfirst=1024\n"))
    assert exclusions_for_platform("darwin") == (("ephemeral ports", ((32768, 65535),)),)


@mark.parametrize("name", ["win32", "openbsd7", "netbsd10", "cygwin"])
def test_other_platforms_use_the_fallback(monkeypatch, name):
    monkeypatch.setattr(_ranges, "run", failing_sysctl(AssertionError("sysctl must not run")))
    assert exclusions_for_platform(name) == (("ephemeral ports", ((32768, 65535),)),)


def test_parse_bsd_ranges_ignores_inverted_low_range():
    assert parse_bsd_ranges(MACOS_SYSCTL) == [(49152, 65535), (49152, 65535)]


def test_parse_bsd_ranges_without_data():
    assert parse_bsd_ranges("") == []


@mark.parametrize(
    "value, expected",
    [
        ("\n", []),
        ("8080\n", [(8080, 8080)]),
        ("8080,9148-9150\n", [(8080, 8080), (9148, 9150)]),
    ],
)
def test_parse_port_list(value, expected):
    assert parse_port_list(value) == expected


@mark.parametrize("count", [1, 2, 3, 4, 12, 97, 100])
def test_random_order_visits_every_index_once(count):
    for _ in range(20):
        order = list(_lease._random_order(count))
        assert sorted(order) == list(range(count))


def test_random_order_is_not_a_linear_scan():
    strides = {(order[1] - order[0]) % 100 for order in (list(_lease._random_order(100)) for _ in range(20))}
    assert strides != {1}
