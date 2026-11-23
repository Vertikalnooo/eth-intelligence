"""Fail-closed outbound socket fence for offline CI only.

Loaded by Python's standard sitecustomize mechanism using the workflow's
explicit PYTHONPATH. Never imported into the live application/deployment.
Allow only loopback TCP/UDP and AF_UNIX (used by local HTTP fixtures).
"""
import ipaddress
import socket

_original_connect = socket.socket.connect
_original_connect_ex = socket.socket.connect_ex
_original_sendto = socket.socket.sendto


def _loopback(address, family):
    if family == socket.AF_UNIX:
        return True
    if family not in (socket.AF_INET, socket.AF_INET6):
        return False
    if type(address) is not tuple or len(address) < 2 or type(address[0]) is not str:
        return False
    host = address[0]
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.split("%", 1)[0]).is_loopback
    except ValueError:
        return False


def _deny_external(sock, address):
    if not _loopback(address, sock.family):
        raise OSError("Stage 2 offline CI forbids external socket traffic")


def _connect(sock, address):
    _deny_external(sock, address)
    return _original_connect(sock, address)


def _connect_ex(sock, address):
    _deny_external(sock, address)
    return _original_connect_ex(sock, address)


def _sendto(sock, data, *args):
    address = args[-1] if args else None
    _deny_external(sock, address)
    return _original_sendto(sock, data, *args)


_connect.__stage2_offline_guard__ = True
socket.socket.connect = _connect
socket.socket.connect_ex = _connect_ex
socket.socket.sendto = _sendto
