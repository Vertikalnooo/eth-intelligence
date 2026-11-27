// Browser preparation only: no network or automatic ceremony. Not served by app.py.
export const RP="eth-intelligence-api.onrender.com";
export const ORIGIN="https://"+RP;
export function decode(s){
 if(typeof s!=="string"||!s.length||! /^[A-Za-z0-9_-]+$/.test(s)||s.length%4===1)throw Error("base64url");
 const a=Uint8Array.from(atob(s.replace(/-/g,"+").replace(/_/g,"/").padEnd(Math.ceil(s.length/4)*4,"=")),c=>c.charCodeAt(0));
 if(encode(a)!==s)throw Error("noncanonical");
 return a;
}
export function encode(v){return btoa(String.fromCharCode(...new Uint8Array(v instanceof ArrayBuffer?v:v.buffer,v.byteOffset??0,v.byteLength))).replace(/\+/g,"-").replace(/\//g,"_").replace(/=+$/,"");}
export function options(raw,registration){
 const o=JSON.parse(raw);
 if(!o||typeof o!=="object"||!o.challenge)throw Error("options");
 o.challenge=decode(o.challenge);
 if(registration){
  if(o.rp?.id!==RP||o.authenticatorSelection?.userVerification!=="required"||o.authenticatorSelection?.residentKey!=="required")throw Error("rp_or_uv");
  o.user.id=decode(o.user.id);
  if(o.excludeCredentials)o.excludeCredentials=o.excludeCredentials.map(x=>({...x,id:decode(x.id)}));
 }else{
  if(o.rpId!==RP||o.userVerification!=="required"||!o.allowCredentials?.length)throw Error("rp_or_uv");
  o.allowCredentials=o.allowCredentials.map(x=>({...x,id:decode(x.id)}));
 }
 return o;
}
export function serialize(c,registration){
 const r=c.response;
 const out={id:c.id,rawId:encode(c.rawId),type:c.type,response:{clientDataJSON:encode(r.clientDataJSON)}};
 if(registration)out.response.attestationObject=encode(r.attestationObject);
 else Object.assign(out.response,{authenticatorData:encode(r.authenticatorData),signature:encode(r.signature),userHandle:r.userHandle?encode(r.userHandle):null});
 return JSON.stringify(out);
}
export async function ceremony(raw,registration){
 if(location.origin!==ORIGIN||!isSecureContext||!navigator.userActivation?.isActive)throw Error("explicit_owner_action_required");
 const publicKey=options(raw,registration);
 const result=await (registration?navigator.credentials.create({publicKey}):navigator.credentials.get({publicKey}));
 if(!result)throw Error("cancelled");
 return serialize(result,registration);
}
