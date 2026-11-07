# pytest-twinport

Pytest plugin that gives your tests a free TCP/UDP port, without collisions
between tests running in parallel.

The usual recipe, `bind(0)`, read the port number, close the socket, has a race
condition: between closing the socket and starting your server, anyone else can
get the same port. With many E2E tests running at once, this shows up as random
`Address already in use` failures.

`pytest-twinport` keeps the port reserved for the whole test, across processes
and across separate pytest runs. It needs no daemon and no lock files.

## Installation

```bash
pip install pytest-twinport
```

## Usage

Use the `twinport` fixture to get a port reserved until the end of the test:

```python
def test_server(twinport):
    server = start_server(port=twinport)
    ...
```

Use `twinport_factory` when a test needs more ports:

```python
def test_cluster(twinport_factory):
    ports = [twinport_factory() for _ in range(3)]
    ...
```

Use `lease_port()` for any other fixture scope, or outside of fixtures. Make the
server fixture depend on the lease, so that the server stops first:

```python
import pytest
from pytest_twinport import lease_port


@pytest.fixture(scope="session")
def server():
    with lease_port() as port:
        process = start_server(port=port)
        yield process
        process.stop()
```

`lease_port()` can also be used without `with`. Keep the returned object for as
long as you need the port, because a garbage collected lease is released:

```python
lease = lease_port()
start_server(port=lease.port)
...
lease.release()
```

If your server can bind to port 0 and report the port it got, you don't need
this plugin. It is meant for servers that must be told their port up front, such
as external processes, Docker containers and services configured by a file.

## How it works

Ports are split into fixed pairs: an even _guard_ port and the odd port after it.

1. Pick a random pair and bind the guard port. `bind()` is atomic, so only one
   process can succeed.
2. Check that the odd port is free.
3. Give the odd port to the caller and keep the guard bound.

Other processes can't take the pair, because they fail on the guard. When the
process ends, even with `SIGKILL`, the kernel closes the guard. Nothing stale is
left behind.

The lock lives in the same network namespace as the port. Containers with their
own network stack don't block each other, and they don't need to, because their
ports don't collide either.

Ports are only chosen outside the ephemeral range. This matters: outgoing
connections take their source port from that range at any moment, and they know
nothing about guards. The range is read from the system, and on Linux, ports
listed in `ip_local_reserved_ports` are skipped too.

## Guarantees

| Scenario | Protected? |
| --- | --- |
| Tests in one `pytest-xdist` run | Yes |
| Separate pytest runs on one machine | Yes |
| Outgoing connections from anything on the machine | Yes, leased ports are outside the ephemeral range |
| Server restarting during a test | Yes, the port stays reserved |
| Crashed or killed pytest process | Yes, the kernel releases the port |
| Software using a fixed port in the same range | No, the port is only checked when it is leased |
| Port allocation by other tools | No, every allocator has to use guards |

The port is free on TCP and UDP, on IPv4 and IPv6, and it can be bound without
`SO_REUSEADDR`. Every lease uses two port numbers.

A process created with `fork()` without `exec()` inherits the guard, and the
port stays blocked until that process ends too. This wastes the port for a
moment, but can't cause a collision.

## Configuration

Ports are chosen from 15000-65535, minus the ephemeral range. To change this:

```bash
export TWINPORT_RANGE=20000-30000
```

`lease_port(port_range=(20000, 30000))` does the same for a single lease.
Ephemeral ports are left out in both cases. If a port range turns out to be
empty or full, `NoFreePortError` is raised.

## Measurements

`scripts/stress.py` runs parallel processes that repeatedly get a port, wait up
to 2 ms like a starting server, then bind the port without `SO_REUSEADDR`. It
counts failed binds. The results of three runs with 16 processes, 8000
allocations each, and a range of 200 ports (Linux, 6 cores):

| Method | Collisions |
| --- | --- |
| `lease_port()` | 0, 0, 0 |
| Random port checked by bind and close | 15, 16, 15 |
| `bind(0)` and close | 1, 0, 0 |

`bind(0)` is not limited to the small range, so it collides less often in this
test. An additional run of `lease_port()` with 32 processes and 64000
allocations had 0 collisions too.

## Platform support

This version is tested on Linux only. The code handles macOS and BSD as well,
but that has not been tried out yet, and neither has Windows.

## Development

```bash
uv sync
uv run pytest
uv run scripts/stress.py
```

## Releasing

Set the new version in `pyproject.toml` and commit it. Publishing a GitHub
release then runs the tests, builds the package and uploads it to PyPI. The
release fails if its tag does not match the version:

```bash
gh release create v0.1.0 --generate-notes
```

## Related projects

- [portpicker](https://pypi.org/project/portpicker/) by Google, which inspired
  this project, solves the race with an optional `portserver` daemon.
  `pytest-twinport` is not an integration of that library.
- [port-for](https://pypi.org/project/port-for/) manages persistent port
  assignments. It avoids the ephemeral range as well.

## License

MIT
