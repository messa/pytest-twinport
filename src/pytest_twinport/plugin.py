"""Pytest fixtures, registered through the `pytest11` entry point."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import ExitStack

import pytest

from ._lease import lease_port


@pytest.fixture
def twinport() -> Iterator[int]:
    """A free TCP/UDP port, reserved until the end of the test."""
    with lease_port() as port:
        yield port


@pytest.fixture
def twinport_factory() -> Iterator[Callable[[], int]]:
    """A callable returning a new free port, all reserved until the end of the test."""
    with ExitStack() as stack:
        yield lambda: stack.enter_context(lease_port())
