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

The lock lives in the network namespace of the process that holds it, so it only
protects ports used in that same namespace. See the note on containers below.

Ports are only chosen outside the ephemeral range. This matters: outgoing
connections take their source port from that range at any moment, and they know
nothing about guards. Linux, macOS and BSD report the range, Windows uses a fixed
assumption (see [Platform support](#platform-support)). On Linux, ports listed in
`ip_local_reserved_ports` are skipped too.

## Guarantees

| Scenario | Protected? |
| --- | --- |
| Tests in one `pytest-xdist` run | Yes |
| Separate pytest runs in one network namespace | Yes |
| Pytest runs in separate network namespaces publishing ports to one host | No, see below |
| Outgoing connections from anything on the machine | Yes, leased ports are outside the ephemeral range |
| Server restarting during a test | Yes, nobody else gets the port |
| Crashed or killed pytest process | Yes, the kernel releases the port |
| Software using a fixed port in the same range | No, the port is only checked when it is leased |
| Port allocation by other tools | No, every allocator has to use guards |

The port is free on TCP and UDP, on IPv4 and IPv6, and the first bind works
without `SO_REUSEADDR`. A server that restarts needs `SO_REUSEADDR` as usual,
because its old connections can keep the port in `TIME_WAIT`. Every lease uses two
port numbers.

A process created with `fork()` without `exec()` inherits the guard, and the
port stays blocked until that process ends too. This can last for a whole test
session, but can't cause a collision.

Containers with their own network stack have their own ports, so tests running
inside them don't collide. That changes once they publish ports to a shared host,
for example when pytest runs in a container and starts other containers with
`docker run -p PORT:80` through a shared Docker socket. The guard is then in a
different namespace than the published port, so two such runs can get the same
port. To avoid this, run pytest with `--network host`, give every run its own
`TWINPORT_RANGE`, or let Docker choose the port with `-p 80` and read it with
`docker port`.

## Configuration

Ports are chosen from 15000-65535, minus the ephemeral range. To change this:

```bash
export TWINPORT_RANGE=20000-30000
```

`lease_port(port_range=(20000, 30000))` does the same for a single lease.
Ephemeral ports are left out in both cases. If no pair can be leased,
`NoFreePortError` is raised. This also happens when the ephemeral range of the
system covers nearly all ports, and then the only fix is to make it smaller.

## Measurements

`scripts/stress.py` runs parallel processes that repeatedly get a port, wait up
to 2 ms like a starting server, then bind the port without `SO_REUSEADDR`. It
counts failed binds. These are the results of three runs. Each run used 16
processes, 8000 allocations in total and a range of 200 ports (Linux, 6 cores):

| Method | Collisions |
| --- | --- |
| `lease_port()` | 0, 0, 0 |
| Random port checked by bind and close | 15, 16, 15 |
| `bind(0)` and close | 1, 0, 0 |

`bind(0)` is not limited to the small range, so it collides less often in this
test. An additional run of `lease_port()` with 32 processes and 64000
allocations had 0 collisions too.

## Platform support

The tests pass on Linux, macOS and Windows. BSD systems have not been tried out.

There are two limits on Windows:

- A bind to a wildcard address does not conflict with a bind to a specific
  address. Ports are checked on wildcard and loopback addresses, so a service
  listening only on another address of the machine is not detected.
- The ephemeral range is not read from the system. Ports from 32768 up are left
  out, which covers the default range. If the range was changed with `netsh` to
  start lower, set `TWINPORT_RANGE` to stay out of it.

## Development

```bash
uv sync
uv run pytest
uv run ruff check
uv run ruff format
uv run mypy
uv run scripts/stress.py
```

## Releasing

Set the new version in `pyproject.toml`, move the changes from Unreleased in the
[changelog](#changelog) to a new section, and commit it. Publishing a GitHub
release then runs the tests, builds the package and uploads it to PyPI. The
release fails if its tag does not match the version:

```bash
gh release create v0.1.0 --generate-notes
```

## Related projects

These get a free port for a moment and don't reserve it, so they have the race
condition described above:

- [pytest-asyncio](https://pypi.org/project/pytest-asyncio/) has the
  `unused_tcp_port` and `unused_udp_port` fixtures and their factories, based on
  `bind(0)` and close.
- [anyio](https://pypi.org/project/anyio/) has the `free_tcp_port` and
  `free_udp_port` fixtures and their factories. They work the same way, and the
  TCP and UDP ports are checked on both IPv4 and IPv6.
- [pytest-unused-port](https://pypi.org/project/pytest-unused-port/) has an
  `unused_port` fixture based on `bind(0)` and close.
- [pytest-server-fixtures](https://pypi.org/project/pytest-server-fixtures/)
  picks a random port below the usual ephemeral range, on a random loopback
  address.

These coordinate between processes in other ways:

- [portpicker](https://pypi.org/project/portpicker/) by Google, which inspired
  this project, solves the race with an optional `portserver` daemon.
  `pytest-twinport` is not an integration of that library.
- [pytest-services](https://pypi.org/project/pytest-services/) has a
  `port_getter` fixture that keeps the list of used ports in a shared lock file.
- [pytest-postgresql](https://pypi.org/project/pytest-postgresql/) marks its
  port with a file in the temporary directory of the test run, which coordinates
  `pytest-xdist` workers.
- [portkeeper](https://pypi.org/project/portkeeper/) keeps reservations in a JSON
  registry protected by a file lock.

These solve neighboring problems:

- [port-for](https://pypi.org/project/port-for/) manages persistent port
  assignments. It avoids the ephemeral range as well.
- [ephemeral-port-reserve](https://pypi.org/project/ephemeral-port-reserve/)
  forces an ephemeral port into the `TIME_WAIT` state before releasing it.
- [portlock](https://pypi.org/project/portlock/) uses a bound port to make sure
  that only one instance of a script is running.
- [pytest-localserver](https://pypi.org/project/pytest-localserver/) provides
  test servers that bind to port 0 themselves, so they don't need a free port up
  front.

## Changelog

### Unreleased

- Nothing yet.

### 0.1.0 (2026-11-07)

- Initial release.

## License

MIT
