"""Offline-only TLS worker, fixed IP, explicit local test CA. No live host."""
import http.client
import json
import os
from pathlib import Path
import ssl
import sys
import time

from rest_one_shot import _exchange_once
from rest_one_shot_worker import arm

def main():
    if len(sys.argv)!=5: return 2
    try:
        parent,deadline,port=map(int,sys.argv[1:4])
        ca=Path(sys.argv[4])
        if not 0<port<=65535 or not ca.is_absolute() or not ca.is_file(): return 2
        arm(parent,deadline)
        ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.minimum_version=ssl.TLSVersion.TLSv1_2
        ctx.load_verify_locations(cafile=str(ca))
        market=_exchange_once(http.client.HTTPSConnection("127.0.0.1",port,timeout=8,context=ctx))
        if time.monotonic_ns()>=deadline: return 2
        print(json.dumps({"mark_price":market["mark_price"]}),flush=True)
        return 0
    except Exception:
        return 2
if __name__=="__main__":
    sys.exit(main())
