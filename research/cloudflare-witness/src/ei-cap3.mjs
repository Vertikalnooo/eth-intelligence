// Offline canonical frame only; no keys, signing or networking.
export const FIELDS=Object.freeze(["version","environment","ledger","object_id","candidate","key_epoch","op","authorization_id","plan","grant","epoch","old_checkpoint","new_checkpoint","owner_head","next_owner_head","credential_revision","trust_sequence","witness_sequence","evidence","prior_authorization","scope","issued_at","not_before","expires_at"]);
const rules=["EI-CAP-3","OFFLINE",32,64,40,32,["ARM","ISSUE","CONSUME","SUCCESS","UNKNOWN","ADVANCE"],32,32,32,32,64,64,64,64,0,0,0,64,32,"SYNTHETIC_ONLY",0,0,0];
export function frame(p){
 if(!p||Object.getPrototypeOf(p)!==Object.prototype||Object.keys(p).length!==FIELDS.length||FIELDS.some(k=>!Object.hasOwn(p,k)))throw Error("fields");
 let out="";
 for(let i=0;i<FIELDS.length;i++){
  const v=p[FIELDS[i]],r=rules[i];
  if(typeof v!=="string"||v.length>64||/[^\x00-\x7f]/.test(v))throw Error("field");
  if(typeof r==="number"){
   if(r===0){if(/[^0-9]/.test(v)||!/^(0|[1-9][0-9]{0,15})$/.test(v)||!Number.isSafeInteger(Number(v)))throw Error("number");}
   else if(v.length!==r||/[^0-9a-f]/.test(v))throw Error("hex");
  }else if(Array.isArray(r)){if(!r.includes(v))throw Error("enum");}
  else if(v!==r)throw Error("constant");
  out+=v.length+":"+v;
 }
 return new TextEncoder().encode(out);
}
