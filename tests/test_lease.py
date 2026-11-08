from errno import EACCES, EADDRINUSE, EMFILE
from socket import (
    AF_INET,
    AF_INET6,
    IPPROTO_IPV6,
    IPV6_V6ONLY,
    SO_REUSEADDR,
    SOCK_DGRAM,
    SOCK_STREAM,
    SOL_SOCKET,
    create_connection,
    socket,
)
from subprocess import PIPE, Popen
from sys import executable, platform
from textwrap import dedent

from pytest import mark, param, raises, skip

from pytest_twinport import NoFreePortError, PortLease, _lease, lease_port


def has_ipv6_loopback() -> bool:
    try:
        with socket(AF_INET6) as sock:
            sock.bind(("::1", 0))
    except OSError:
        return False
    return True


HAS_IPV6_LOOPBACK = has_ipv6_loopback()


def bound_socket(family, kind, host, port, *, reuse=False) -> socket:
    sock = socket(family, kind)
    try:
        if family == AF_INET6:
            sock.setsockopt(IPPROTO_IPV6, IPV6_V6ONLY, 1)
        if reuse:
            sock.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
        sock.bind((host, port))
    except OSError:
        sock.close()
        raise
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
    with raises(OSError):
        bound_socket(AF_INET, SOCK_STREAM, "127.0.0.1", guard_port)
    lease.release()
    bound_socket(AF_INET, SOCK_STREAM, "127.0.0.1", guard_port).close()


def test_release_is_idempotent(pair):
    lease = lease_port(port_range=pair)
    lease.release()
    lease.release()


def test_guard_cannot_be_taken_with_so_reuseaddr(pair):
    guard_port, _ = pair
    with lease_port(port_range=pair):
        with raises(OSError):
            bound_socket(AF_INET, SOCK_STREAM, "127.0.0.1", guard_port, reuse=True)


def test_leased_pair_is_not_leased_again(pair):
    with lease_port(port_range=pair):
        with raises(NoFreePortError):
            lease_port(port_range=pair)
    lease_port(port_range=pair).release()


@mark.parametrize("kind", [SOCK_STREAM, SOCK_DGRAM])
@mark.parametrize(
    "family, host",
    [
        (AF_INET, "127.0.0.1"),
        (AF_INET, "0.0.0.0"),
        param(
            AF_INET6,
            "::1",
            marks=mark.skipif(not HAS_IPV6_LOOPBACK, reason="no IPv6"),
        ),
        param(
            AF_INET6,
            "::",
            marks=mark.skipif(not HAS_IPV6_LOOPBACK, reason="no IPv6"),
        ),
    ],
)
def test_leased_port_can_be_bound_without_so_reuseaddr(family, kind, host):
    with lease_port() as port:
        bound_socket(family, kind, host, port).close()


@mark.parametrize("kind", [SOCK_STREAM, SOCK_DGRAM])
@mark.parametrize(
    "family, host",
    [
        (AF_INET, "127.0.0.1"),
        (AF_INET, "0.0.0.0"),
        param(
            AF_INET6,
            "::1",
            marks=mark.skipif(not HAS_IPV6_LOOPBACK, reason="no IPv6"),
        ),
        param(
            AF_INET6,
            "::",
            marks=mark.skipif(not HAS_IPV6_LOOPBACK, reason="no IPv6"),
        ),
    ],
)
def test_pair_with_busy_odd_port_is_skipped(pair, family, kind, host):
    _, port = pair
    with bound_socket(family, kind, host, port):
        with raises(NoFreePortError):
            lease_port(port_range=pair)
    # The failed attempt must not leave the guard behind.
    lease_port(port_range=pair).release()


@mark.parametrize("host", ["127.0.0.1", "0.0.0.0"])
def test_pair_with_busy_guard_is_skipped(pair, host):
    guard_port, _ = pair
    with bound_socket(AF_INET, SOCK_STREAM, host, guard_port):
        with raises(NoFreePortError):
            lease_port(port_range=pair)


def test_port_in_time_wait_is_skipped(pair):
    _, port = pair
    server = bound_socket(AF_INET, SOCK_STREAM, "127.0.0.1", port, reuse=True)
    server.listen(1)
    client = create_connection(("127.0.0.1", port))
    connection, _ = server.accept()
    connection.close()
    client.recv(1)
    client.close()
    server.close()
    try:
        bound_socket(AF_INET, SOCK_STREAM, "127.0.0.1", port).close()
    except OSError:
        pass
    else:
        skip("the kernel did not keep TIME_WAIT, its table is probably full")
    with raises(NoFreePortError):
        lease_port(port_range=pair)


def test_spawned_process_does_not_keep_the_guard(pair):
    lease = lease_port(port_range=pair)
    # close_fds=False, otherwise the child could not inherit the guard anyway.
    child = Popen([executable, "-c", "import sys; sys.stdin.read()"], stdin=PIPE, close_fds=False)
    try:
        lease.release()
        lease_port(port_range=pair).release()
    finally:
        child.communicate()
    assert child.returncode == 0


def test_guard_is_released_when_the_process_is_killed(pair):
    code = dedent(f"""
        from pytest_twinport import lease_port

        lease = lease_port(port_range={pair})
        print("leased", flush=True)
        input()
    """)
    child = Popen(
        [executable, "-c", code],
        stdin=PIPE,
        stdout=PIPE,
        text=True,
    )
    assert child.stdout.readline().strip() == "leased"
    with raises(NoFreePortError):
        lease_port(port_range=pair)
    child.kill()
    child.communicate()
    lease_port(port_range=pair).release()


@mark.skipif(platform != "win32", reason="the option exists only on Windows")
def test_guard_is_exclusive_on_windows():
    from socket import SO_EXCLUSIVEADDRUSE

    lease = lease_port()
    try:
        assert lease._guard.getsockopt(SOL_SOCKET, SO_EXCLUSIVEADDRUSE) != 0
    finally:
        lease.release()


def failing_call(error_number):
    def fail(*args):
        raise OSError(error_number, "Simulated error")

    return fail


@mark.parametrize("error_number", [EADDRINUSE, EACCES])
def test_busy_errors_mean_the_pair_is_skipped(monkeypatch, pair, error_number):
    monkeypatch.setattr(_lease, "socket", failing_call(error_number))
    with raises(NoFreePortError):
        lease_port(port_range=pair)


def test_other_errors_are_not_reported_as_busy_ports(monkeypatch):
    monkeypatch.setattr(_lease, "socket", failing_call(EMFILE))
    with raises(OSError) as exc_info:
        lease_port()
    assert exc_info.value.errno == EMFILE


def test_guard_is_released_when_the_check_of_the_port_fails(monkeypatch, pair):
    with monkeypatch.context() as patch:
        patch.setattr(_lease, "_is_free", failing_call(EMFILE))
        with raises(OSError):
            lease_port(port_range=pair)
    lease_port(port_range=pair).release()


def test_range_without_a_whole_pair_raises_value_error():
    # Port 65535 has no partner, so this range contains no whole pair.
    with raises(ValueError, match="even port and the odd port after it"):
        lease_port(port_range=(65535, 65535))


def test_error_names_the_range_when_all_pairs_are_busy(pair):
    guard_port, port = pair
    with lease_port(port_range=pair):
        with raises(NoFreePortError, match=rf"None of the 1 port pairs in {guard_port}-{port} \(port_range\)"):
            lease_port(port_range=pair)
