def test_twinport_fixture_holds_the_port_during_the_test(pytester):
    pytester.makepyfile(
        """
        from socket import socket

        from pytest import raises


        def test_port(twinport):
            assert twinport % 2 == 1
            with raises(OSError):
                socket().bind(("127.0.0.1", twinport - 1))
            with socket() as server:
                server.bind(("127.0.0.1", twinport))
        """
    )
    pytester.runpytest().assert_outcomes(passed=1)


def test_twinport_fixture_releases_the_port_after_the_test(pytester):
    pytester.makepyfile(
        """
        from socket import socket

        guards = []


        def test_first(twinport):
            guards.append(twinport - 1)


        def test_second():
            with socket() as sock:
                sock.bind(("127.0.0.1", guards[0]))
        """
    )
    pytester.runpytest("-p", "no:randomly").assert_outcomes(passed=2)


def test_factory_returns_different_ports_and_releases_them(pytester):
    pytester.makepyfile(
        """
        from socket import socket

        ports = []


        def test_factory(twinport_factory):
            ports.extend(twinport_factory() for _ in range(20))
            assert len(set(ports)) == 20


        def test_released():
            for port in ports:
                with socket() as sock:
                    sock.bind(("127.0.0.1", port - 1))
        """
    )
    pytester.runpytest("-p", "no:randomly").assert_outcomes(passed=2)


def test_lease_port_supports_custom_fixture_scopes(pytester):
    pytester.makepyfile(
        """
        from pytest import fixture

        from pytest_twinport import lease_port


        @fixture(scope="session")
        def server_port():
            with lease_port() as port:
                yield port


        def test_one(server_port):
            assert server_port


        def test_two(server_port):
            assert server_port
        """
    )
    pytester.runpytest().assert_outcomes(passed=2)
