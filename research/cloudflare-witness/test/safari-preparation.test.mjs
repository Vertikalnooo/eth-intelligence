import {test} from "node:test";
import assert from "node:assert/strict";
import {decode,encode,options,serialize,RP} from "../src/safari-preparation.mjs";
test("Safari preparation converts binary fields and rejects wrong RP/UV",()=>{
 const a=new Uint8Array([0,255,42]);assert.deepEqual(decode(encode(a)),a);
 const reg={challenge:encode(a),rp:{id:RP},user:{id:encode(a)},authenticatorSelection:{residentKey:"required",userVerification:"required"}};
 assert.deepEqual(options(JSON.stringify(reg),true).user.id,a);
 assert.throws(()=>options(JSON.stringify({...reg,rp:{id:"evil.invalid"}}),true));
 const auth={challenge:encode(a),rpId:RP,userVerification:"required",allowCredentials:[{id:encode(a)}]};
 assert.deepEqual(options(JSON.stringify(auth),false).allowCredentials[0].id,a);
 assert.throws(()=>options(JSON.stringify({...auth,userVerification:"preferred"}),false));
 const cred={id:encode(a),rawId:a.buffer,type:"public-key",response:{clientDataJSON:a.buffer,authenticatorData:a.buffer,signature:a.buffer,userHandle:null}};
 assert.equal(JSON.parse(serialize(cred,false)).response.signature,encode(a));
});
