#!/usr/bin/env python3
"""Measure port collisions between processes allocating ports in parallel.

Every worker repeatedly gets a port, waits a moment like a starting server,
binds the port without SO_REUSEADDR, serves one connection and shuts down.
A collision is a bind that fails with EADDRINUSE.

Modes:
  twinport  lease_port() from this project
  probe     random port from the same range, checked by bind and close
  bind0     port assigned by the kernel with bind(0), then closed
"""

from __future__ import annotations

import argparse
import errno
import json
import multiprocessing
import random
import socket
import struct
import time
from collections.abc import Iterator
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager

from pytest_twinport import lease_port

HOST = "127.0.0.1"
START_DELAY_SECONDS = 2.0
MAX_STARTUP_SECONDS = 0.002
RESET_ON_CLOSE = struct.pack("ii", 1, 0)


@contextmanager
def twinport_port(port_range: tuple[int, int]) -> Iterator[int]:
    with lease_port(port_range=port_range) as port:
        yield port


@contextmanager
def probe_port(port_range: tuple[int, int]) -> Iterator[int]:
    while True:
        port = random.randint(*port_range)
        with socket.socket() as sock:
            try:
                sock.bind((HOST, port))
            except OSError:
                continue
        yield port
        return


@contextmanager
def bind0_port(port_range: tuple[int, int]) -> Iterator[int]:
    with socket.socket() as sock:
        sock.bind((HOST, 0))
        port = sock.getsockname()[1]
    yield port


MODES = {"twinport": twinport_port, "probe": probe_port, "bind0": bind0_port}


def serve_once(port: int) -> None:
    with socket.socket() as server:
        server.bind((HOST, port))
        server.listen(1)
        client = socket.create_connection((HOST, port))
        connection, _ = server.accept()
        # Reset the connection: a run would otherwise leave tens of thousands of
        # sockets in TIME_WAIT and overflow the kernel table.
        client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, RESET_ON_CLOSE)
        client.close()
        connection.close()


def worker(
    mode: str, port_range: tuple[int, int], iterations: int, start_at: float
) -> int:
    time.sleep(max(0.0, start_at - time.time()))
    collisions = 0
    for _ in range(iterations):
        with MODES[mode](port_range) as port:
            time.sleep(random.uniform(0, MAX_STARTUP_SECONDS))
            try:
                serve_once(port)
            except OSError as exc:
                if exc.errno != errno.EADDRINUSE:
                    raise
                collisions += 1
    return collisions


def run(
    mode: str, workers: int, iterations: int, port_range: tuple[int, int]
) -> dict[str, object]:
    start_at = time.time() + START_DELAY_SECONDS
    context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(workers, mp_context=context) as pool:
        futures = [
            pool.submit(worker, mode, port_range, iterations, start_at)
            for _ in range(workers)
        ]
        collisions = sum(future.result() for future in futures)
    return {
        "mode": mode,
        "workers": workers,
        "allocations": workers * iterations,
        "collisions": collisions,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=[*MODES, "all"], default="all")
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--iterations", type=int, default=500)
    parser.add_argument("--low", type=int, default=21000)
    parser.add_argument("--high", type=int, default=21199)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    modes = list(MODES) if args.mode == "all" else [args.mode]
    for mode in modes:
        result = run(mode, args.workers, args.iterations, (args.low, args.high))
        print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
