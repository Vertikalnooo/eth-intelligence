"""Linux offline network-call supervisor. Timeout is UNKNOWN, never retry.
Child result is bounded; only JSON scalar/dict/list, no pickle from child.
A remote commit can still complete after a local timeout.
"""
import json
import os
import select
import signal
import sys
import time
from production_pg import Denied
from rest_one_shot_worker import arm

def bounded(fn,*args,seconds=8):
    if sys.platform!="linux" or type(seconds) not in (int,float) or not 0<seconds<=8:raise Denied("deadline_configuration")
    parent=os.getpid();deadline=time.monotonic_ns()+int(seconds*1e9)
    rd,wr=os.pipe()
    try:pid=os.fork()
    except BaseException:os.close(rd);os.close(wr);raise
    if pid==0:
        os.close(rd)
        try:
            arm(parent,deadline)
            raw=json.dumps({"ok":True,"value":fn(*args)},separators=(",",":")).encode()
            if len(raw)>16384:raise RuntimeError("result_size")
            while raw:
                n=os.write(wr,raw);raw=raw[n:]
            os._exit(0)
        except BaseException:os._exit(2)
    os.close(wr);data=bytearray();reaped=False
    try:
        while True:
            remaining=(deadline-time.monotonic_ns())/1e9
            if remaining<=0 or not select.select([rd],[],[],remaining)[0]:raise Denied("network_deadline_unknown")
            chunk=os.read(rd,4096)
            if not chunk:break
            data.extend(chunk)
            if len(data)>16384:raise Denied("network_result_size")
        while True:
            waited,status=os.waitpid(pid,os.WNOHANG)
            if waited:reaped=True;break
            if time.monotonic_ns()>=deadline:raise Denied("network_deadline_unknown")
            time.sleep(.001)
        if status!=0 or time.monotonic_ns()>=deadline:raise Denied("network_unconfirmed")
        obj=json.loads(data)
        if type(obj) is not dict or set(obj)!={"ok","value"} or obj["ok"] is not True:raise Denied("network_result")
        return obj["value"]
    finally:
        os.close(rd)
        if not reaped:
            try:os.kill(pid,signal.SIGKILL)
            except ProcessLookupError:pass
            # Bounded reap; never wait indefinitely on a stuck kernel task.
            until=time.monotonic()+.25
            while time.monotonic()<until:
                try:
                    if os.waitpid(pid,os.WNOHANG)[0]:break
                except ChildProcessError:break
                time.sleep(.002)
