import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import json
import subprocess

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("deploy_guard",ROOT/"research/cloudflare-disabled/deploy_guard.py")
g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)
class DeploymentGuardTests(unittest.TestCase):
    def test_source_config_pin(self):
        self.assertEqual(g.CONFIG["vars"],{"WITNESS_MODE":"DISABLED"})
        self.assertFalse(g.CONFIG["workers_dev"]);self.assertFalse(g.CONFIG["preview_urls"])
        self.assertEqual(g.CONFIG["main"],"src/hardened.mjs")
        self.assertEqual(g.CONFIG["migrations"],[{"tag":"v1","new_sqlite_classes":["CheckpointWitness"]}])
        # The approval marker is a permanent spent receipt after the first deployment.
        # Preparation may have no marker; an accepted marker must remain exact.
        marker=ROOT/g.MARKER
        if marker.exists():
            self.assertFalse(marker.is_symlink())
            self.assertEqual(marker.read_bytes(),g.APPROVAL.encode())
    def test_no_existing_worker_or_namespace(self):
        g.preflight(lambda _:{"result":[]})
        for scripts,ns in [([{"id":g.NAME}],[]),([],[{"script":g.NAME}])]:
            with self.assertRaises(RuntimeError):
                g.preflight(lambda p:{"result":ns if "namespaces" in p else scripts})
        with self.assertRaises(RuntimeError):
            g.preflight(lambda p:{"result":[],"result_info":{"total_pages":2}})
    def metadata(self):
        return {"/workers/durable_objects/namespaces?page=1&per_page=1000":{"result":[{"script":g.NAME,"class":g.CLASS,"use_sqlite":True,"id":"a"*32}]},
                "/workers/scripts/"+g.NAME+"/subdomain":{"result":{"enabled":False,"previews_enabled":False}},
                "/workers/scripts/"+g.NAME+"/settings":{"result":{"bindings":[{"name":"WITNESS_MODE","type":"plain_text","text":"DISABLED"},{"name":g.BINDING,"type":"durable_object_namespace","class_name":g.CLASS,"namespace_id":"a"*32}]}},
                "/workers/domains":{"result":[]}}
    def test_metadata_acceptance(self):
        d=self.metadata();self.assertTrue(g.postflight(d.__getitem__))
    def test_metadata_denies_wrong_mode_urls_sqlite_namespace(self):
        for path,key,value in [
            ("/workers/durable_objects/namespaces?page=1&per_page=1000","use_sqlite",False),
            ("/workers/scripts/"+g.NAME+"/subdomain","enabled",True),
            ("/workers/scripts/"+g.NAME+"/subdomain","previews_enabled",True),
            ("/workers/scripts/"+g.NAME+"/settings","text","OFFLINE_V2"),
            ("/workers/scripts/"+g.NAME+"/settings","namespace_id","b"*32),
        ]:
            d=self.metadata();result=d[path]["result"]
            if "settings" in path:result["bindings"][0 if key=="text" else 1][key]=value
            elif isinstance(result,list):result[0][key]=value
            else:result[key]=value
            with self.assertRaises(RuntimeError):g.postflight(d.__getitem__)
        d=self.metadata();d["/workers/domains"]["result"]=[{"service":g.NAME}]
        with self.assertRaises(RuntimeError):g.postflight(d.__getitem__)
    def test_approval_gate_exact_single_commit_and_no_reuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            def git(*a):return subprocess.check_output(["git","-C",tmp,*a],text=True,stderr=subprocess.DEVNULL).strip()
            git("init","-q");git("config","user.email","test@example.invalid");git("config","user.name","fixture")
            git("commit","--allow-empty","-m","base");before=git("rev-parse","HEAD")
            f=root/g.MARKER;f.parent.mkdir(parents=True);f.write_text(g.APPROVAL)
            git("add",".");git("commit","-m","approval");after=git("rev-parse","HEAD")
            event={"before":before,"after":after,"ref":g.BRANCH,"forced":False,"created":False,"deleted":False}
            env={"GITHUB_EVENT_NAME":"push","GITHUB_REF":g.BRANCH,"GITHUB_RUN_ATTEMPT":"1","GITHUB_ACTOR":"Vertikalnooo","GITHUB_TRIGGERING_ACTOR":"Vertikalnooo","GITHUB_SHA":after}
            g.check_gate(root,event,env)
            for k,v in [("GITHUB_RUN_ATTEMPT","2"),("GITHUB_REF","refs/heads/main"),("GITHUB_ACTOR","other")]:
                with self.assertRaises(RuntimeError):g.check_gate(root,event,dict(env,**{k:v}))
            with self.assertRaises(RuntimeError):g.check_gate(root,dict(event,forced=True),env)
            git("rm",g.MARKER);git("commit","-m","remove");before=git("rev-parse","HEAD")
            f.parent.mkdir(parents=True,exist_ok=True);f.write_text(g.APPROVAL);git("add",".");git("commit","-m","readd");after=git("rev-parse","HEAD")
            with self.assertRaisesRegex(RuntimeError,"approval_already_used"):
                g.check_gate(root,dict(event,before=before,after=after),dict(env,GITHUB_SHA=after))
