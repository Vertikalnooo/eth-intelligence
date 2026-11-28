"""Offline public ETH market parser. No transport or credentials."""
import json
import re
from decimal import Decimal

def number(value, *, signed=False, positive=False):
    if type(value) is not str or len(value)>64 or not re.fullmatch(r"-?[0-9]+(?:\.[0-9]+)?",value):
        raise ValueError("invalid decimal")
    n=Decimal(value)
    if (not signed and n<0) or (positive and n<=0):
        raise ValueError("invalid range")
    return str(n)

def parse_context(raw):
    if type(raw) is not bytes or len(raw)>2_000_000:
        raise ValueError("payload size")
    def unique(pairs):
        result={}
        for k,v in pairs:
            if k in result:
                raise ValueError("duplicate key")
            result[k]=v
        return result
    data=json.loads(raw,object_pairs_hook=unique)
    if type(data) is not list or len(data)!=2:
        raise ValueError("envelope")
    meta,contexts=data
    if type(meta) is not dict or type(meta.get("universe")) is not list or type(contexts) is not list:
        raise ValueError("schema")
    universe=meta["universe"]
    if len(universe)!=len(contexts) or any(type(x) is not dict or type(x.get("name")) is not str for x in universe):
        raise ValueError("universe")
    indices=[i for i,x in enumerate(universe) if x["name"]=="ETH"]
    if len(indices)!=1 or type(contexts[indices[0]]) is not dict:
        raise ValueError("ETH context")
    ctx=contexts[indices[0]]
    return {"symbol":"ETH/USDC","market_type":"perpetual","source":"hyperliquid",
            "mark_price":number(ctx.get("markPx"),positive=True),
            "funding_rate":number(ctx.get("funding"),signed=True),
            "open_interest":number(ctx.get("openInterest")),
            "daily_notional_volume":number(ctx.get("dayNtlVlm")),
            "exchange_timestamp":None,"exchange_freshness_verified":False,
            "recommendation_eligible":False,"stage2_complete":False,
            "mode":"OFFLINE_FIXTURE"}
