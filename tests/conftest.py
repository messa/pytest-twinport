from pytest import fixture


@fixture(autouse=True)
def no_range_from_environment(monkeypatch):
    """Keep a TWINPORT_RANGE set in the shell from changing what the tests check."""
    monkeypatch.delenv("TWINPORT_RANGE", raising=False)
