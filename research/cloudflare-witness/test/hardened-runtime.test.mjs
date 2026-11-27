import {test} from "node:test";
import assert from "node:assert/strict";
import {mkdtempSync,rmSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {fileURLToPath} from "node:url";
import {createHmac,createHash,generateKeyPairSync,sign} from "node:crypto";
import {spawn} from "node:child_process";
import {createServer} from "node:net";
import https from "node:https";
const root=fileURLToPath(new URL("../",import.meta.url));
const key=generateKeyPairSync("ec",{namedCurve:"prime256v1"});
const pub=key.publicKey.export({format:"jwk"});
const zero="0".repeat(64),cp="b".repeat(64),next="9".repeat(64);
const env={WITNESS_MODE:"OFFLINE_V2",WITNESS_STORE:"a".repeat(32),WITNESS_IDENTITY:"1".repeat(64),WITNESS_CANDIDATE:"c".repeat(40),WITNESS_OWNER_PUBLIC:"04"+Buffer.from(pub.x,"base64url").toString("hex")+Buffer.from(pub.y,"base64url").toString("hex"),WITNESS_HMAC_KEY:"1234567890abcdef".repeat(4)};
function signed(id,overrides={},privateKey=key.privateKey){
 const body=JSON.stringify({op:"CLAIM",store:env.WITNESS_STORE,identity:env.WITNESS_IDENTITY,object_id:id,seq:0,checkpoint:cp,candidate:env.WITNESS_CANDIDATE,epoch:"d".repeat(32),grant:"e".repeat(32),previous:zero,next:zero,evidence:zero,issued_at:Math.floor(Date.now()/1000),...overrides});
 const msg="EI-WITNESS-OFFLINE-V2\nPOST\n/v2/transition\n"+body;
 return {method:"POST",headers:{"content-type":"application/json","x-ei-owner":sign("sha256",Buffer.from(msg),{key:privateKey,dsaEncoding:"ieee-p1363"}).toString("hex"),"x-ei-signature":createHmac("sha256",Buffer.from(env.WITNESS_HMAC_KEY,"hex")).update(msg).digest("hex")},body,digest:createHash("sha256").update(msg).digest("hex")};
}
test("hardened workerd: independent owner, immutable identity, explicit advance and lost storage",{timeout:120000},async t=>{
 const home=mkdtempSync(join(tmpdir(),"ei-v2-"));const dir=join(home,"persist");let proc,log="";
 function signal(p,s){try{process.kill(-p.pid,s);}catch(e){if(e.code!=="ESRCH")throw e;}}
 async function stop(hard=false){
  if(!proc)return;const p=proc;proc=null;
  const exited=p.exitCode!==null||p.signalCode!==null?Promise.resolve():new Promise(r=>p.once("exit",r));
  signal(p,hard?"SIGKILL":"SIGTERM");const timer=setTimeout(()=>signal(p,"SIGKILL"),2000);await exited;clearTimeout(timer);signal(p,"SIGKILL");
 }
 t.after(async()=>{await stop(true);rmSync(home,{recursive:true,force:true});});
 const probe=createServer();await new Promise(r=>probe.listen(0,"127.0.0.1",r));const port=probe.address().port;await new Promise(r=>probe.close(r));
 function call(path,o={}){
  return new Promise((resolve,reject)=>{const req=https.request({hostname:"127.0.0.1",port,path,rejectUnauthorized:false,method:o.method??"GET",headers:o.headers??{},timeout:2500},res=>{let body="";res.on("data",x=>body+=x);res.on("end",()=>resolve({status:res.statusCode,body}));});req.on("error",reject);req.on("timeout",()=>req.destroy(Error("timeout")));req.end(o.body);});
 }
 async function start(config,bindings=env,persist=dir){
  const argv=["dev","--local","--ip","127.0.0.1","--port",String(port),"--local-protocol","https","--persist-to",persist,"--config",join(root,config),...Object.entries(bindings).flatMap(([k,v])=>["--var",k+":"+v])];
  log="";proc=spawn(process.execPath,[join(root,"node_modules/wrangler/bin/wrangler.js"),...argv],{cwd:root,detached:true,env:{PATH:process.env.PATH,HOME:home,CI:"true",WRANGLER_SEND_METRICS:"false",CLOUDFLARE_INCLUDE_PROCESS_ENV:"false",CLOUDFLARE_CF_FETCH_ENABLED:"false"},stdio:["ignore","pipe","pipe"]});
  proc.stdout.on("data",d=>log+=d);proc.stderr.on("data",d=>log+=d);
  for(let i=0;i<160;i++){if(proc.exitCode!==null||proc.signalCode!==null)throw Error("wrangler exited: "+log);try{if((await call("/health")).status===200)return;}catch{}await new Promise(r=>setTimeout(r,100));}throw Error("not ready: "+log);
 }
 const claim=o=>call("/v2/transition",o);
 await start("wrangler.fixture.jsonc");
 const init=await call("/fixture/provision",{method:"POST"});assert.equal(init.status,201,init.body+" "+log);const id=init.body;assert.match(id,/^[a-f0-9]{64}$/);
 assert.equal((await call("/fixture/provision",{method:"POST"})).status,409);await stop();
 await start("wrangler.jsonc",{});
 await t.test("default disabled",async()=>assert.equal((await claim(signed(id))).status,503));await stop();
 await start("wrangler.jsonc");
 await t.test("no production bootstrap and HMAC alone cannot authorize",async()=>{
  assert.equal((await call("/fixture/provision",{method:"POST"})).status,403);
  const impostor=generateKeyPairSync("ec",{namedCurve:"prime256v1"});
  assert.equal((await claim(signed(id,{},impostor.privateKey))).status,403);
  assert.equal((await claim(signed("f".repeat(64)))).status,403);
  assert.equal((await claim(signed(id,{identity:"2".repeat(64)}))).status,403);
  assert.equal((await claim(signed(id,{issued_at:Math.floor(Date.now()/1000)-60}))).status,403);
 });
 const first=signed(id);
 await t.test("owner signed concurrent claim exactly once",async()=>{
  const all=await Promise.all(Array.from({length:20},()=>claim(first)));
  assert.equal(all.filter(x=>x.status===201).length,1,JSON.stringify(all)+" "+log);
  assert.equal(all.filter(x=>x.status===409).length,19);
  const receipt=JSON.parse(all.find(x=>x.status===201).body);assert.equal(receipt.head,first.digest);assert.equal(receipt.seq,1);assert.equal(receipt.mode,"SPENT");
 });
 await stop(true);await start("wrangler.jsonc");
 const advance=signed(id,{op:"ADVANCE",seq:1,previous:first.digest,next,evidence:"8".repeat(64)});
 await t.test("hard restart retains spent and requires separately signed advance",async()=>{
  assert.equal((await claim(first)).status,409);
  assert.equal((await claim(signed(id,{op:"ADVANCE",seq:1,previous:first.digest,next,evidence:"8".repeat(64),grant:"f".repeat(32)}))).status,409);
  assert.equal((await claim(signed(id,{op:"ADVANCE",seq:1,previous:first.digest,next,evidence:zero}))).status,403);
  const result=await claim(advance);assert.equal(result.status,201,result.body);assert.equal(JSON.parse(result.body).head,advance.digest);
  assert.equal((await claim(advance)).status,409);
 });
 const second=signed(id,{seq:2,checkpoint:next,previous:advance.digest,grant:"f".repeat(32)});
 await t.test("new checkpoint requires current chain; spent checkpoint cannot be recycled",async()=>{
  assert.equal((await claim(signed(id,{seq:2,checkpoint:next,previous:zero}))).status,409);
  const got=await claim(second);assert.equal(got.status,201,got.body);
  assert.equal((await claim(signed(id,{op:"ADVANCE",seq:3,checkpoint:next,previous:second.digest,next:cp,evidence:"7".repeat(64),grant:"f".repeat(32)}))).status,409);
 });
 await stop();await start("wrangler.jsonc",{...env,WITNESS_IDENTITY:"2".repeat(64)});
 await t.test("changed configured identity cannot repin existing journal",async()=>assert.equal((await claim(signed(id,{identity:"2".repeat(64)}))).status,503));await stop();
 await start("wrangler.jsonc",env,join(home,"empty"));
 await t.test("empty storage cannot initialize or claim",async()=>{
  const result=await claim(signed(id));assert.ok([403,503].includes(result.status),result.body);
  assert.equal((await call("/fixture/provision",{method:"POST"})).status,403);
 });await stop();
});
