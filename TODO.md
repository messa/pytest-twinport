# TODO

Ideas and known gaps, mostly from the review before v0.1.0. None of them blocks
normal use.

## Library

- Treat only `EADDRINUSE` and `EACCES` as a busy port, and let other errors
  through. Running out of file descriptors now ends with a misleading
  `NoFreePortError`. Check how these errors map on Windows first.
- Raise an error when a released lease is used in `with`. It now returns the
  port with no protection.
- Reject an empty `port_range=()`, which silently uses the default range.
- Add a way to use ephemeral ports on systems where that range covers nearly all
  ports, because nothing can be leased there.
- Consider handing over the bound socket (`lease.socket`) for servers running in
  the test process. It would have no gap at all, even against other software.

## Windows

- Set `SO_EXCLUSIVEADDRUSE` on the guard.
- Read the ephemeral range with `netsh int ipv4 show dynamicport tcp` and `udp`,
  parsing only the numbers, because the output is localized.
- Find out whether the guard needs `listen()`. Without it, clients that hit the
  guard get refused instead of hanging. The tests pass without it on Linux.

## Tests

- Run the tests with `pytest-xdist` in CI, and add a test using `-n`. The README
  promises this works, but only manual runs cover it.
- Test that ports from `ip_local_reserved_ports` are skipped.
- Test that `with` releases the lease while a reference to it still exists.
- Test that a missing IPv6 is tolerated.
- Test that seeding the global random generator does not affect leases.
- Make tests independent of `TWINPORT_RANGE` set in the environment.
- Don't assume that ports 20000 and 21000 are outside the ephemeral range, in the
  tests and in the default range of `scripts/stress.py`.
- Set `filterwarnings = error` to catch unclosed sockets.
- Run mypy on the tests and scripts too.

## CI and release

- Build the wheel in CI and run a smoke test from it. The tests now use an
  editable install, so packaging errors show up only at release.
- Commit `uv.lock`, pin `hatchling` and the `uv` version, and add the `uv`
  ecosystem to Dependabot. A new ruff or mypy can now block a release with no
  change in the code.
- Test the lowest supported pytest on the newest Python too.
- Limit the `pypi` environment to `v*` tags, and check that the tag is on `main`.
- Set `enable-cache: false` in the release build job.

## Packaging

- Add classifiers for the supported Python versions.
