"""Owner-side signed receipt journal; independent local filesystem fixture.
No private key in controller, no automatic provisioning, recovery or re-pinning.
The owner must retain the returned head separately from this directory.
"""
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
from production_pg import Denied
from production_recovery import valid

ZERO="0"*64
DOMAIN=b"EI-OWNER-JOURNAL-OFFLINE-V1\n"
FIELDS=("index","previous","kind","store","identity","object_id","checkpoint","seq","head","binding")
NEXT={None:("BOOTSTRAP_INTENT",),"BOOTSTRAP_INTENT":("GENESIS",),"GENESIS":("CLAIM_INTENT",),
      "CLAIM_INTENT":("CLAIM_RECEIPT",),"CLAIM_RECEIPT":("ADVANCE_INTENT",),
      "ADVANCE_INTENT":("ADVANCE_RECEIPT",),"ADVANCE_RECEIPT":("CLAIM_INTENT",)}
def compact(x):return json.dumps(x,separators=(",",":"),ensure_ascii=True).encode()
def digest(x):return hashlib.sha256(x).hexdigest()

def check_record(p,last):
    if type(p) is not dict or tuple(p)!=FIELDS or type(p["index"]) is not int or not 0<=p["index"]<128:
        raise Denied("journal_shape")
    if any(not valid(p[k],n) for k,n in (("previous",64),("store",32),("identity",64),("object_id",64),("checkpoint",64),("head",64),("binding",64))) or type(p["seq"]) is not int or not 0<=p["seq"]<2**53-1:
        raise Denied("journal_binding")
    if p["kind"] not in NEXT[None if last is None else last["kind"]]:raise Denied("journal_order")
    if last is None:
        if p["index"]!=0 or p["previous"]!=ZERO or p["seq"]!=0 or p["head"]!=ZERO:raise Denied("genesis_invalid")
        return
    if p["index"]!=last["index"]+1 or any(p[k]!=last[k] for k in ("store","identity","object_id")):
        raise Denied("identity_changed")
    k=p["kind"]
    if k!="CLAIM_INTENT" and p["binding"]!=last["binding"]:raise Denied("plan_changed")
    if k in ("CLAIM_RECEIPT","ADVANCE_RECEIPT"):
        if p["seq"]!=last["seq"]+1 or p["head"] in (ZERO,last["head"]):raise Denied("receipt_invalid")
        if (p["checkpoint"]!=last["checkpoint"])!=(k=="ADVANCE_RECEIPT"):raise Denied("checkpoint_invalid")
    elif any(p[x]!=last[x] for x in ("seq","head","checkpoint")):
        raise Denied("unexpected_advance")

@dataclass(frozen=True)
class Journal:
    directory:Path
    device:int
    inode:int
    public:bytes
    @classmethod
    def pin(cls,directory,public):
        if not isinstance(directory,Path) or not directory.is_absolute() or directory.resolve(strict=True)!=directory:
            raise Denied("journal_path")
        info=directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o077 or type(public) is not bytes or len(public)!=65 or public[0]!=4:
            raise Denied("journal_config")
        return cls(directory,info.st_dev,info.st_ino,public)
    def _open(self):
        fd=os.open(self.directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        st=os.fstat(fd)
        if (st.st_dev,st.st_ino)!=(self.device,self.inode) or st.st_uid!=os.geteuid() or st.st_mode&0o077:
            os.close(fd);raise Denied("journal_replaced")
        fcntl.flock(fd,fcntl.LOCK_EX)
        return fd
    def _verify(self,body,sig):
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
        if not valid(sig,128):raise Denied("journal_signature")
        try:
            key=ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(),self.public)
            key.verify(encode_dss_signature(int(sig[:64],16),int(sig[64:],16)),DOMAIN+body,ec.ECDSA(hashes.SHA256()))
        except Exception as exc:raise Denied("journal_signature") from exc
    def _load(self,fd,expected):
        if not valid(expected,64):raise Denied("external_head_required")
        names=sorted(os.listdir(fd))
        if len(names)>128 or names!=[f"{i:04}.json" for i in range(len(names))]:raise Denied("journal_layout")
        last=None;head=ZERO
        for name in names:
            f=os.open(name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=fd)
            try:
                st=os.fstat(f)
                if not stat.S_ISREG(st.st_mode) or st.st_nlink!=1 or st.st_uid!=os.geteuid() or st.st_mode&0o077:raise Denied("journal_file")
                raw=os.read(f,4097)
                if len(raw)>4096:raise Denied("journal_size")
                obj=json.loads(raw)
                if type(obj) is not dict or tuple(obj)!=("record","signature") or compact(obj)!=raw:raise Denied("journal_encoding")
                p=obj["record"];body=compact(p);self._verify(body,obj["signature"])
                check_record(p,last)
                if p["previous"]!=head:raise Denied("journal_chain")
                head=digest(raw);last=p
            finally:os.close(f)
        if head!=expected:raise Denied("external_head_mismatch")
        return last,head
    def read(self,expected):
        fd=self._open()
        try:return self._load(fd,expected)[0]
        finally:os.close(fd)
    def append(self,expected,record,signature):
        fd=self._open()
        try:
            last,head=self._load(fd,expected)
            check_record(record,last)
            if record["previous"]!=head:raise Denied("journal_chain")
            self._verify(compact(record),signature)
            raw=compact({"record":record,"signature":signature})
            f=os.open(f'{record["index"]:04}.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
            try:
                if os.write(f,raw)!=len(raw):raise Denied("journal_short_write")
                os.fsync(f);os.fsync(fd)
            finally:os.close(f)
            return digest(raw)
        finally:os.close(fd)

def recovery(journal,expected,pg,witness):
    """Pure classification of independently collected snapshots, never permission.
    Caller must authenticate sources; equality is not proof of historical truth.
    """
    try:
        last=journal.read(expected)
        if last is None or last["kind"] not in ("GENESIS","ADVANCE_RECEIPT"):return "FROZEN_PENDING_OR_EMPTY"
        if type(pg) is not dict or type(witness) is not dict:return "FROZEN_MISSING"
        if pg!={"store":last["store"],"checkpoint":last["checkpoint"],"mode":"DISARMED" if last["kind"]=="GENESIS" else "CLOSED","active":None}:return "FROZEN_PG_MISMATCH"
        if witness!={k:last[k] for k in ("store","identity","object_id","checkpoint","seq","head")}|{"mode":"READY"}:return "FROZEN_WITNESS_MISMATCH"
        return "MANUAL_REVIEW_ONLY"
    except Exception:return "FROZEN_UNCONFIRMED"
