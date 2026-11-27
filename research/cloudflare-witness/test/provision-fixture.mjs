// Explicit LOCAL TEST ONLY bootstrap; never referenced by default Wrangler config.
import main,{CheckpointWitness as Hardened} from "../src/hardened.mjs";
const name="eth-intelligence-checkpoint-v2";
export class CheckpointWitness extends Hardened {
 async fetch(req){
  if(new URL(req.url).pathname!=="/fixture/provision")return super.fetch(req);
  if(this.env.WITNESS_MODE!=="OFFLINE_V2"||req.method!=="POST")return new Response(null,{status:403});
  const storage=this.ctx.storage;
  try{
   storage.transactionSync(()=>{
    const sql=storage.sql;
    sql.exec("CREATE TABLE anchor(singleton INTEGER PRIMARY KEY CHECK(singleton=1),store TEXT NOT NULL,identity TEXT NOT NULL,object_id TEXT NOT NULL,owner TEXT NOT NULL,seq INTEGER NOT NULL,head TEXT NOT NULL,checkpoint TEXT NOT NULL,mode TEXT NOT NULL,epoch TEXT,grant_id TEXT,candidate TEXT) STRICT");
    sql.exec("CREATE TABLE used(checkpoint TEXT PRIMARY KEY) STRICT");
    sql.exec("CREATE TABLE events(seq INTEGER PRIMARY KEY,op TEXT NOT NULL,digest TEXT UNIQUE NOT NULL,body TEXT NOT NULL) STRICT");
    sql.exec("INSERT INTO anchor VALUES(1,?,?,?,?,0,?,?,'READY',NULL,NULL,NULL)",this.env.WITNESS_STORE,this.env.WITNESS_IDENTITY,this.ctx.id.toString(),this.env.WITNESS_OWNER_PUBLIC,"0".repeat(64),"b".repeat(64));
   });await storage.sync();return new Response(this.ctx.id.toString(),{status:201});
  }catch{return new Response(null,{status:409});}
 }
}
export default {async fetch(req,e){
 if(new URL(req.url).pathname!=="/fixture/provision")return main.fetch(req,e);
 if(e.WITNESS_MODE!=="OFFLINE_V2"||req.method!=="POST")return new Response(null,{status:403});
 return e.CHECKPOINT_WITNESS.get(e.CHECKPOINT_WITNESS.idFromName(name)).fetch(req);
}};
