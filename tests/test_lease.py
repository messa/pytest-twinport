import socket
import subprocess
import sys

import pytest

from pytest_twinport import NoFreePortError, PortLease, lease_port


def has_ipv6_loopback() -> bool:
    try:
        with socket.socket(socket.AF_INET6) as sock:
            sock.bind(("::1", 0))
    except OSError:
        return False
    return True


HAS_IPV6_LOOPBACK = has_ipv6_loopback()


@pytest.fixture
def pair() -> tuple[int, int]:
    """A currently free pair, to be used through a one-pair `port_range`."""
    lease = lease_port()
    lease.release()
    return lease.guard_port, lease.port


def bound_socket(family, kind, host, port, *, reuse=False) -> socket.socket:
    sock = socket.socket(family, kind)
    if family == socket.AF_INET6:
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
    if reuse:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    return sock


def test_returns_odd_port_guarded_by_the_even_one():
    with lease_port() as port:
        assert port % 2 == 1


def test_lease_exposes_port_and_guard():
    lease = lease_port()
    try:
        assert isinstance(lease, PortLease)
        assert lease.guard_port == lease.port - 1
        assert repr(lease) == f"PortLease(port={lease.port})"
    finally:
        lease.release()


def test_guard_is_held_until_release(pair):
    guard_port, _ = pair
    lease = lease_port(port_range=pair)
    with pytest.raises(OSError):
        bound_socket(socket.AF_INET, socket.SOCK_STREAM, "127.0.0.1", guard_port)
    lease.release()
    bound_socket(socket.AF_INET, socket.SOCK_STREAM, "127.0.0.1", guard_port).close()


def test_release_is_idempotent(pair):
    lease = lease_port(port_range=pair)
    lease.release()
    lease.release()


def test_guard_cannot_be_taken_with_so_reuseaddr(pair):
    guard_port, _ = pair
    with lease_port(port_range=pair):
        with pytest.raises(OSError):
            bound_socket(
                socket.AF_INET, socket.SOCK_STREAM, "127.0.0.1", guard_port, reuse=True
            )


def test_leased_pair_is_not_leased_again(pair):
    with lease_port(port_range=pair):
        with pytest.raises(NoFreePortError):
            lease_port(port_range=pair)
    lease_port(port_range=pair).release()


@pytest.mark.parametrize("kind", [socket.SOCK_STREAM, socket.SOCK_DGRAM])
@pytest.mark.parametrize(
    "family, host",
    [
        (socket.AF_INET, "127.0.0.1"),
        (socket.AF_INET, "0.0.0.0"),
        pytest.param(
            socket.AF_INET6,
            "::1",
            marks=pytest.mark.skipif(not HAS_IPV6_LOOPBACK, reason="no IPv6"),
        ),
        pytest.param(
            socket.AF_INET6,
            "::",
            marks=pytest.mark.skipif(not HAS_IPV6_LOOPBACK, reason="no IPv6"),
        ),
    ],
)
def test_leased_port_can_be_bound_without_so_reuseaddr(family, kind, host):
    with lease_port() as port:
        bound_socket(family, kind, host, port).close()


@pytest.mark.parametrize("kind", [socket.SOCK_STREAM, socket.SOCK_DGRAM])
@pytest.mark.parametrize(
    "family, host",
    [
        (socket.AF_INET, "127.0.0.1"),
        pytest.param(
            socket.AF_INET6,
            "::1",
            marks=pytest.mark.skipif(not HAS_IPV6_LOOPBACK, reason="no IPv6"),
        ),
    ],
)
def test_pair_with_busy_odd_port_is_skipped(pair, family, kind, host):
    _, port = pair
    with bound_socket(family, kind, host, port):
        with pytest.raises(NoFreePortError):
            lease_port(port_range=pair)
    # The failed attempt must not leave the guard behind.
    lease_port(port_range=pair).release()


def test_pair_with_busy_guard_is_skipped(pair):
    guard_port, _ = pair
    with bound_socket(socket.AF_INET, socket.SOCK_STREAM, "127.0.0.1", guard_port):
        with pytest.raises(NoFreePortError):
            lease_port(port_range=pair)


def test_port_in_time_wait_is_skipped(pair):
    _, port = pair
    server = bound_socket(
        socket.AF_INET, socket.SOCK_STREAM, "127.0.0.1", port, reuse=True
    )
    server.listen(1)
    client = socket.create_connection(("127.0.0.1", port))
    connection, _ = server.accept()
    connection.close()
    client.recv(1)
    client.close()
    server.close()
    try:
        bound_socket(socket.AF_INET, socket.SOCK_STREAM, "127.0.0.1", port).close()
    except OSError:
        pass
    else:
        pytest.skip("the kernel did not keep TIME_WAIT, its table is probably full")
    with pytest.raises(NoFreePortError):
        lease_port(port_range=pair)


def test_spawned_process_does_not_keep_the_guard(pair):
    lease = lease_port(port_range=pair)
    child = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE
    )
    try:
        lease.release()
        lease_port(port_range=pair).release()
    finally:
        child.communicate()


def test_guard_is_released_when_the_process_is_killed(pair):
    low, high = pair
    code = (
        "from pytest_twinport import lease_port\n"
        f"lease = lease_port(port_range=({low}, {high}))\n"
        "print('leased', flush=True)\n"
        "input()\n"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", code],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert child.stdout.readline().strip() == "leased"
    with pytest.raises(NoFreePortError):
        lease_port(port_range=pair)
    child.kill()
    child.communicate()
    lease_port(port_range=pair).release()


def test_empty_range_raises_no_free_port_error():
    # Port 65535 has no partner, so this range contains no whole pair.
    with pytest.raises(NoFreePortError):
        lease_port(port_range=(65535, 65535))
