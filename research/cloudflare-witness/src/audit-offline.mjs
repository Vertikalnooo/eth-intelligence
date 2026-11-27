// Read-only offline audit entry. Never use for deployed Worker.
const enc=new TextEncoder(),name="eth-intelligence-checkpoint-v2";
const hex=(s,n)=>typeof s==="string"&&s.length===n&&/^[0-9a-f]+$/.test(s);
const bytes=s=>Uint8Array.from(s.match(/../g),x=>parseInt(x,16));
const hx=b=>Array.from(new Uint8Array(b),x=>x.toString(16).padStart(2,"0")).join("");
const enabled=e=>e.WITNESS_MODE==="OFFLINE_AUDIT"&&hex(e.WITNESS_AUDIT_KEY,64)&&!/^0+$/.test(e.WITNESS_AUDIT_KEY);
const reply=(status,body,headers={})=>new Response(body,{status,headers:{"content-type":"application/json","cache-control":"no-store",...headers}});
async function key(e){return crypto.subtle.importKey("raw",bytes(e.WITNESS_AUDIT_KEY),{name:"HMAC",hash:"SHA-256"},false,["sign","verify"]);}
async function authorized(req,e){
 if(!enabled(e)||req.method!=="GET"||new URL(req.url).protocol!=="https:"||new URL(req.url).pathname!=="/v4/audit"||new URL(req.url).search)return false;
 const nonce=req.headers.get("x-ei-nonce"),mac=req.headers.get("x-ei-audit");
 return hex(nonce,64)&&hex(mac,64)&&await crypto.subtle.verify("HMAC",await key(e),bytes(mac),enc.encode("EI-AUDIT-REQUEST-V1\n"+nonce));
}
export class CheckpointWitness {
 constructor(ctx,env){this.ctx=ctx;this.env=env;}
 async fetch(req){
  try{
   if(!await authorized(req,this.env))return reply(403,'{"code":"denied"}');
   const nonce=req.headers.get("x-ei-nonce"),storage=this.ctx.storage;
   const snapshot=storage.transactionSync(()=>{
    const sql=storage.sql;
    const rows=sql.exec("SELECT store,identity,object_id,seq,head,checkpoint,mode FROM anchor WHERE singleton=1").toArray();
    if(rows.length!==1||rows[0].object_id!==this.ctx.id.toString())throw Error("anchor");
    const events=sql.exec("SELECT seq,op,digest,body FROM events ORDER BY seq DESC LIMIT 2").toArray().reverse();
    const used=sql.exec("SELECT checkpoint FROM used WHERE checkpoint=?",(events.length?JSON.parse(events[0].body).checkpoint:"")).toArray();
    return {nonce,anchor:rows[0],events,used};
   });
   const body=JSON.stringify(snapshot);
   if(enc.encode(body).length>8192)throw Error("size");
   const sig=hx(await crypto.subtle.sign("HMAC",await key(this.env),enc.encode("EI-AUDIT-RESPONSE-V1\n"+body)));
   return reply(200,body,{"x-ei-audit":sig});
  }catch{return reply(503,'{"code":"unconfirmed"}');}
 }
}
export default {async fetch(req,e){
 try{
  if(req.method==="GET"&&new URL(req.url).pathname==="/health")return reply(200,'{"code":"research_audit_only"}');
  if(!await authorized(req,e))return reply(403,'{"code":"denied"}');
  return await e.CHECKPOINT_WITNESS.get(e.CHECKPOINT_WITNESS.idFromName(name)).fetch(req);
 }catch{return reply(503,'{"code":"unconfirmed"}');}
}};
