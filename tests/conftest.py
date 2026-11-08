from pytest import fixture

from pytest_twinport import lease_port


@fixture(autouse=True)
def no_range_from_environment(monkeypatch):
    """Keep a TWINPORT_RANGE set in the shell from changing what the tests check."""
    monkeypatch.delenv("TWINPORT_RANGE", raising=False)


@fixture
def pair() -> tuple[int, int]:
    """A currently free pair, to be used through a one-pair `port_range`."""
    lease = lease_port()
    lease.release()
    return lease.guard_port, lease.port
