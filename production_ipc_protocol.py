"""Bounded Linux Unix seqpacket protocol; mutually authenticated kernel UIDs."""
import ctypes,json,os,socket,stat,struct,sys
from pathlib import Path
from production_owner import unique
from production_pg import Denied
MAX=32768
def encode(obj):
    raw=json.dumps(obj,ensure_ascii=True,separators=(",",":"),allow_nan=False).encode("ascii")
    if not 0<len(raw)<=MAX:raise Denied("ipc_size")
    return raw

def decode(raw):
    if not 0<len(raw)<=MAX:raise Denied("ipc_size")
    def bad(_):raise Denied("ipc_constant")
    result=json.loads(raw.decode("ascii"),object_pairs_hook=unique,parse_constant=bad)
    if type(result) is not dict:raise Denied("ipc_shape")
    return result

def peer(sock):
    pid,uid,gid=struct.unpack("3i",sock.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
    if pid<=0 or uid<0 or gid<0:raise Denied("peer")
    return uid

def recv(sock):
    raw,anc,flags,_=sock.recvmsg(MAX,0)
    if anc or flags&(socket.MSG_TRUNC|socket.MSG_CTRUNC):raise Denied("ipc_truncated")
    return decode(raw)

def send(sock,obj):
    raw=encode(obj)
    if sock.send(raw)!=len(raw):raise Denied("ipc_send")

def request(path,broker_uid,payload,timeout=40):
    """No retries. Both sides verify peer UID before sending credentials."""
    with socket.socket(socket.AF_UNIX,socket.SOCK_SEQPACKET) as s:
        s.settimeout(timeout)
        s.connect(str(path))
        if peer(s)!=broker_uid:raise Denied("broker_identity")
        send(s,payload)
        reply=recv(s)
        if set(reply)!={"ok","value"} or reply["ok"] is not True:raise Denied("ipc_denied")
        return reply["value"]

def private_file(path,limit=32768):
    f=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
    try:
        st=os.fstat(f)
        if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.geteuid() or st.st_nlink!=1 or st.st_mode&0o077:raise Denied("private_file")
        raw=os.read(f,limit+1)
        if len(raw)>limit:raise Denied("private_file_size")
        return raw
    finally:os.close(f)

def private_directory(path):
    if path.resolve(strict=True)!=path:raise Denied("private_path")
    st=path.lstat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid!=os.geteuid() or st.st_mode&0o077:raise Denied("private_directory")

def burn_start(path):
    # Permanent one-process admission receipt. No automatic restart or reset.
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
    try:
        if os.write(fd,b"OFFLINE_BROKER_STARTED\n")!=23:raise Denied("start_receipt")
        os.fsync(fd)
    finally:os.close(fd)
    fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(fd)
    finally:os.close(fd)

def lock_process(uid):
    if sys.platform!="linux" or uid<=0 or os.getuid()!=uid or os.geteuid()!=uid or os.getgid()!=uid or os.getegid()!=uid or os.getgroups():raise Denied("os_identity")
    libc=ctypes.CDLL(None,use_errno=True)
    if libc.prctl(38,1,0,0,0)!=0 or libc.prctl(4,0,0,0,0)!=0:raise Denied("process_hardening")
    state={k:v.strip() for k,v in (line.split(":",1) for line in Path("/proc/self/status").read_text().splitlines() if ":" in line)}
    if state.get("NoNewPrivs")!="1" or any(int(state.get(k,"1"),16) for k in ("CapEff","CapPrm","CapAmb")):raise Denied("process_privilege")
    os.umask(0o077)

def socket_directory(path,uid):
    if path.parent.resolve(strict=True)!=path.parent:raise Denied("socket_path")
    st=path.parent.lstat()
    if st.st_uid!=uid or st.st_mode&0o022 or not stat.S_ISDIR(st.st_mode):raise Denied("socket_directory")
