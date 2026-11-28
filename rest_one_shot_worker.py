"""Private Linux loopback fixture. Never accepts a remote host or live grant."""
import ctypes
import http.client
import json
import os
import signal
import sys
import time

from rest_one_shot import _exchange_once


def arm(parent,deadline):
    if sys.platform != "linux" or parent <= 1 or os.getppid()!=parent:
        raise RuntimeError("parent_missing")
    libc=ctypes.CDLL(None,use_errno=True)
    # PR_SET_PDEATHSIG=1. Recheck parent to close the fork/exec race.
    if libc.prctl(1,signal.SIGKILL,0,0,0)!=0 or os.getppid()!=parent:
        raise RuntimeError("parent_guard_failed")
    remaining=(deadline-time.monotonic_ns())/1e9
    if not 0 < remaining <= 8:
        raise RuntimeError("expired_deadline")
    signal.signal(signal.SIGALRM,signal.SIG_DFL)
    signal.setitimer(signal.ITIMER_REAL,remaining)


def main():
    if len(sys.argv)!=4:
        return 2
    try:
        parent,deadline,port=map(int,sys.argv[1:])
        if not 0 < port <= 65535:
            return 2
        arm(parent,deadline)
        market=_exchange_once(http.client.HTTPConnection("127.0.0.1",port,timeout=8))
        if time.monotonic_ns() >= deadline:
            return 2
        print(json.dumps({"mark_price":market["mark_price"],
                          "received_ns":time.monotonic_ns()}),flush=True)
        return 0
    except Exception:
        return 2


if __name__=="__main__":
    sys.exit(main())
