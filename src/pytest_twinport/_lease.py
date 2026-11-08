"""Port leases protected by a guard port held open in the kernel.

Ports are split into fixed pairs: an even guard port and the odd port after it.
Binding the guard is the only atomic step, so it acts as a mutex for the pair:
whoever holds it owns the odd port, and the kernel releases it even if the
process is killed.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from errno import EACCES, EADDRINUSE, EADDRNOTAVAIL, EAFNOSUPPORT
from math import gcd
from random import SystemRandom
from socket import (
    AF_INET,
    AF_INET6,
    IPPROTO_IPV6,
    IPV6_V6ONLY,
    SOCK_DGRAM,
    SOCK_STREAM,
    has_ipv6,
    socket,
)
from types import TracebackType

from ._ranges import Candidates, candidate_guards


if sys.platform == "win32":
    from socket import SO_EXCLUSIVEADDRUSE, SOL_SOCKET


GUARD_HOST = "127.0.0.1"

# EACCES covers privileged ports and the port ranges excluded on Windows.
_BUSY_ERRORS = {EADDRINUSE, EACCES}
_IPV6_UNAVAILABLE = {EAFNOSUPPORT, EADDRNOTAVAIL}

# Wildcard and specific addresses conflict on Linux and macOS, but not on
# Windows, so both have to be checked.
_PROBE_ADDRESSES = [(AF_INET, "0.0.0.0"), (AF_INET, "127.0.0.1")]
if has_ipv6:
    _PROBE_ADDRESSES += [(AF_INET6, "::"), (AF_INET6, "::1")]

# Private generator: tests often seed the global one, and workers sharing a seed
# would all fight over the same pairs.
_rng = SystemRandom()


class NoFreePortError(RuntimeError):
    """No pair in the configured range is free."""


class PortLease:
    """A leased port, reserved until `release()` is called.

    Keep a reference for as long as the port is in use: a lease that gets
    garbage collected loses its guard. Used as a context manager, it yields the
    port number and releases it on exit.
    """

    def __init__(self, port: int, guard: socket) -> None:
        self.port = port
        self._guard = guard

    @property
    def guard_port(self) -> int:
        return self.port - 1

    def release(self) -> None:
        """Give the port back. Stop anything using it first."""
        self._guard.close()

    def __enter__(self) -> int:
        return self.port

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()

    def __repr__(self) -> str:
        return f"PortLease(port={self.port})"


def lease_port(*, port_range: tuple[int, int] | None = None) -> PortLease:
    """Lease a port that is free for both TCP and UDP, on IPv4 and IPv6.

    `port_range` is an inclusive `(low, high)` tuple of ints, which must contain
    at least one even port and the odd port after it. It defaults to the
    `TWINPORT_RANGE` environment variable, then to 15000-65535 without
    30000-32767. Ephemeral ports, and on Linux reserved ports, are always left
    out.
    """
    guards = candidate_guards(port_range)
    assert isinstance(guards, Candidates)
    if not guards:
        raise NoFreePortError(
            f"The port range {guards.describe_range()} has no pair outside the excluded ports: "
            f"{guards.describe_exclusions()}."
        )
    for index in _random_order(len(guards)):
        lease = _try_lease(guards[index])
        if lease is not None:
            return lease
    raise NoFreePortError(f"None of the {len(guards)} port pairs in {guards.describe_range()} could be leased.")


def _random_order(count: int) -> Iterator[int]:
    """Yield every index below `count` once, from a random start with a random stride.

    A stride coprime to `count` visits every index and spreads the attempts over
    the range. A linear scan would hand out the pairs right after the leased
    ones, and the leases would pile up into runs that later scans have to walk
    through. This is not a uniformly random permutation, it doesn't need to be.
    """
    start = _rng.randrange(count)
    step = 1
    if count > 1:
        step = _rng.randrange(1, count)
        while gcd(step, count) != 1:
            step = _rng.randrange(1, count)
    for i in range(count):
        yield (start + i * step) % count


def _try_lease(guard_port: int) -> PortLease | None:
    guard = _bind_guard(guard_port)
    if guard is None:
        return None
    # The odd port is only ever probed by the guard holder, so a probe can
    # neither race another allocator nor disturb a server using a leased port.
    port = guard_port + 1
    try:
        if _is_free(port):
            return PortLease(port, guard)
    except OSError:
        guard.close()
        raise
    guard.close()
    return None


def _bind_guard(port: int) -> socket | None:
    # On Windows, a guard on loopback would succeed next to a server on the
    # wildcard address and take over its local connections.
    if not _can_bind(AF_INET, SOCK_STREAM, "0.0.0.0", port):
        return None
    # No SO_REUSEADDR, so that a second bind of this port fails. listen() makes
    # the port look taken to tools that check it by connecting.
    guard = socket(AF_INET, SOCK_STREAM)
    try:
        if sys.platform == "win32":
            # Windows otherwise lets a socket with SO_REUSEADDR take the port over.
            guard.setsockopt(SOL_SOCKET, SO_EXCLUSIVEADDRUSE, 1)
        guard.bind((GUARD_HOST, port))
        guard.listen(1)
    except OSError as exc:
        guard.close()
        if exc.errno in _BUSY_ERRORS:
            return None
        raise
    return guard


def _is_free(port: int) -> bool:
    # IPv4 and IPv6 sockets do not conflict with each other, nor do TCP and UDP.
    # No SO_REUSEADDR, so that ports in TIME_WAIT count as busy and the result
    # also holds for servers that do not set it.
    return all(
        _can_bind(family, kind, host, port) for kind in (SOCK_STREAM, SOCK_DGRAM) for family, host in _PROBE_ADDRESSES
    )


def _can_bind(family: int, kind: int, host: str, port: int) -> bool:
    try:
        with socket(family, kind) as sock:
            if family == AF_INET6:
                sock.setsockopt(IPPROTO_IPV6, IPV6_V6ONLY, 1)
            sock.bind((host, port))
    except OSError as exc:
        if exc.errno in _BUSY_ERRORS:
            return False
        if family == AF_INET6 and exc.errno in _IPV6_UNAVAILABLE:
            return True
        raise
    return True
