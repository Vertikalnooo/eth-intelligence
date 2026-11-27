"""Local-only durable credential history. Never auto-enroll or adopt disk state."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import threading
from production_passkey import Credential,Ceremonies,_valid
from production_pg import Denied
from production_recovery import valid

def encode(c,profile):
    if not _valid(c) or profile not in ("offline","safari-preparation"):raise Denied("credential_invalid")
    return json.dumps({"profile":profile,"id":c.id.hex(),"key":c.public_key.hex(),"count":c.count},separators=(",",":")).encode()
def digest(raw):return hashlib.sha256(raw).hexdigest()
def fingerprint(c,profile):
    return digest(encode(Credential(c.id,c.public_key,0),profile))

class CredentialStore:
    def __init__(self,path):
        if not isinstance(path,Path) or not path.is_absolute() or path.resolve(strict=True)!=path:raise Denied("credential_path")
        st=path.lstat()
        if not stat.S_ISDIR(st.st_mode) or st.st_uid!=os.geteuid() or st.st_mode&0o077:raise Denied("credential_directory")
        self.path=path;self.inode=(st.st_dev,st.st_ino)
    def _open(self):
        fd=os.open(self.path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        st=os.fstat(fd)
        if (st.st_dev,st.st_ino)!=self.inode or st.st_uid!=os.geteuid() or st.st_mode&0o077:
            os.close(fd);raise Denied("credential_directory_replaced")
        fcntl.flock(fd,fcntl.LOCK_EX)
        return fd
    def _read(self,fd,name):
        f=os.open(name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=fd)
        try:
            st=os.fstat(f)
            if not stat.S_ISREG(st.st_mode) or st.st_nlink!=1 or st.st_uid!=os.geteuid() or st.st_mode&0o077:raise Denied("credential_file")
            raw=os.read(f,16385)
            if len(raw)>16384:raise Denied("credential_size")
            return raw
        finally:os.close(f)
    def _load(self,fd,pin):
        names=sorted(os.listdir(fd))
        if not names or len(names)>128 or names!=[f"{i:04}.json" for i in range(len(names))]:raise Denied("credential_history")
        prev="0"*64;old=None
        for i,name in enumerate(names):
            raw=self._read(fd,name);obj=json.loads(raw)
            if set(obj)!={"index","previous","state"} or obj["index"]!=i or obj["previous"]!=prev:raise Denied("credential_chain")
            s=obj["state"];c=Credential(bytes.fromhex(s["id"]),bytes.fromhex(s["key"]),s["count"]);profile=s["profile"]
            if json.dumps({"index":i,"previous":prev,"state":json.loads(encode(c,profile))},separators=(",",":")).encode()!=raw:raise Denied("credential_encoding")
            if old and (fingerprint(c,profile)!=fingerprint(old[0],old[1]) or c.count<old[0].count or (old[0].count>0 and c.count==old[0].count)):raise Denied("credential_rollback")
            prev=digest(raw);old=(c,profile)
        if not valid(pin,64) or prev!=pin:raise Denied("credential_external_pin")
        return old[0],old[1],len(names)
    def _append(self,fd,index,previous,c,profile):
        if index>=128:raise Denied("credential_history_full")
        raw=json.dumps({"index":index,"previous":previous,"state":json.loads(encode(c,profile))},separators=(",",":")).encode()
        f=os.open(f"{index:04}.json",os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
        try:
            if os.write(f,raw)!=len(raw):raise Denied("credential_short_write")
            os.fsync(f);os.fsync(fd)
        finally:os.close(f)
        return digest(raw)
    def install_verified(self,c,profile,owner_fingerprint):
        # Explicit offline ceremony result and independently checked key fingerprint.
        if fingerprint(c,profile)!=owner_fingerprint:raise Denied("credential_owner_pin")
        fd=self._open()
        try:
            if os.listdir(fd):raise Denied("credential_already_enrolled")
            return self._append(fd,0,"0"*64,c,profile)
        finally:os.close(fd)
    def load(self,pin):
        fd=self._open()
        try:return self._load(fd,pin)[:2]
        finally:os.close(fd)
    def advance(self,pin,c,profile):
        fd=self._open()
        try:
            old,p,n=self._load(fd,pin)
            if fingerprint(c,profile)!=fingerprint(old,p) or c.count<old.count or (old.count>0 and c.count==old.count):raise Denied("credential_rollback")
            return self._append(fd,n,pin,c,profile)
        finally:os.close(fd)

class DurableCeremonies:
    def __init__(self,store,external_pin):
        if type(store) is not CredentialStore:raise Denied("credential_store")
        c,self.profile=store.load(external_pin)
        self.store=store;self.pin=external_pin;self.auth=Ceremonies(c,profile=self.profile);self.lock=threading.Lock();self.failed=False
    def begin(self,op,binding):
        with self.lock:
            if self.failed:raise Denied("credential_frozen")
            self.store.load(self.pin)
            return self.auth.begin(op,binding)
    def verify(self,op,binding,assertion):
        with self.lock:
            if self.failed:raise Denied("credential_frozen")
            self.store.load(self.pin)
            self.auth.verify(op,binding,assertion)
            try:self.pin=self.store.advance(self.pin,self.auth._pinned,self.profile)
            except BaseException:self.failed=True;raise
            return self.pin
