"""EI-CAP-3 canonical encoding, offline only. No signer, keys or network."""
import re
FIELDS=["version","environment","ledger","object_id","candidate","key_epoch","op","authorization_id","plan","grant","epoch","old_checkpoint","new_checkpoint","owner_head","next_owner_head","credential_revision","trust_sequence","witness_sequence","evidence","prior_authorization","scope","issued_at","not_before","expires_at"]
RULES=["EI-CAP-3","OFFLINE",32,64,40,32,["ARM","ISSUE","CONSUME","SUCCESS","UNKNOWN","ADVANCE"],32,32,32,32,64,64,64,64,0,0,0,64,32,"SYNTHETIC_ONLY",0,0,0]
MAX_INT=9007199254740991

def frame(fields):
    if type(fields) is not dict or set(fields)!=set(FIELDS):raise ValueError("fields")
    parts=[]
    for name,rule in zip(FIELDS,RULES):
        v=fields[name]
        if type(v) is not str or not v.isascii() or len(v)>64:raise ValueError("field")
        if type(rule) is int:
            if rule==0:
                if not re.fullmatch(r"0|[1-9][0-9]{0,15}",v) or int(v)>MAX_INT:raise ValueError("number")
            elif not re.fullmatch("[0-9a-f]{"+str(rule)+"}",v):raise ValueError("hex")
        elif type(rule) is list:
            if v not in rule:raise ValueError("enum")
        elif v!=rule:raise ValueError("constant")
        parts.append(str(len(v))+":"+v)
    return "".join(parts).encode("ascii")
