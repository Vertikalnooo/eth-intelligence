"""Safe rollback entrypoint candidate: never loads legacy app or upstream code."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import os

DENY={"exchange_freshness_verified":False,"recommendation_eligible":False,"stage2_complete":False}

def status():
    return {"source":"hyperliquid","state":"disabled","retry_after_seconds":0,
            "consecutive_rate_limits":0,"next_upstream_attempt_seconds":0,
            "cache_valid":False,"cache_remaining_seconds":0,
            "upstream_request_allowed":False,"throttle_reason":"rollback_disabled",
            **DENY,"rest_operational_freshness":{"state":"NO_DATA","age_ms":None,
            "clock_basis":"process_local_monotonic","source_channel":"REST",
            "reason_codes":["no_rest_receipt","rollback_disabled","missing_exchange_provenance"],**DENY}}

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def respond(self,code,body):
        payload=json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(payload)))
        self.send_header("Cache-Control","no-store")
        self.end_headers()
        if self.command!="HEAD":self.wfile.write(payload)
    def do_GET(self):
        if self.path in ("/","/health"):
            self.respond(200,{"status":"ok","service":"eth-intelligence","mode":"ROLLBACK_DISABLED"})
        elif self.path=="/market/status":self.respond(200,status())
        elif self.path=="/market/eth":self.respond(503,{"error":"market_data_unavailable",**DENY})
        else:self.respond(404,{"error":"not_found",**DENY})
    def do_HEAD(self):self.do_GET()
    def do_POST(self):self.respond(403,{"error":"disabled",**DENY})

if __name__=="__main__":
    ThreadingHTTPServer(("0.0.0.0",int(os.environ.get("PORT","10000"))),Handler).serve_forever()
