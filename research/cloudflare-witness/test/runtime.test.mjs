import {test} from "node:test";
import assert from "node:assert/strict";
import {mkdtempSync,rmSync,readFileSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {fileURLToPath} from "node:url";
import {createHmac} from "node:crypto";
import {spawn} from "node:child_process";
import {createServer} from "node:net";
import https from "node:https";
const root=fileURLToPath(new URL("../",import.meta.url));
const cfg=JSON.parse(readFileSync(join(root,"wrangler.legacy-test.jsonc"),"utf8"));
const env={WITNESS_MODE:"OFFLINE_FIXTURE",WITNESS_STORE:"a".repeat(32),WITNESS_CHECKPOINT:"b".repeat(64),WITNESS_CANDIDATE:"c".repeat(40),WITNESS_HMAC_KEY:"1234567890abcdef".repeat(4)};
function signed(e=env,overrides={},key=e.WITNESS_HMAC_KEY){
 const body=JSON.stringify({store:e.WITNESS_STORE,checkpoint:e.WITNESS_CHECKPOINT,candidate:e.WITNESS_CANDIDATE,epoch:"d".repeat(32),grant:"e".repeat(32),issued_at:Math.floor(Date.now()/1000),...overrides});
 const signature=createHmac("sha256",Buffer.from(key,"hex")).update("EI-WITNESS-OFFLINE-V1\nPOST\n/v1/claim\n"+body).digest("hex");
 return {method:"POST",headers:{"content-type":"application/json","x-ei-signature":signature},body};
}
test("Wrangler local workerd: migration, authorization, concurrency and persistence", {timeout:90000},async t=>{
 const dir=mkdtempSync(join(tmpdir(),"ei-wrangler-"));let proc;
 function signal(p,s){try{process.kill(-p.pid,s);}catch(e){if(e.code!=="ESRCH")throw e;}}
 async function stop(hard=false){
  if(!proc)return;const p=proc;proc=null;
  const exited=p.exitCode!==null||p.signalCode!==null?Promise.resolve():new Promise(r=>p.once("exit",r));
  signal(p,hard?"SIGKILL":"SIGTERM");
  const timer=setTimeout(()=>signal(p,"SIGKILL"),2000);await exited;clearTimeout(timer);signal(p,"SIGKILL");
 }
 t.after(async()=>{await stop(true);rmSync(dir,{recursive:true,force:true});});
 const probe=createServer();await new Promise(r=>probe.listen(0,"127.0.0.1",r));const port=probe.address().port;await new Promise(r=>probe.close(r));
 let log="";
 function call(path,options={}){
  return new Promise((resolve,reject)=>{const req=https.request({hostname:"127.0.0.1",port,path,rejectUnauthorized:false,method:options.method??"GET",headers:options.headers??{},timeout:2500},res=>{let body="";res.on("data",x=>body+=x);res.on("end",()=>resolve({status:res.statusCode,body}));});req.on("error",reject);req.on("timeout",()=>req.destroy(Error("timeout")));req.end(options.body);});
 }
 async function start(bindings={}){
  const argv=["dev","--local","--ip","127.0.0.1","--port",String(port),"--local-protocol","https","--persist-to",dir,"--config",join(root,"wrangler.legacy-test.jsonc"),...Object.entries(bindings).flatMap(([k,v])=>["--var",k+":"+v])];
  log="";proc=spawn(process.execPath,[join(root,"node_modules/wrangler/bin/wrangler.js"),...argv],{cwd:root,detached:true,env:{PATH:process.env.PATH,HOME:dir,CI:"true",WRANGLER_SEND_METRICS:"false",CLOUDFLARE_INCLUDE_PROCESS_ENV:"false",CLOUDFLARE_CF_FETCH_ENABLED:"false"},stdio:["ignore","pipe","pipe"]});
  proc.stdout.on("data",d=>log+=d);proc.stderr.on("data",d=>log+=d);
  for(let i=0;i<160;i++){if(proc.exitCode!==null||proc.signalCode!==null)throw Error("wrangler exited: "+log);try{if((await call("/health")).status===200)return;}catch{}await new Promise(r=>setTimeout(r,100));}
  throw Error("wrangler not ready: "+log);
 }
 const claim=opts=>call("/v1/claim",opts??signed());
 await start();
 await t.test("actual checked-in disabled Wrangler config",async()=>{
  assert.equal((await claim()).status,503);
  const h=await call("/health");assert.equal(h.status,200);assert.equal(JSON.parse(h.body).recommendation_eligible,false);
 });await stop();
 await start(env);
 await t.test("actual workerd denies wrong signature, TTL, scope and unknown routes",async()=>{
  for(const o of [signed(env,{},"f".repeat(64)),signed(env,{issued_at:Math.floor(Date.now()/1000)-60}),signed(env,{issued_at:Math.floor(Date.now()/1000)+60}),signed(env,{checkpoint:"f".repeat(64)}),signed(env,{store:"0".repeat(32)})])assert.equal((await claim(o)).status,403);
  for(const path of ["/reset","/v1/advance","/v1/list"])assert.equal((await call(path,signed())).status,403);
 });
 await t.test("actual SQLite migration and 32 concurrent requests exactly one",async()=>{
  const results=await Promise.all(Array.from({length:32},()=>claim()));
  assert.equal(results.filter(x=>x.status===201).length,1,JSON.stringify(results)+" "+log);
  assert.equal(results.filter(x=>x.status===409).length,31);
  const ok=JSON.parse(results.find(x=>x.status===201).body);
  assert.equal(ok.code,"spent");assert.equal(ok.recommendation_eligible,false);
 });
 // Hard process-group kill AFTER acknowledged commit, not during commit.
 await stop(true);await start(env);
 await t.test("checkpoint survives hard runtime process termination",async()=>assert.equal((await claim()).status,409));
 await stop();const next={...env,WITNESS_CANDIDATE:"f".repeat(40)};await start(next);
 await t.test("candidate SHA change cannot reuse checkpoint",async()=>assert.equal((await claim(signed(next))).status,409));
 await stop();
});
