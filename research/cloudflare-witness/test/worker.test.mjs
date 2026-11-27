import {test} from "node:test";
import assert from "node:assert/strict";
import {DatabaseSync} from "node:sqlite";
import {mkdtempSync,rmSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {createHmac} from "node:crypto";
import worker,{CheckpointWitness} from "../src/worker.mjs";
import {readFileSync} from "node:fs";
const envBase={WITNESS_MODE:"OFFLINE_FIXTURE",WITNESS_STORE:"a".repeat(32),WITNESS_CHECKPOINT:"b".repeat(64),WITNESS_CANDIDATE:"c".repeat(40),WITNESS_HMAC_KEY:"1234567890abcdef".repeat(4)};
function payload(env=envBase){return {store:env.WITNESS_STORE,checkpoint:env.WITNESS_CHECKPOINT,candidate:env.WITNESS_CANDIDATE,epoch:"d".repeat(32),grant:"e".repeat(32),issued_at:Math.floor(Date.now()/1000)};}
function request(p=payload(),opts={}){
  const raw=typeof p==="string"?p:JSON.stringify(p);
  const sig=createHmac("sha256",Buffer.from(opts.key??envBase.WITNESS_HMAC_KEY,"hex")).update("EI-WITNESS-OFFLINE-V1\nPOST\n/v1/claim\n"+raw).digest("hex");
  return new Request(opts.url??"https://local.test/v1/claim",{method:"POST",headers:{"content-type":"application/json","x-ei-signature":sig,...opts.headers},body:raw});
}
function fixture(t){
 const dir=mkdtempSync(join(tmpdir(),"ei-witness-"));const path=join(dir,"db.sqlite");let db;
 const state={failSync:false,failTx:false,names:[],calls:0};
 function open(){db=new DatabaseSync(path);db.exec("PRAGMA synchronous=FULL");}
 open();t.after(()=>{db.close();rmSync(dir,{recursive:true,force:true});});
 const storage={sql:{exec(q,...args){return {toArray:()=>db.prepare(q).all(...args)};}},transactionSync(cb){db.exec("BEGIN IMMEDIATE");try{const v=cb();if(state.failTx)throw Error("injected");db.exec("COMMIT");return v;}catch(e){db.exec("ROLLBACK");throw e;}},async sync(){if(state.failSync)throw Error("lost acknowledgement");}};
 // DDL calls may be ignored by worker: execute immediately, not lazily.
 storage.sql.exec=(q,...args)=>{const rows=db.prepare(q).all(...args);return {toArray:()=>rows};};
 function make(env=envBase){const e={...env};const obj=new CheckpointWitness({storage},e);e.CHECKPOINT_WITNESS={idFromName(n){state.names.push(n);return n;},get(){return {fetch(req){state.calls++;return obj.fetch(req);}};}};return e;}
 return {state,make,reopen(){db.close();open();},count(){return db.prepare("SELECT count(*) AS n FROM spent").get().n;}};
}
test("default disabled config and health",async t=>{
 const f=fixture(t),e=f.make({WITNESS_MODE:"DISABLED"});
 assert.equal((await worker.fetch(request(),e)).status,503);assert.equal(f.state.calls,0);
 const h=await worker.fetch(new Request("https://local.test/health"),e);
 assert.equal(h.status,200);assert.equal((await h.json()).recommendation_eligible,false);
 const cfg=JSON.parse(readFileSync(new URL("../wrangler.jsonc",import.meta.url),"utf8"));
 assert.equal(cfg.workers_dev,false);assert.equal(cfg.preview_urls,false);assert.equal(cfg.vars.WITNESS_MODE,"DISABLED");
 assert.deepEqual(cfg.migrations[0].new_sqlite_classes,["CheckpointWitness"]);
});
test("valid claim, persisted on close and reopen",async t=>{
 const f=fixture(t);assert.equal((await worker.fetch(request(),f.make())).status,201);
 f.reopen();assert.equal((await worker.fetch(request(),f.make())).status,409);assert.equal(f.count(),1);
});
test("concurrent claims exactly one",async t=>{
 const f=fixture(t),e=f.make();const statuses=await Promise.all(Array.from({length:20},async()=> (await worker.fetch(request(),e)).status));
 assert.equal(statuses.filter(x=>x===201).length,1);assert.equal(statuses.filter(x=>x===409).length,19);assert.equal(f.count(),1);assert.equal(new Set(f.state.names).size,1);
});
test("different candidate cannot spend same anchor",async t=>{
 const f=fixture(t);assert.equal((await worker.fetch(request(),f.make())).status,201);
 const env={...envBase,WITNESS_CANDIDATE:"f".repeat(40)};
 assert.equal((await worker.fetch(request(payload(env)),f.make(env))).status,409);assert.equal(f.count(),1);
});
test("invalid key, stale, future, wrong bindings, duplicate keys and body cap",async t=>{
 const f=fixture(t),e=f.make(),p=payload();
 for(const req of [request(p,{key:"f".repeat(64)}),request({...p,issued_at:p.issued_at-31}),request({...p,issued_at:p.issued_at+60}),request({...p,store:"0".repeat(32)}),request({...p,checkpoint:"0".repeat(64)}),request({...p,candidate:"0".repeat(40)}),request({...p,epoch:"oops"}),request(JSON.stringify(p).replace('"store":','"store":"x","store":')),request(" ".repeat(1025)),request(p,{url:"https://local.test/v1/claim?x=1"}),request(p,{headers:{"content-length":"9999"}}),request(p,{headers:{"content-type":"text/plain"}})])assert.equal((await worker.fetch(req,e)).status,403);
 assert.equal(f.state.calls,0);
});
test("lost durable acknowledgement burns checkpoint",async t=>{
 const f=fixture(t);f.state.failSync=true;assert.equal((await worker.fetch(request(),f.make())).status,503);
 f.reopen();f.state.failSync=false;assert.equal((await worker.fetch(request(),f.make())).status,409);assert.equal(f.count(),1);
});
test("transaction failure returns no success",async t=>{
 const f=fixture(t);f.state.failTx=true;assert.equal((await worker.fetch(request(),f.make())).status,503);
 // SQL transaction rolls back, but caller must freeze rather than retry.
});
test("direct object invocation repeats authorization",async t=>{
 const f=fixture(t),e=f.make();const stub=e.CHECKPOINT_WITNESS.get();
 assert.equal((await stub.fetch(request(payload(),{key:"0".repeat(64)}))).status,403);
});
test("no reset/list/advance methods",async t=>{
 const f=fixture(t),e=f.make();
 for(const path of ["/reset","/v1/advance","/v1/list","/v1/claim"])assert.equal((await worker.fetch(new Request("https://local.test"+path),e)).status,403);
 assert.equal(f.state.calls,0);
});
