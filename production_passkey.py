"""Offline library-backed WebAuthn ceremony. No public registration route.
Provisioning result must be pinned by trusted operator before another startup.
"""
from dataclasses import dataclass
import hashlib
import hmac
import json
import secrets
import threading
import time
from production_pg import Denied
from production_owner import b64,unique

RP="localhost"
ORIGIN="https://localhost"
USER=b"eth-intelligence-offline-owner"
PURPOSES={"ARM","ISSUE","CONSUME"}

@dataclass(frozen=True)
class Credential:
    id:bytes
    public_key:bytes
    count:int

def library():
    import webauthn as w
    from webauthn.helpers.structs import (AuthenticatorSelectionCriteria,
        UserVerificationRequirement,ResidentKeyRequirement,PublicKeyCredentialDescriptor)
    from webauthn import options_to_json
    return w,AuthenticatorSelectionCriteria,UserVerificationRequirement,ResidentKeyRequirement,PublicKeyCredentialDescriptor,options_to_json

def _valid(c):
    return (type(c) is Credential and type(c.id) is bytes and 0<len(c.id)<=1024
            and type(c.public_key) is bytes and 0<len(c.public_key)<=4096
            and type(c.count) is int and 0<=c.count<=2**32-1)

class Ceremonies:
    def __init__(self,pinned=None,bootstrap_digest=None):
        if pinned is not None and not _valid(pinned): raise Denied("bad_credential")
        if bootstrap_digest is not None and (pinned is not None or type(bootstrap_digest) is not bytes or len(bootstrap_digest)!=32):
            raise Denied("bad_bootstrap_configuration")
        self._pinned=pinned
        self._bootstrap=bootstrap_digest
        self._pending=None
        self._lock=threading.Lock()

    def begin_registration(self,token):
        with self._lock:
            if self._bootstrap is None or type(token) is not bytes or len(token)!=32 or not hmac.compare_digest(hashlib.sha256(token).digest(),self._bootstrap):
                raise Denied("bootstrap_denied")
            self._bootstrap=None # burn even if library fails
            c=secrets.token_bytes(32)
            try:
                w,S,UV,RK,_,encode=library()
                options=w.generate_registration_options(rp_id=RP,rp_name="ETH Intelligence Offline",
                    user_id=USER,user_name="owner",challenge=c,timeout=60000,
                    authenticator_selection=S(resident_key=RK.REQUIRED,user_verification=UV.REQUIRED))
                result=encode(options)
            except Exception as exc: raise Denied("registration_options_failed") from exc
            self._pending=(c,"REGISTER","",time.monotonic_ns()+60_000_000_000)
            return result

    def _take(self,purpose,binding):
        pending=self._pending
        self._pending=None
        if pending is None or pending[1:3]!=(purpose,binding) or time.monotonic_ns()>=pending[3]:
            raise Denied("challenge_denied")
        return pending

    def _body(self,credential):
        if type(credential) is not str or not 0<len(credential)<=16384:
            raise Denied("credential_shape")
        try:
            obj=json.loads(credential,object_pairs_hook=unique)
        except (ValueError,RecursionError) as exc: raise Denied("credential_shape") from exc
        if type(obj) is not dict:raise Denied("credential_shape")
        return obj

    def finish_registration(self,credential):
        with self._lock:
            pending=self._take("REGISTER","")
            self._body(credential)
            try:
                w,*_=library()
                result=w.verify_registration_response(credential=credential,expected_challenge=pending[0],
                    expected_rp_id=RP,expected_origin=ORIGIN,require_user_verification=True)
                pin=Credential(result.credential_id,result.credential_public_key,result.sign_count)
                if not _valid(pin) or time.monotonic_ns()>=pending[3]:raise Denied("invalid_registration")
                # Do not install it into this process or automatically enable authentication.
                return pin
            except Exception as exc:raise Denied("registration_denied") from exc

    def begin(self,purpose,binding):
        with self._lock:
            if self._pinned is None or purpose not in PURPOSES or type(binding) is not str or not 1<=len(binding)<=512:
                raise Denied("authentication_denied")
            now=time.monotonic_ns()
            if self._pending is not None and now<self._pending[3]:raise Denied("challenge_busy")
            c=secrets.token_bytes(32)
            try:
                w,_,UV,_,Desc,encode=library()
                options=w.generate_authentication_options(rp_id=RP,challenge=c,timeout=60000,
                    allow_credentials=[Desc(id=self._pinned.id)],user_verification=UV.REQUIRED)
                result=encode(options)
            except Exception as exc:raise Denied("authentication_options_failed") from exc
            self._pending=(c,purpose,binding,now+60_000_000_000)
            return result

    def verify(self,purpose,binding,credential):
        with self._lock:
            pending=self._take(purpose,binding)
            obj=self._body(credential)
            if self._pinned is None or obj.get("id")!=b64(self._pinned.id) or obj.get("rawId")!=b64(self._pinned.id):
                raise Denied("credential_not_owner")
            response=obj.get("response")
            if type(response) is not dict or response.get("userHandle") not in (None,b64(USER)):
                raise Denied("wrong_user_handle")
            try:
                w,*_=library()
                result=w.verify_authentication_response(credential=credential,expected_challenge=pending[0],
                    expected_rp_id=RP,expected_origin=ORIGIN,credential_public_key=self._pinned.public_key,
                    credential_current_sign_count=self._pinned.count,require_user_verification=True)
                count=result.new_sign_count
                if result.credential_id!=self._pinned.id or type(count) is not int or not 0<=count<=2**32-1 or time.monotonic_ns()>=pending[3]:
                    raise Denied("invalid_assertion")
                self._pinned=Credential(self._pinned.id,self._pinned.public_key,count)
            except Exception as exc:raise Denied("authentication_denied") from exc
