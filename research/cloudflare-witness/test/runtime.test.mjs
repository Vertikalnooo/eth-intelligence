import {test} from "node:test";
import assert from "node:assert/strict";
import {Miniflare} from "miniflare";
import {mkdtempSync,rmSync,readFileSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {fileURLToPath} from "node:url";
import {createHmac} from "node:crypto";
import {spawn} from "node:child_process";
import {createServer} from "node:net";
import https from "node:https";
const root=fileURLToPath(new URL("../",import.meta.url));
const cfg=JSON.parse(readFileSync(join(root,"wrangler.jsonc"),"utf8"));
const env={WITNESS_MODE:"OFFLINE_FIXTURE",WITNESS_STORE:"a".repeat(32),WITNESS_CHECKPOINT:"b".repeat(64),WITNESS_CANDIDATE:"c".repeat(40),WITNESS_HMAC_KEY:"1234567890abcdef".repeat(4)};
function signed(e=env,overrides={},key=e.WITNESS_HMAC_KEY){
 const body=JSON.stringify({store:e.WITNESS_STORE,checkpoint:e.WITNESS_CHECKPOINT,candidate:e.WITNESS_CANDIDATE,epoch:"d".repeat(32),grant:"e".repeat(32),issued_at:Math.floor(Date.now()/1000),...overrides});
 const signature=createHmac("sha256",Buffer.from(key,"hex")).update("EI-WITNESS-OFFLINE-V1\nPOST\n/v1/claim\n"+body).digest("hex");
 return {method:"POST",headers:{"content-type":"application/json","x-ei-signature":signature},body};
}
function make(dir,bindings=env){
 const classes=new Set(cfg.migrations.flatMap(m=>m.new_sqlite_classes??[]));
 return new Miniflare({modules:true,scriptPath:join(root,cfg.main),compatibilityDate:cfg.compatibility_date,host:"127.0.0.1",port:0,cf:false,bindings,durableObjectsPersist:dir,durableObjects:Object.fromEntries(cfg.durable_objects.bindings.map(b=>[b.name,{className:b.class_name,useSQLite:classes.has(b.class_name)}]))});
}
const claim=(mf,options=signed())=>mf.dispatchFetch("https://local.test/v1/claim",options);
async function using(t,fn){
 const dir=mkdtempSync(join(tmpdir(),"ei-workerd-"));let mf;
 t.after(async()=>{if(mf)await mf.dispose();rmSync(dir,{recursive:true,force:true});});
 const start=async(bindings=env)=>{if(mf)await mf.dispose();mf=make(dir,bindings);await mf.ready;return mf;};
 await fn(start,dir);
}
test("workerd SQLite persistence and candidate upgrade",async t=>using(t,async start=>{
 let mf=await start();let res=await claim(mf);assert.equal(res.status,201,await res.clone().text());assert.equal((await res.json()).recommendation_eligible,false);
 mf=await start();assert.equal((await claim(mf)).status,409);
 const next={...env,WITNESS_CANDIDATE:"f".repeat(40)};mf=await start(next);assert.equal((await claim(mf,signed(next))).status,409);
}));
test("workerd concurrent requests exactly one",async t=>using(t,async start=>{
 const mf=await start();const statuses=await Promise.all(Array.from({length:32},async()=>{const r=await claim(mf);await r.text();return r.status;}));
 assert.equal(statuses.filter(x=>x===201).length,1);assert.equal(statuses.filter(x=>x===409).length,31);
}));
test("workerd deny invalid auth, timestamps, bindings and routes",async t=>using(t,async start=>{
 const mf=await start();
 for(const o of [signed(env,{},"f".repeat(64)),signed(env,{issued_at:Math.floor(Date.now()/1000)-60}),signed(env,{issued_at:Math.floor(Date.now()/1000)+60}),signed(env,{checkpoint:"f".repeat(64)}),signed(env,{store:"0".repeat(32)})])assert.equal((await claim(mf,o)).status,403);
 for(const path of ["/reset","/v1/advance","/v1/list"])assert.equal((await mf.dispatchFetch("https://local.test"+path,signed())).status,403);
 assert.equal((await claim(mf)).status,201);
}));
test("workerd disabled config denies even correctly signed request",async t=>using(t,async start=>{
 const mf=await start(cfg.vars);
 assert.equal((await claim(mf)).status,503);
 assert.equal((await mf.dispatchFetch("https://local.test/health")).status,200);
}));
test("workerd discarded success acknowledgement cannot reuse after restart",async t=>using(t,async start=>{
 let mf=await start();const ignored=await claim(mf);await ignored.arrayBuffer(); // caller discards status, not an actual crash mid-commit
 mf=await start();assert.equal((await claim(mf)).status,409);
}));
test("Wrangler local CLI reads actual migration and persists claim", {timeout:45000},async t=>{
 const dir=mkdtempSync(join(tmpdir(),"ei-wrangler-"));let proc;
 t.after(async()=>{if(proc&&proc.exitCode===null){proc.kill("SIGKILL");await new Promise(r=>proc.once("exit",r));}rmSync(dir,{recursive:true,force:true});});
 const probe=createServer();await new Promise(r=>probe.listen(0,"127.0.0.1",r));const port=probe.address().port;await new Promise(r=>probe.close(r));
 let log="";
 const argv=["dev","--local","--ip","127.0.0.1","--port",String(port),"--local-protocol","https","--persist-to",dir,"--config",join(root,"wrangler.jsonc"),...Object.entries(env).flatMap(([k,v])=>["--var",k+":"+v])];
 function call(path,options={}){
  return new Promise((resolve,reject)=>{const req=https.request({hostname:"127.0.0.1",port,path,rejectUnauthorized:false,method:options.method??"GET",headers:options.headers??{},timeout:1000},res=>{let body="";res.on("data",x=>body+=x);res.on("end",()=>resolve({status:res.statusCode,body}));});req.on("error",reject);req.on("timeout",()=>req.destroy(Error("timeout")));req.end(options.body);});
 }
 async function start(){
  proc=spawn(process.execPath,[join(root,"node_modules/wrangler/bin/wrangler.js"),...argv],{cwd:root,env:{PATH:process.env.PATH,HOME:dir,CI:"true",WRANGLER_SEND_METRICS:"false",CLOUDFLARE_INCLUDE_PROCESS_ENV:"false"},stdio:["ignore","pipe","pipe"]});
  proc.stdout.on("data",d=>log+=d);proc.stderr.on("data",d=>log+=d);
  for(let i=0;i<100;i++){if(proc.exitCode!==null)throw Error("wrangler exited: "+log);try{if((await call("/health")).status===200)return;}catch{}await new Promise(r=>setTimeout(r,100));}
  throw Error("wrangler not ready: "+log);
 }
 async function stop(){const p=proc;const exited=new Promise(r=>p.once("exit",r));p.kill("SIGTERM");const timer=setTimeout(()=>p.kill("SIGKILL"),2000);await exited;clearTimeout(timer);}
 await start();const first=await call("/v1/claim",signed());assert.equal(first.status,201,first.body+" "+log);
 await stop();await start();const second=await call("/v1/claim",signed());assert.equal(second.status,409,second.body+" "+log);await stop();
});
