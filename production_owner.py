"""Offline WebAuthn assertion verification for a pre-provisioned ES256 owner.

Registration, public deployment, browser UI and live credentials are absent.
"""
import base64
from dataclasses import dataclass
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import subprocess
import tempfile
import threading
import time

from production_pg import Denied,hex_id

RP="localhost"
ORIGIN="https://localhost"
# DER SubjectPublicKeyInfo for id-ecPublicKey / prime256v1, uncompressed point.
SPKI_PREFIX=bytes.fromhex("3059301306072a8648ce3d020106082a8648ce3d03010703420004")
PURPOSES={"ISSUE","CONSUME","REVOKE"}

def b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

def decode(value,limit):
    if type(value) is not str or not 0<len(value)<=limit*2 or not value.isascii():
        raise Denied("invalid_encoding")
    try:
        out=base64.b64decode(value+"="*((-len(value))%4),altchars=b"-_",validate=True)
    except ValueError as exc:
        raise Denied("invalid_encoding") from exc
    if len(out)>limit or b64(out)!=value:
        raise Denied("invalid_encoding")
    return out

def unique(pairs):
    d={}
    for k,v in pairs:
        if k in d: raise Denied("duplicate_json_key")
        d[k]=v
    return d

def verify_es256(key,message,sig):
    if type(key) is not bytes or len(key)!=91 or not key.startswith(SPKI_PREFIX):
        raise Denied("invalid_pinned_es256_key")
    if not 8<=len(sig)<=72:
        raise Denied("invalid_signature")
    with tempfile.TemporaryDirectory() as tmp:
        p=Path(tmp)
        (p/"pub.der").write_bytes(key)
        (p/"sig.der").write_bytes(sig)
        try:
            result=subprocess.run(["/usr/bin/openssl","dgst","-sha256","-verify",
                                   str(p/"pub.der"),"-keyform","DER","-signature",str(p/"sig.der")],
                                  input=message,capture_output=True,timeout=2,
                                  env={"PATH":"/usr/bin:/bin","LANG":"C"})
        except (OSError,subprocess.TimeoutExpired) as exc:
            raise Denied("signature_verifier_unavailable") from exc
        if result.returncode!=0 or result.stdout.strip()!=b"Verified OK":
            raise Denied("bad_signature")

@dataclass(frozen=True)
class Pending:
    challenge: str
    purpose: str
    grant: str
    expires_ns: int

class Owner:
    def __init__(self,credential_id,public_key):
        decode(credential_id,256)
        if type(public_key) is not bytes or len(public_key)!=91 or not public_key.startswith(SPKI_PREFIX):
            raise Denied("invalid_pinned_es256_key")
        self.credential_id=credential_id
        self.public_key=public_key
        self._lock=threading.Lock()
        self._pending={}

    def challenge(self,purpose,grant):
        if type(purpose) is not str or purpose not in PURPOSES:
            raise Denied("bad_purpose")
        hex_id(grant)
        with self._lock:
            now=time.monotonic_ns()
            self._pending={k:v for k,v in self._pending.items() if now<v.expires_ns}
            if len(self._pending)>=16:
                raise Denied("challenge_capacity")
            c=b64(secrets.token_bytes(32))
            self._pending[c]=Pending(c,purpose,grant,now+60_000_000_000)
        return {"challenge":c,"rpId":RP,"allowCredentials":[{"type":"public-key","id":self.credential_id}],
                "userVerification":"required","timeout":60000,"purpose":purpose,"grant":grant}

    def assert_owner(self,purpose,grant,assertion):
        if type(assertion) is not dict or set(assertion)!={"id","challenge","clientDataJSON","authenticatorData","signature"}:
            raise Denied("assertion_shape")
        c=assertion["challenge"]
        if type(c) is not str or len(c)!=43:
            raise Denied("challenge_shape")
        # Burn before verification, including malformed signatures. No retry.
        with self._lock:
            pending=self._pending.pop(c,None)
        if (pending is None or time.monotonic_ns()>=pending.expires_ns
                or pending.purpose!=purpose or pending.grant!=grant):
            raise Denied("challenge_missing_or_wrong_binding")
        if type(assertion["id"]) is not str or not hmac.compare_digest(assertion["id"],self.credential_id):
            raise Denied("credential_not_owner")
        raw=decode(assertion["clientDataJSON"],2048)
        data=decode(assertion["authenticatorData"],64)
        sig=decode(assertion["signature"],72)
        try:
            client=json.loads(raw.decode("utf-8"),object_pairs_hook=unique)
        except (ValueError,UnicodeError,RecursionError) as exc:
            raise Denied("bad_client_data") from exc
        if (type(client) is not dict or client.get("type")!="webauthn.get"
                or client.get("challenge")!=c or client.get("origin")!=ORIGIN
                or client.get("crossOrigin",False) is not False
                or "topOrigin" in client):
            raise Denied("client_binding_failed")
        if len(data)!=37 or not hmac.compare_digest(data[:32],hashlib.sha256(RP.encode()).digest()):
            raise Denied("rp_hash_or_authenticator_data")
        flags=data[32]
        # UP and UV, no AT/ED/reserved bits. BS implies BE.
        if flags&5!=5 or flags&0xe2 or (flags&0x10 and not flags&8):
            raise Denied("user_presence_or_verification")
        verify_es256(self.public_key,data+hashlib.sha256(raw).digest(),sig)
        if time.monotonic_ns()>=pending.expires_ns:
            raise Denied("expired_after_verify")
        # Synced passkeys may report zero/nonmonotonic counters. Replay
        # prevention here is one-use challenge + grant, not signCount.
