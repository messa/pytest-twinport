"""Which port pairs may be leased on this system."""

from __future__ import annotations

from functools import cache
from os import environ
from subprocess import CalledProcessError, run


DEFAULT_RANGE = (15000, 65535)
RANGE_ENV_VAR = "TWINPORT_RANGE"

# Covers the Linux default and the IANA dynamic range used by Windows and macOS.
FALLBACK_EPHEMERAL_RANGE = (32768, 65535)

_LINUX_EPHEMERAL_FILE = "/proc/sys/net/ipv4/ip_local_port_range"
_LINUX_RESERVED_FILE = "/proc/sys/net/ipv4/ip_local_reserved_ports"


def candidate_guards(port_range: tuple[int, int] | None = None) -> list[int]:
    """Return the even guard ports whose whole pair is allowed to be leased."""
    if port_range is None:
        port_range = _range_from_env() or DEFAULT_RANGE
    low, high = port_range
    if not 1 <= low <= high <= 65535:
        raise ValueError(f"Invalid port range: {low}-{high}")
    excluded = _excluded_ports()
    return [port for port in range(low + low % 2, high, 2) if port not in excluded and port + 1 not in excluded]


def _range_from_env() -> tuple[int, int] | None:
    value = environ.get(RANGE_ENV_VAR)
    if not value:
        return None
    try:
        low, high = parse_range(value)
    except ValueError:
        raise ValueError(f"{RANGE_ENV_VAR} must look like 20000-30000, got {value!r}") from None
    return low, high


def parse_range(value: str) -> tuple[int, int]:
    low, high = value.split("-")
    return int(low), int(high)


@cache
def _excluded_ports() -> frozenset[int]:
    # Outgoing connections take their source port from the ephemeral range at
    # any moment and know nothing about leases, so no lease is safe in there.
    ports: set[int] = set()
    for low, high in _ephemeral_ranges():
        ports.update(range(low, high + 1))
    return frozenset(ports | _linux_reserved_ports())


def _ephemeral_ranges() -> list[tuple[int, int]]:
    try:
        with open(_LINUX_EPHEMERAL_FILE) as f:
            low, high = f.read().split()
        return [(int(low), int(high))]
    except OSError:
        pass
    try:
        output = run(
            ["sysctl", "net.inet.ip.portrange"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (OSError, CalledProcessError):
        return [FALLBACK_EPHEMERAL_RANGE]
    return parse_bsd_ranges(output) or [FALLBACK_EPHEMERAL_RANGE]


def parse_bsd_ranges(sysctl_output: str) -> list[tuple[int, int]]:
    """Parse the output of `sysctl net.inet.ip.portrange` on BSD and macOS."""
    values: dict[str, int] = {}
    for line in sysctl_output.splitlines():
        key, separator, value = line.partition(":")
        if separator and value.strip().isdigit():
            values[key.strip().rsplit(".", 1)[-1]] = int(value)
    bounds = [("first", "last"), ("hifirst", "hilast")]
    return [
        (values[first], values[last])
        for first, last in bounds
        if first in values and last in values and values[first] <= values[last]
    ]


def _linux_reserved_ports() -> set[int]:
    try:
        with open(_LINUX_RESERVED_FILE) as f:
            return parse_port_list(f.read())
    except OSError:
        return set()


def parse_port_list(value: str) -> set[int]:
    """Parse a kernel port list such as `8080,9148-9150`."""
    ports: set[int] = set()
    for item in value.strip().split(","):
        if item:
            low, _, high = item.partition("-")
            ports.update(range(int(low), int(high or low) + 1))
    return ports
