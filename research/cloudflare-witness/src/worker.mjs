// Research only. No external fetch, no automatic initialization or advancement.
const encoder = new TextEncoder();
const PATH = "/v1/claim";
const OBJECT = "eth-intelligence-checkpoint-v1";
const MAX = 1024;
const hex = (v,n) => typeof v === "string" && v.length === n && /^[0-9a-f]+$/.test(v);
const fields = ["store","checkpoint","candidate","epoch","grant","issued_at"];
const flags = {scope:"OFFLINE_ONLY",recommendation_eligible:false,exchange_freshness_verified:false,stage2_complete:false};
function response(status,code) {
  return new Response(JSON.stringify({code,...flags}), {status,headers:{"Content-Type":"application/json","Cache-Control":"no-store","X-Content-Type-Options":"nosniff"}});
}
function configured(env) {
  return env.WITNESS_MODE === "OFFLINE_FIXTURE" && hex(env.WITNESS_STORE,32)
    && hex(env.WITNESS_CHECKPOINT,64) && hex(env.WITNESS_CANDIDATE,40)
    && hex(env.WITNESS_HMAC_KEY,64) && !/^0+$/.test(env.WITNESS_HMAC_KEY);
}
function route(req) {
  const url = new URL(req.url);
  return url.protocol === "https:" && url.pathname === PATH && !url.search && !url.hash
    && req.method === "POST" && req.headers.get("content-type") === "application/json";
}
async function boundedBody(req) {
  const n=req.headers.get("content-length");
  if(n!==null && (!/^[1-9][0-9]{0,3}$/.test(n)||Number(n)>MAX)) throw Error("length");
  if(!req.body)throw Error("body");
  const reader=req.body.getReader(), chunks=[];
  let size=0;
  try {
    while(true) {
      const {done,value}=await reader.read();
      if(done)break;
      size+=value.byteLength;
      if(size>MAX){await reader.cancel();throw Error("length");}
      chunks.push(value);
    }
  } finally {reader.releaseLock();}
  if(!size||(n!==null&&size!==Number(n)))throw Error("length");
  const raw=new Uint8Array(size);let offset=0;
  for(const chunk of chunks){raw.set(chunk,offset);offset+=chunk.byteLength;}
  return new TextDecoder("utf-8",{fatal:true}).decode(raw);
}
function parse(raw,env,now) {
  const p=JSON.parse(raw);
  if(!p||Object.getPrototypeOf(p)!==Object.prototype||Object.keys(p).join(",")!==fields.join(",")
      ||JSON.stringify(p)!==raw) throw Error("noncanonical");
  if(!hex(p.store,32)||!hex(p.checkpoint,64)||!hex(p.candidate,40)||!hex(p.epoch,32)||!hex(p.grant,32)
      ||!Number.isSafeInteger(p.issued_at)||p.issued_at>now||now-p.issued_at>30
      ||p.store!==env.WITNESS_STORE||p.checkpoint!==env.WITNESS_CHECKPOINT||p.candidate!==env.WITNESS_CANDIDATE)throw Error("binding");
  return p;
}
const bytes = s => Uint8Array.from(s.match(/../g), x=>parseInt(x,16));
async function verify(req,raw,env) {
  const sig=req.headers.get("x-ei-signature");
  if(!hex(sig,64))return false;
  const key=await crypto.subtle.importKey("raw",bytes(env.WITNESS_HMAC_KEY),{name:"HMAC",hash:"SHA-256"},false,["verify"]);
  return crypto.subtle.verify("HMAC",key,bytes(sig),encoder.encode("EI-WITNESS-OFFLINE-V1\nPOST\n"+PATH+"\n"+raw));
}
async function authorized(req,env) {
  if(!configured(env)||!route(req))return null;
  try {
    const raw=await boundedBody(req);
    if(!await verify(req,raw,env))return null;
    return {raw,p:parse(raw,env,Math.floor(Date.now()/1000))};
  }catch{return null;}
}
// SQL is also executed by the local real SQLite test adapter.
// No deletion, reset, arbitrary object name, or automatic next checkpoint.
export class CheckpointWitness {
  constructor(ctx,env){this.ctx=ctx;this.env=env;}
  async fetch(req) {
    if(!configured(this.env))return response(503,"disabled");
    const auth=await authorized(req,this.env);
    if(!auth)return response(403,"denied");
    const p=auth.p, storage=this.ctx.storage;
    try {
      // Recheck TTL after async crypto and before a synchronous transaction.
      parse(auth.raw,this.env,Math.floor(Date.now()/1000));
      const claimed=storage.transactionSync(()=>{
        const sql=storage.sql;
        sql.exec("CREATE TABLE IF NOT EXISTS spent (store TEXT NOT NULL, checkpoint TEXT NOT NULL, candidate TEXT NOT NULL, epoch TEXT NOT NULL, grant_id TEXT NOT NULL, issued_at INTEGER NOT NULL, PRIMARY KEY(store,checkpoint)) STRICT");
        const rows=sql.exec("INSERT INTO spent(store,checkpoint,candidate,epoch,grant_id,issued_at) VALUES(?,?,?,?,?,?) ON CONFLICT(store,checkpoint) DO NOTHING RETURNING checkpoint",p.store,p.checkpoint,p.candidate,p.epoch,p.grant,p.issued_at).toArray();
        return rows.length===1&&rows[0].checkpoint===p.checkpoint;
      });
      // Explicit durability fence in addition to the platform output gate.
      await storage.sync();
      return claimed?response(201,"spent"):response(409,"already_spent");
    }catch{return response(503,"unconfirmed");}
  }
}
export default {
  async fetch(req,env) {
    const u=new URL(req.url);
    if(req.method==="GET"&&u.pathname==="/health"&&!u.search)return response(200,"research_disabled");
    if(!configured(env))return response(503,"disabled");
    const auth=await authorized(req,env);
    if(!auth)return response(403,"denied");
    try {
      const stub=env.CHECKPOINT_WITNESS.get(env.CHECKPOINT_WITNESS.idFromName(OBJECT));
      return await stub.fetch(new Request("https://witness.internal"+PATH,{method:"POST",headers:{"content-type":"application/json","x-ei-signature":req.headers.get("x-ei-signature")},body:auth.raw}));
    }catch{return response(503,"unconfirmed");}
  }
};
