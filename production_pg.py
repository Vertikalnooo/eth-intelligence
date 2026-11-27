"""Offline PostgreSQL adapter. Unix-domain socket only, no remote DSN."""
from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
import os
import stat

class Denied(Exception):
    pass

HEX=re.compile(r"[0-9a-f]{32}")
SHA=re.compile(r"[0-9a-f]{40}")
OPS={"ISSUE","CONSUME","REVOKE","SUCCESS","UNKNOWN"}

def hex_id(value,pattern=HEX):
    if type(value) is not str or pattern.fullmatch(value) is None:
        raise Denied("invalid_identifier")
    return value

@dataclass(frozen=True)
class PgLedger:
    psql: Path
    socket_dir: Path
    port: int
    store_id: str
    candidate: str
    epoch: str

    def apply(self,op,grant):
        # Fixed local peer only. No environment credentials, no DSN, no shell,
        # no psql rc, no retries. Values are strict ASCII hex or fixed enum.
        if type(op) is not str or op not in OPS:
            raise Denied("invalid_operation")
        values=[hex_id(self.store_id),op,hex_id(grant),
                hex_id(self.epoch),hex_id(self.candidate,SHA)]
        if (not isinstance(self.psql,Path) or not self.psql.is_absolute()
                or not isinstance(self.socket_dir,Path) or not self.socket_dir.is_absolute()
                or re.fullmatch(r"/[A-Za-z0-9_./-]+",str(self.socket_dir)) is None
                or not self.socket_dir.is_dir()
                or self.socket_dir.resolve()!=self.socket_dir
                or type(self.port) is not int or not 1<=self.port<=65535):
            raise Denied("offline_pg_configuration")
        try:
            sock=self.socket_dir/(".s.PGSQL."+str(self.port))
            if not stat.S_ISSOCK(sock.lstat().st_mode):
                raise Denied("not_local_socket")
        except OSError as exc:
            raise Denied("local_socket_unavailable") from exc
        sql="SELECT ei.apply("+",".join("'"+v+"'" for v in values)+");"
        args=[str(self.psql),"-X","-qAt","-w","-v","ON_ERROR_STOP=1",
              "-h",str(self.socket_dir),"-p",str(self.port),"-d","postgres",
              "-U","ei_app","-f","-"]
        env={"PATH":"/usr/bin:/bin","LANG":"C","PGCONNECT_TIMEOUT":"2",
             "PGOPTIONS":"-c statement_timeout=2000 -c lock_timeout=1000 -c synchronous_commit=on"}
        try:
            result=subprocess.run(args,input=sql.encode(),capture_output=True,
                                  env=env,timeout=4,check=False)
        except (OSError,subprocess.TimeoutExpired) as exc:
            raise Denied("ledger_outcome_unknown") from exc
        if result.returncode!=0 or result.stdout.strip()!=b"OK":
            raise Denied("ledger_denied_or_unknown")
