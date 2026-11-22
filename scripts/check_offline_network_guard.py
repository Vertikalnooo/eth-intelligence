"""Executable proof that the offline Python startup fence was loaded."""
import socket

assert getattr(socket.socket.connect, "__stage2_offline_guard__", False), (
    "Stage 2 CI must refuse to run tests without its offline socket fence"
)
for fn in ("connect", "connect_ex"):
    with socket.socket(socket.AF_INET) as client:
        try:
            getattr(client, fn)(("203.0.113.7", 443))
        except OSError as exc:
            assert "offline CI forbids external" in str(exc), repr(exc)
        else:
            raise AssertionError("External network connection was not blocked")
with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
    try:
        client.sendto(b"no", ("198.51.100.1", 443))
    except OSError as exc:
        assert "offline CI forbids external" in str(exc), repr(exc)
    else:
        raise AssertionError("External UDP traffic was not blocked")
with socket.socket(socket.AF_INET) as server:
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    with socket.socket(socket.AF_INET) as client:
        client.connect(server.getsockname())
        peer, _ = server.accept()
        peer.close()
print("Offline CI: external TCP/UDP blocked; loopback allowed")
