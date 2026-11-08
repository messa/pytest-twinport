"""Which port pairs may be leased on this system.

Port sets are kept as sorted lists of inclusive `(low, high)` intervals, and the
candidate guards as blocks of consecutive pairs, so nothing here grows with the
number of ports.
"""

from __future__ import annotations

import sys
from bisect import bisect_right
from collections.abc import Iterator, Sequence
from functools import cache
from itertools import accumulate
from os import environ
from subprocess import CalledProcessError, run


Interval = tuple[int, int]
# A labeled group of excluded ports, the label is used in error messages.
Exclusion = tuple[str, tuple[Interval, ...]]

DEFAULT_RANGE = (15000, 65535)
RANGE_ENV_VAR = "TWINPORT_RANGE"

# Left out of the default range only. Kubernetes NodePorts are redirected by
# packet filter rules without any socket being bound, so a bind check can't see
# them.
DEFAULT_EXCLUDED = ("Kubernetes NodePorts", ((30000, 32767),))

# Covers the Linux default and the IANA dynamic range used by Windows and macOS.
FALLBACK_EPHEMERAL_RANGE = (32768, 65535)

_LINUX_EPHEMERAL_FILE = "/proc/sys/net/ipv4/ip_local_port_range"
_LINUX_RESERVED_FILE = "/proc/sys/net/ipv4/ip_local_reserved_ports"
_BSD_PLATFORMS = ("freebsd", "openbsd", "netbsd", "dragonfly")


class Candidates:
    """Guard ports whose whole pair may be leased, in increasing order."""

    def __init__(
        self,
        port_range: Interval,
        source: str,
        exclusions: Sequence[Exclusion],
    ) -> None:
        self.port_range = port_range
        self.source = source
        self.exclusions = tuple((label, intervals) for label, intervals in exclusions if intervals)
        excluded = [interval for _, intervals in self.exclusions for interval in intervals]
        self.blocks = guard_blocks(port_range, excluded)
        self._ends = list(accumulate(count for _, count in self.blocks))

    def __len__(self) -> int:
        return self._ends[-1] if self._ends else 0

    def __getitem__(self, index: int) -> int:
        if not 0 <= index < len(self):
            raise IndexError(index)
        block = bisect_right(self._ends, index)
        first, count = self.blocks[block]
        return first + 2 * (index - self._ends[block] + count)

    def __iter__(self) -> Iterator[int]:
        for first, count in self.blocks:
            yield from range(first, first + 2 * count, 2)

    def describe_range(self) -> str:
        low, high = self.port_range
        return f"{low}-{high} ({self.source})"

    def describe_exclusions(self) -> str:
        return ", ".join(f"{label} {format_intervals(intervals)}" for label, intervals in self.exclusions)


def candidate_guards(port_range: tuple[int, int] | None = None) -> Candidates:
    """Return the guards of the pairs that may be leased.

    The range is `port_range` if given, then `TWINPORT_RANGE`, then the default.
    Ephemeral and reserved ports of the system are always left out.
    """
    exclusions = list(system_exclusions())
    if port_range is not None:
        return Candidates(check_range(port_range, "port_range"), "port_range", exclusions)
    value = environ.get(RANGE_ENV_VAR)
    if value:
        return Candidates(check_range(parse_range(value), RANGE_ENV_VAR), RANGE_ENV_VAR, exclusions)
    return Candidates(DEFAULT_RANGE, "default", [DEFAULT_EXCLUDED, *exclusions])


def check_range(port_range: object, source: str) -> Interval:
    """Validate a `(low, high)` range that should contain at least one pair."""
    if not isinstance(port_range, (tuple, list)) or not all(
        isinstance(port, int) and not isinstance(port, bool) for port in port_range
    ):
        raise TypeError(f"{source} must be a (low, high) tuple of ints, got {port_range!r}")
    if len(port_range) != 2:
        raise ValueError(f"{source} must be a (low, high) tuple of ints, got {port_range!r}")
    low, high = port_range
    if not 1 <= low <= high <= 65535:
        raise ValueError(f"Invalid port range {low}-{high} in {source}: it must be within 1-65535 and low <= high")
    if low + low % 2 + 1 > high:
        raise ValueError(
            f"Invalid port range {low}-{high} in {source}: it must contain an even port and the odd port after it"
        )
    return low, high


def parse_range(value: str) -> Interval:
    """Parse a range such as `20000-30000` from the environment."""
    try:
        low, high = value.split("-")
        return int(low), int(high)
    except ValueError:
        raise ValueError(f"{RANGE_ENV_VAR} must look like 20000-30000, got {value!r}") from None


def guard_blocks(port_range: Interval, excluded: Sequence[Interval]) -> tuple[Interval, ...]:
    """Return `(first guard, pair count)` blocks of the pairs in `port_range`
    that don't touch any excluded interval."""
    low, high = port_range
    blocks = []
    guard = low + low % 2
    # The sentinel past the range closes the last block.
    for excluded_low, excluded_high in [*sorted(excluded), (high + 2, high + 2)]:
        # The last guard before the interval needs its odd port below it too.
        last = min(excluded_low - 2, high - 1)
        if last >= guard:
            blocks.append((guard, (last - guard) // 2 + 1))
        after = excluded_high + 1
        guard = max(guard, after + after % 2)
    return tuple(blocks)


def format_intervals(intervals: Sequence[Interval]) -> str:
    return ",".join(str(low) if low == high else f"{low}-{high}" for low, high in intervals)


@cache
def system_exclusions() -> tuple[Exclusion, ...]:
    """Return the ports that must never be leased on this system.

    Outgoing connections take their source port from the ephemeral range at any
    moment and know nothing about leases, so no lease is safe in there.
    """
    if sys.platform == "linux":
        return (
            ("ephemeral ports", tuple(_linux_ephemeral_ranges())),
            ("reserved ports", tuple(_linux_reserved_ports())),
        )
    if sys.platform == "darwin" or sys.platform.startswith(_BSD_PLATFORMS):
        return (("ephemeral ports", tuple(_bsd_ephemeral_ranges())),)
    # Windows doesn't report its range in a form easy to read, see the README.
    return (("ephemeral ports", (FALLBACK_EPHEMERAL_RANGE,)),)


def _linux_ephemeral_ranges() -> list[Interval]:
    try:
        with open(_LINUX_EPHEMERAL_FILE) as f:
            low, high = f.read().split()
        return [(int(low), int(high))]
    except (OSError, ValueError):
        return [FALLBACK_EPHEMERAL_RANGE]


def _linux_reserved_ports() -> list[Interval]:
    try:
        with open(_LINUX_RESERVED_FILE) as f:
            return parse_port_list(f.read())
    except OSError:
        return []


def _bsd_ephemeral_ranges() -> list[Interval]:
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


def parse_bsd_ranges(sysctl_output: str) -> list[Interval]:
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


def parse_port_list(value: str) -> list[Interval]:
    """Parse a kernel port list such as `8080,9148-9150`."""
    intervals = []
    for item in value.strip().split(","):
        if item:
            low, _, high = item.partition("-")
            intervals.append((int(low), int(high or low)))
    return intervals
