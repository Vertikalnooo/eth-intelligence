"""Run only inside a loopback-only Linux network namespace."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

root=Path(__file__).resolve().parents[1]
if set(os.listdir("/sys/class/net"))!={"lo"}:
    # sysfs can reflect the original mount namespace; verify actual netns via ip.
    interfaces=subprocess.check_output(["ip","-o","link","show"],text=True).splitlines()
    if len(interfaces)!=1 or ": lo:" not in interfaces[0]:
        raise RuntimeError("offline namespace required")
with tempfile.TemporaryDirectory() as tmp:
    subprocess.run([sys.executable,"-m","venv",tmp],check=True)
    py=str(Path(tmp)/"bin/python")
    subprocess.run([py,"-m","pip","--isolated","install","--no-index","--find-links",sys.argv[1],
                    "-r",str(root/"research/requirements-production.txt")],check=True)
    subprocess.run([py,"-m","pip","check"],check=True)
    subprocess.run([py,"-m","pip","freeze"],check=True)
    env=dict(os.environ,EI_REAL_DEPS="1")
    subprocess.run([py,str(root/"scripts/check_offline_network_guard.py")],cwd=root,env=env,check=True)
    subprocess.run([py,"-m","unittest","discover","-s","tests","-v"],cwd=root,env=env,check=True)
