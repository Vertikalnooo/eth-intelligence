"""Research Psycopg3 adapter. Loopback TLS only; no production credentials."""
from dataclasses import dataclass
from pathlib import Path
import re
from production_pg import Denied

FUNCS={"ARM":"arm","ISSUE":"issue","CONSUME":"consume","SUCCESS":"success","UNKNOWN":"unknown"}
ROLE={"ARM":"ei2_recovery","ISSUE":"ei2_issuer","CONSUME":"ei2_executor","SUCCESS":"ei2_finalizer","UNKNOWN":"ei2_finalizer"}
PATTERNS=(32,64,64,32,40,32)

@dataclass(frozen=True)
class LocalPg:
    port:int
    ca:Path
    user:str
    password:str

    def apply(self,op,values):
        if type(op) is not str or op not in FUNCS or self.user!=ROLE[op]:
            raise Denied("role_scope")
        if type(values) is not tuple or len(values)!=6 or any(
            type(v) is not str or re.fullmatch("[0-9a-f]{"+str(n)+"}",v) is None
            for v,n in zip(values,PATTERNS)):
            raise Denied("invalid_transition")
        if (type(self.port) is not int or not 1<=self.port<=65535
            or not isinstance(self.ca,Path) or not self.ca.is_absolute() or not self.ca.is_file()
            or type(self.password) is not str or not self.password):
            raise Denied("offline_config")
        conn=None
        try:
            import psycopg
            conn=psycopg.connect(host="127.0.0.1",hostaddr="127.0.0.1",port=self.port,
                dbname="postgres",user=self.user,password=self.password,
                sslmode="verify-full",sslrootcert=str(self.ca),channel_binding="require",
                connect_timeout=5,options="-c statement_timeout=3000 -c lock_timeout=1000 -c synchronous_commit=on",
                autocommit=False)
            with conn.cursor() as cur:
                cur.execute("SELECT ei2."+FUNCS[op]+"(%s,%s,%s,%s,%s,%s) = 'OK'",values)
                row=cur.fetchone()
                if type(row) is not tuple or len(row)!=1 or row[0] is not True:
                    raise Denied("transition_denied")
            conn.commit() # only acknowledgement permits caller to proceed
        except Exception as exc:
            # Never retry or query to silently infer commit outcome.
            raise Denied("transaction_unconfirmed") from exc
        finally:
            if conn is not None:
                try:conn.close()
                except Exception:pass
