// Separate, explicitly local bootstrap entry. Never deploy this configuration.
import main,{CheckpointWitness as Hardened} from "./hardened.mjs";
const name="eth-intelligence-checkpoint-v2",path="/v3/bootstrap",enc=new TextEncoder();
const hex=(s,n)=>typeof s==="string"&&s.length===n&&/^[a-f0-9]+$/.test(s);
const bytes=s=>Uint8Array.from(s.match(/../g),v=>parseInt(v,16));
const hx=b=>Array.from(new Uint8Array(b),v=>v.toString(16).padStart(2,"0")).join("");
const out=(status,body)=>new Response(JSON.stringify(body),{status,headers:{"content-type":"application/json","cache-control":"no-store"}});
const keys=["store","identity","object_id","candidate","checkpoint","owner","token","issued_at"];
const on=e=>e.WITNESS_MODE==="OFFLINE_BOOTSTRAP"&&hex(e.BOOTSTRAP_TOKEN_HASH,64)&&hex(e.WITNESS_HMAC_KEY,64)&&!/^0+$/.test(e.WITNESS_HMAC_KEY)&&hex(e.WITNESS_OWNER_PUBLIC,130);
async function verified(req,e,id){
 if(!on(e)||req.method!=="POST"||req.headers.get("content-type")!=="application/json"||new URL(req.url).protocol!=="https:"||!req.body)return null;
 try{
  const sig=req.headers.get("x-ei-owner"),mac=req.headers.get("x-ei-signature");
  if(!hex(sig,128)||!hex(mac,64))return null;
  const reader=req.body.getReader();let n=0;const chunks=[];
  try{while(true){const {done,value}=await reader.read();if(done)break;n+=value.byteLength;if(n>2048){await reader.cancel();return null;}chunks.push(value);}}finally{reader.releaseLock();}
  const a=new Uint8Array(n);let i=0;for(const v of chunks){a.set(v,i);i+=v.byteLength;}
  const raw=new TextDecoder("utf-8",{fatal:true}).decode(a),p=JSON.parse(raw);
  if(!p||Object.keys(p).join(",")!==keys.join(",")||JSON.stringify(p)!==raw||!hex(p.store,32)||p.store!==e.WITNESS_STORE||!hex(p.identity,64)||p.identity!==e.WITNESS_IDENTITY||!hex(p.object_id,64)||p.object_id!==id||!hex(p.candidate,40)||p.candidate!==e.WITNESS_CANDIDATE||!hex(p.checkpoint,64)||/^0+$/.test(p.checkpoint)||!hex(p.owner,130)||p.owner!==e.WITNESS_OWNER_PUBLIC||!hex(p.token,64)||!Number.isSafeInteger(p.issued_at))return null;
  if(hx(await crypto.subtle.digest("SHA-256",bytes(p.token)))!==e.BOOTSTRAP_TOKEN_HASH)return null;
  const msg=enc.encode("EI-WITNESS-BOOTSTRAP-OFFLINE-V1\n"+raw);
  const h=await crypto.subtle.importKey("raw",bytes(e.WITNESS_HMAC_KEY),{name:"HMAC",hash:"SHA-256"},false,["verify"]);
  const k=await crypto.subtle.importKey("raw",bytes(p.owner),{name:"ECDSA",namedCurve:"P-256"},false,["verify"]);
  if(!await crypto.subtle.verify("HMAC",h,bytes(mac),msg)||!await crypto.subtle.verify({name:"ECDSA",hash:"SHA-256"},k,bytes(sig),msg))return null;
  const age=Math.floor(Date.now()/1000)-p.issued_at;
  if(age<0||age>30)return null;
  return p;
 }catch{return null;}
}
export class CheckpointWitness extends Hardened{
 async fetch(req){
  const u=new URL(req.url);
  if(!on(this.env)||u.pathname!==path||u.search)return out(403,{code:"denied"});
  const id=this.ctx.id.toString(),p=await verified(req,this.env,id);
  if(!p)return out(403,{code:"denied"});
  try{
   const s=this.ctx.storage;
   s.transactionSync(()=>{
    const sql=s.sql;
    // No IF NOT EXISTS or overwrite. Any existing user table means deny.
    if(sql.exec("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('anchor','used','events','bootstrap_seal')").toArray().length)throw Error("existing");
    sql.exec("CREATE TABLE anchor(singleton INTEGER PRIMARY KEY CHECK(singleton=1),store TEXT NOT NULL,identity TEXT NOT NULL,object_id TEXT NOT NULL,owner TEXT NOT NULL,seq INTEGER NOT NULL,head TEXT NOT NULL,checkpoint TEXT NOT NULL,mode TEXT NOT NULL,epoch TEXT,grant_id TEXT,candidate TEXT) STRICT");
    sql.exec("CREATE TABLE used(checkpoint TEXT PRIMARY KEY) STRICT");
    sql.exec("CREATE TABLE events(seq INTEGER PRIMARY KEY,op TEXT NOT NULL,digest TEXT UNIQUE NOT NULL,body TEXT NOT NULL) STRICT");
    sql.exec("CREATE TABLE bootstrap_seal(singleton INTEGER PRIMARY KEY CHECK(singleton=1),token_hash TEXT NOT NULL) STRICT");
    sql.exec("INSERT INTO bootstrap_seal VALUES(1,?)",this.env.BOOTSTRAP_TOKEN_HASH);
    sql.exec("INSERT INTO anchor VALUES(1,?,?,?,?,0,?,?,'READY',NULL,NULL,NULL)",p.store,p.identity,id,p.owner,"0".repeat(64),p.checkpoint);
   });
   await s.sync();
   return out(201,{code:"genesis",store:p.store,identity:p.identity,object_id:id,checkpoint:p.checkpoint,seq:0,head:"0".repeat(64),mode:"READY"});
  }catch{return out(503,{code:"unconfirmed"});}
 }
}
export default {async fetch(req,e){
 const u=new URL(req.url);
 if(u.pathname==="/health"&&req.method==="GET")return main.fetch(req,e);
 if(!on(e)||u.search)return out(403,{code:"denied"});
 const id=e.CHECKPOINT_WITNESS.idFromName(name);
 if(u.pathname==="/v3/identity"&&req.method==="GET")return out(200,{object_id:id.toString()});
 if(u.pathname!==path||req.method!=="POST")return out(403,{code:"denied"});
 return e.CHECKPOINT_WITNESS.get(id).fetch(req);
}};
