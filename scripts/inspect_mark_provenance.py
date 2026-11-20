"""Read-only live inspection of Hyperliquid ETH mark-price provenance.

Usage: python scripts/inspect_mark_provenance.py
Prints structural findings only; never asserts freshness or enables trading.
"""
import json
import ssl
from datetime import datetime, timezone
from urllib.request import Request, urlopen

URL = "https://api.hyperliquid.xyz/info"


def inspect(fetch=urlopen):
    request = Request(URL, data=b'{"type":"metaAndAssetCtxs"}',
                      headers={"Content-Type": "application/json",
                               "User-Agent": "eth-intelligence-provenance-probe/0.1"},
                      method="POST")
    with fetch(request, timeout=10) as response:
        payload = json.load(response)
    if not isinstance(payload, list) or len(payload) != 2:
        raise ValueError("unexpected_payload_shape")
    meta, contexts = payload
    universe = meta.get("universe") if isinstance(meta, dict) else None
    if not isinstance(universe, list) or not isinstance(contexts, list) or len(universe) != len(contexts):
        raise ValueError("invalid_universe")
    matches = [i for i, asset in enumerate(universe)
               if isinstance(asset, dict) and asset.get("name") == "ETH"]
    if len(matches) != 1 or not isinstance(contexts[matches[0]], dict):
        raise ValueError("eth_context_missing")
    ctx = contexts[matches[0]]
    mark = ctx.get("markPx")
    if mark is None:
        raise ValueError("mark_price_missing")
    # Keys are evidence of available fields, not of semantic timestamp provenance.
    return {
        "probe": "read_only_mark_provenance",
        "source": "hyperliquid_info_metaAndAssetCtxs",
        "asset": "ETH",
        "mark_price_present": True,
        "context_fields": sorted(ctx.keys()),
        "exchange_mark_timestamp_verified": False,
        "mark_price_freshness_verified": False,
        "recommendation_eligible": False,
        "conclusion": "field_inventory_only_no_authenticated_mark_tick_timestamp",
    }


if __name__ == "__main__":
    try:
        print(json.dumps(inspect(), sort_keys=True))
    except Exception as exc:
        print(json.dumps({"probe": "read_only_mark_provenance",
                          "status": "failed", "error_type": type(exc).__name__,
                          "recommendation_eligible": False}))
        raise SystemExit(1)
