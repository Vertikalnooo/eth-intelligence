"""One-shot Cloudflare DISABLED deployment guard. No network during --check.
Cloud mutation is confined to explicit --deploy after separately gated approval.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.request

SOURCE="a4cba20e3308878989ae88c1a376837d99bf5e81"
BRANCH="refs/heads/research/cloudflare-disabled-deployment"
MARKER="research/cloudflare-disabled/APPROVED-ONCE.txt"
APPROVAL="APPROVE_DISABLED_ONCE "+SOURCE+" WORKERS_FREE_CONFIRMED\n"
NAME="eth-intelligence-witness"
CLASS="CheckpointWitness"
BINDING="CHECKPOINT_WITNESS"
FILES={"wrangler.jsonc":"d4843fd3122cc5f2c7d38d099753ea67bd38310f",
       "package.json":"d4d97b8791eb16761ebf42aaa1ed3354a2371f63",
       "package-lock.json":"3f89acc9d658d7538aa644568719dcc6db885be7",
       "src/hardened.mjs":"0e1d4f52368307f791ccc1ac7c904405be97d093"}
CONFIG={"name":NAME,"main":"src/hardened.mjs","compatibility_date":"2026-11-18",
        "workers_dev":False,"preview_urls":False,"vars":{"WITNESS_MODE":"DISABLED"},
        "durable_objects":{"bindings":[{"name":BINDING,"class_name":CLASS}]},
        "migrations":[{"tag":"v1","new_sqlite_classes":[CLASS]}],"observability":{"enabled":False}}

def require(condition,code):
    if not condition:raise RuntimeError(code)
def git(root,*args):
    return subprocess.check_output(["git","-C",str(root),*args],text=True,timeout=15).strip()
def blob(data):
    return hashlib.sha1(b"blob "+str(len(data)).encode()+b"\0"+data).hexdigest()
def check_source(source):
    require(git(source,"rev-parse","HEAD")==SOURCE,"wrong_source_commit")
    require(not git(source,"status","--porcelain","--untracked-files=no"),"dirty_source")
    root=source/"research/cloudflare-witness"
    for p,want in FILES.items():
        f=root/p
        require(f.is_file() and not f.is_symlink() and blob(f.read_bytes())==want,"source_blob_mismatch")
    require(json.loads((root/"wrangler.jsonc").read_text())==CONFIG,"unsafe_config")
    pkg=json.loads((root/"package.json").read_text())
    require(pkg.get("devDependencies")=={"wrangler":"4.165.0"} and not any(k in pkg.get("scripts",{}) for k in ("preinstall","install","postinstall","prepare")),"unsafe_package")
    lock=json.loads((root/"package-lock.json").read_text())
    require(all(not p or (x.get("resolved","").startswith("https://registry.npmjs.org/") and x.get("integrity","").startswith("sha")) for p,x in lock["packages"].items()),"unlocked_dependency")
    return root

def check_gate(control,event,env):
    require(env.get("GITHUB_EVENT_NAME")=="push" and env.get("GITHUB_REF")==BRANCH and env.get("GITHUB_RUN_ATTEMPT")=="1","not_first_approved_push")
    require(env.get("GITHUB_ACTOR")=="Vertikalnooo" and env.get("GITHUB_TRIGGERING_ACTOR")=="Vertikalnooo","wrong_actor")
    before,after=event.get("before",""),event.get("after","")
    require(all(re.fullmatch("[0-9a-f]{40}",x) and x!="0"*40 for x in (before,after)),"bad_push")
    require(event.get("ref")==BRANCH and event.get("forced") is False and event.get("created") is False and event.get("deleted") is False,"unexpected_push")
    require(after==env.get("GITHUB_SHA")==git(control,"rev-parse","HEAD"),"wrong_control_commit")
    require(git(control,"rev-list","--parents","-n","1",after)==after+" "+before,"not_single_commit")
    require(git(control,"diff","--name-status",before,after)==("A\t"+MARKER),"approval_not_single_new_file")
    require(not git(control,"log","-1","--format=%H",before,"--",MARKER),"approval_already_used")
    require(git(control,"show",after+":"+MARKER)+"\n"==APPROVAL and (control/MARKER).read_text()==APPROVAL,"approval_mismatch")

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise RuntimeError("unexpected_redirect")
def api(account,token,path):
    req=urllib.request.Request("https://api.cloudflare.com/client/v4/accounts/"+account+path,
        headers={"Authorization":"Bearer "+token,"Accept":"application/json"})
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    with opener.open(req,timeout=15) as res:
        data=res.read(2_000_001)
    require(len(data)<=2_000_000,"api_response_too_large")
    obj=json.loads(data)
    require(obj.get("success") is True and not obj.get("errors"),"api_unconfirmed")
    return obj
def items(obj):
    result=obj.get("result")
    require(type(result) is list,"api_list_shape")
    info=obj.get("result_info") or {}
    require(info.get("total_pages",1)<=1 and info.get("total_count",len(result))==len(result),"api_pagination_incomplete")
    return result
def target_namespaces(rows):
    require(all(type(x) is dict and type(x.get("script")) is str for x in rows),"namespace_shape")
    return [x for x in rows if x["script"]==NAME]
def preflight(get):
    scripts=items(get("/workers/scripts"))
    require(all(type(x) is dict and type(x.get("id")) is str for x in scripts),"script_shape")
    require(not any(x["id"]==NAME for x in scripts),"worker_already_exists")
    require(not target_namespaces(items(get("/workers/durable_objects/namespaces?page=1&per_page=1000"))),"namespace_already_exists")
def postflight(get):
    rows=target_namespaces(items(get("/workers/durable_objects/namespaces?page=1&per_page=1000")))
    require(len(rows)==1 and rows[0].get("class")==CLASS and rows[0].get("use_sqlite") is True and re.fullmatch("[0-9a-f]{32}",rows[0].get("id") or ""),"namespace_unverified")
    sub=get("/workers/scripts/"+NAME+"/subdomain")["result"]
    require(sub.get("enabled") is False and sub.get("previews_enabled") is False,"public_url_not_disabled")
    settings=get("/workers/scripts/"+NAME+"/settings")["result"]
    bindings=settings.get("bindings")
    require(type(bindings) is list and len(bindings)==2 and all(type(b) is dict for b in bindings),"unexpected_bindings")
    by={b.get("name"):b for b in bindings}
    require(set(by)=={"WITNESS_MODE",BINDING},"unexpected_bindings")
    require(by["WITNESS_MODE"].get("type")=="plain_text" and by["WITNESS_MODE"].get("text")=="DISABLED","mode_not_disabled")
    b=by[BINDING]
    require(b.get("type")=="durable_object_namespace" and b.get("class_name")==CLASS and b.get("script_name") in (None,NAME) and not b.get("dispatch_namespace") and not b.get("environment"),"binding_mismatch")
    if b.get("namespace_id") is not None:require(b["namespace_id"]==rows[0]["id"],"namespace_binding_mismatch")
    domains=items(get("/workers/domains"))
    require(all(type(d) is dict and type(d.get("service")) is str for d in domains),"domains_shape")
    require(not any(d["service"]==NAME for d in domains),"unexpected_custom_domain")
    # No identifiers in public Actions logs or artifacts.
    return True
def run():
    mode=sys.argv[1];control=Path(sys.argv[2]).resolve();source=Path(sys.argv[3]).resolve()
    root=check_source(source)
    if mode=="--check":
        print("Pinned source and DISABLED config verified");return
    require(mode=="--deploy","unknown_mode")
    event=json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    check_gate(control,event,os.environ)
    require(os.environ.get("WORKERS_FREE_CONFIRMED")=="YES","free_plan_owner_confirmation_required")
    account=os.environ.get("CLOUDFLARE_ACCOUNT_ID","");token=os.environ.get("CLOUDFLARE_API_TOKEN","")
    require(re.fullmatch("[0-9a-f]{32}",account) and len(token)>=20 and not any(c.isspace() for c in token),"missing_credentials")
    get=lambda p:api(account,token,p)
    preflight(get)
    cmd=[str(root/"node_modules/.bin/wrangler"),"deploy","--config","wrangler.jsonc"]
    env={k:os.environ[k] for k in ("PATH","HOME","CLOUDFLARE_ACCOUNT_ID","CLOUDFLARE_API_TOKEN") if k in os.environ}
    env.update(CI="true",WRANGLER_SEND_METRICS="false",CLOUDFLARE_INCLUDE_PROCESS_ENV="false")
    # Avoid leaking cloud identifiers or credentials via verbose CLI diagnostics.
    # No automatic retry on timeout, nonzero exit or failed verification.
    subprocess.run(cmd,cwd=root,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True,timeout=90)
    postflight(get)
    print("DISABLED deployment metadata verified; record private namespace ID in dashboard; revoke deploy token.")
if __name__=="__main__":
    try:run()
    except Exception:
        print("STOP: deployment not confirmed. Do not retry. Inspect dashboard and revoke token.",file=sys.stderr)
        sys.exit(1)
