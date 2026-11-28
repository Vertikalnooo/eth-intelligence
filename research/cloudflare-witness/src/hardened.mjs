// Offline v2 candidate. No initialization, reset, or external network in this entry.
const enc=new TextEncoder(), name="eth-intelligence-checkpoint-v2";
const hex=(v,n)=>typeof v==="string"&&v.length===n&&/^[0-9a-f]+$/.test(v);
const bytes=s=>Uint8Array.from(s.match(/../g),x=>parseInt(x,16));
const tohex=b=>Array.from(new Uint8Array(b),x=>x.toString(16).padStart(2,"0")).join("");
const fields=["op","store","identity","object_id","seq","checkpoint","candidate","epoch","grant","previous","next","evidence","issued_at"];
const zero="0".repeat(64);
const flags={scope:"OFFLINE_ONLY",recommendation_eligible:false,exchange_freshness_verified:false,stage2_complete:false};
function reply(status,code,extra={}){return new Response(JSON.stringify({code,...extra,...flags}),{status,headers:{"content-type":"application/json","cache-control":"no-store"}});}
function enabled(e){return e.WITNESS_MODE==="OFFLINE_V2"&&hex(e.WITNESS_STORE,32)&&hex(e.WITNESS_IDENTITY,64)&&hex(e.WITNESS_CANDIDATE,40)&&hex(e.WITNESS_OWNER_PUBLIC,130)&&e.WITNESS_OWNER_PUBLIC.startsWith("04")&&hex(e.WITNESS_HMAC_KEY,64)&&!/^0+$/.test(e.WITNESS_HMAC_KEY);}
function valid(p,raw,e,now){
 if(!p||Object.getPrototypeOf(p)!==Object.prototype||Object.keys(p).join(",")!==fields.join(",")||JSON.stringify(p)!==raw)throw Error("shape");
 if(!["CLAIM","ADVANCE"].includes(p.op)||p.store!==e.WITNESS_STORE||p.identity!==e.WITNESS_IDENTITY||p.candidate!==e.WITNESS_CANDIDATE||!hex(p.object_id,64)||!Number.isSafeInteger(p.seq)||p.seq<0||p.seq>=Number.MAX_SAFE_INTEGER-1||!hex(p.checkpoint,64)||!hex(p.epoch,32)||!hex(p.grant,32)||!hex(p.previous,64)||!hex(p.next,64)||!hex(p.evidence,64)||!Number.isSafeInteger(p.issued_at)||p.issued_at>now||now-p.issued_at>30)throw Error("binding");
 if(p.op==="CLAIM"&&(p.next!==zero||p.evidence!==zero))throw Error("claim");
 if(p.op==="ADVANCE"&&(p.next===zero||p.next===p.checkpoint||p.evidence===zero))throw Error("advance");
}
async function auth(req,e){
 if(!enabled(e))return null;
 const u=new URL(req.url);
 if(u.protocol!=="https:"||u.pathname!=="/v2/transition"||u.search||u.hash||req.method!=="POST"||req.headers.get("content-type")!=="application/json")return null;
 try{
  const sig=req.headers.get("x-ei-owner"),mac=req.headers.get("x-ei-signature");
  if(!hex(sig,128)||!hex(mac,64)||!req.body)return null;
  const len=req.headers.get("content-length");
  if(len!==null&&(!/^[1-9][0-9]{0,3}$/.test(len)||Number(len)>2048))return null;
  const reader=req.body.getReader();let n=0;const chunks=[];
  try{while(true){const {done,value}=await reader.read();if(done)break;n+=value.byteLength;if(n>2048){await reader.cancel();return null;}chunks.push(value);}}finally{reader.releaseLock();}
  if(!n||(len!==null&&Number(len)!==n))return null;
  const data=new Uint8Array(n);let i=0;for(const c of chunks){data.set(c,i);i+=c.byteLength;}
  const raw=new TextDecoder("utf-8",{fatal:true}).decode(data),p=JSON.parse(raw);
  valid(p,raw,e,Math.floor(Date.now()/1000));
  const msg=enc.encode("EI-WITNESS-OFFLINE-V2\nPOST\n/v2/transition\n"+raw);
  const h=await crypto.subtle.importKey("raw",bytes(e.WITNESS_HMAC_KEY),{name:"HMAC",hash:"SHA-256"},false,["verify"]);
  if(!await crypto.subtle.verify("HMAC",h,bytes(mac),msg))return null;
  const pub=await crypto.subtle.importKey("raw",bytes(e.WITNESS_OWNER_PUBLIC),{name:"ECDSA",namedCurve:"P-256"},false,["verify"]);
  if(!await crypto.subtle.verify({name:"ECDSA",hash:"SHA-256"},pub,bytes(sig),msg))return null;
  const digest=tohex(await crypto.subtle.digest("SHA-256",msg));
  return {raw,p,digest,sig,mac};
 }catch{return null;}
}
export class CheckpointWitness {
 constructor(ctx,env){this.ctx=ctx;this.env=env;}
 async fetch(req){
  if(!enabled(this.env))return reply(503,"disabled");
  const a=await auth(req,this.env);if(!a)return reply(403,"denied");
  const {p,digest}=a,storage=this.ctx.storage;
  try{
   valid(p,a.raw,this.env,Math.floor(Date.now()/1000));
   if(p.object_id!==this.ctx.id.toString())return reply(403,"object_mismatch");
   const result=storage.transactionSync(()=>{
    const sql=storage.sql;
    // Missing table, missing row, or invalid state fails closed. NO DDL here.
    const rows=sql.exec("SELECT * FROM anchor WHERE singleton=1").toArray();
    if(rows.length!==1)throw Error("unprovisioned");
    const s=rows[0];
    if(s.store!==p.store||s.identity!==p.identity||s.object_id!==p.object_id||s.owner!==this.env.WITNESS_OWNER_PUBLIC)throw Error("identity");
    if(s.seq!==p.seq||s.checkpoint!==p.checkpoint||s.head!==p.previous)return null;
    if(p.op==="CLAIM"){
     if(s.mode!=="READY")return null;
     const used=sql.exec("INSERT INTO used(checkpoint) VALUES(?) ON CONFLICT DO NOTHING RETURNING checkpoint",p.checkpoint).toArray();
     if(used.length!==1)return null;
     sql.exec("UPDATE anchor SET mode='SPENT',seq=?,head=?,epoch=?,grant_id=?,candidate=? WHERE singleton=1",p.seq+1,digest,p.epoch,p.grant,p.candidate);
    }else{
     if(s.mode!=="SPENT"||s.epoch!==p.epoch||s.grant_id!==p.grant||s.candidate!==p.candidate)return null;
     if(sql.exec("SELECT checkpoint FROM used WHERE checkpoint=?",p.next).toArray().length)return null;
     sql.exec("UPDATE anchor SET mode='READY',seq=?,head=?,checkpoint=?,epoch=NULL,grant_id=NULL,candidate=NULL WHERE singleton=1",p.seq+1,digest,p.next);
    }
    sql.exec("INSERT INTO events(seq,op,digest,body) VALUES(?,?,?,?)",p.seq+1,p.op,digest,a.raw);
    return {seq:p.seq+1,head:digest,checkpoint:p.op==="CLAIM"?p.checkpoint:p.next,mode:p.op==="CLAIM"?"SPENT":"READY"};
   });
   await storage.sync();
   return result?reply(201,"committed",result):reply(409,"conflict");
  }catch{return reply(503,"unconfirmed");}
 }
}
export default {async fetch(req,e){
 const u=new URL(req.url);
 if(req.method==="GET"&&u.pathname==="/health"&&!u.search)return reply(200,"research_disabled");
 if(!enabled(e))return reply(503,"disabled");
 const a=await auth(req,e);if(!a)return reply(403,"denied");
 try{
  const id=e.CHECKPOINT_WITNESS.idFromName(name);
  if(id.toString()!==a.p.object_id)return reply(403,"object_mismatch");
  return await e.CHECKPOINT_WITNESS.get(id).fetch(new Request("https://witness.internal/v2/transition",{method:"POST",headers:{"content-type":"application/json","x-ei-owner":a.sig,"x-ei-signature":a.mac},body:a.raw}));
 }catch{return reply(503,"unconfirmed");}
}};
