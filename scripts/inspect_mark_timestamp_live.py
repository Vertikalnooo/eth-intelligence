"""Bounded read-only field inventory. Never establishes mark-tick provenance."""
import json
import time
from urllib.request import Request, urlopen

REST = "https://api.hyperliquid.xyz/info"
WS = "wss://api.hyperliquid.xyz/ws"
MAX_MESSAGES = 5
MAX_SECONDS = 15

def rest_fields():
    request = Request(REST, data=b'{"type":"metaAndAssetCtxs"}',
                      headers={"Content-Type": "application/json",
                               "User-Agent": "eth-intelligence-read-only-probe/0.1"},
                      method="POST")
    with urlopen(request, timeout=8) as response:
        data = json.load(response)
    if not isinstance(data, list) or len(data) != 2:
        raise ValueError("invalid_rest_shape")
    meta, contexts = data
    universe = meta.get("universe") if isinstance(meta, dict) else None
    if not isinstance(universe, list) or not isinstance(contexts, list) or len(universe) != len(contexts):
        raise ValueError("invalid_universe")
    indices = [i for i, item in enumerate(universe) if isinstance(item, dict) and item.get("name") == "ETH"]
    if len(indices) != 1 or not isinstance(contexts[indices[0]], dict):
        raise ValueError("invalid_eth_context")
    ctx = contexts[indices[0]]
    return {"source": "metaAndAssetCtxs", "eth_context_keys": sorted(ctx),
            "mark_price_present": "markPx" in ctx}

def ws_fields():
    import websocket
    conn = websocket.create_connection(WS, timeout=5, enable_multithread=True)
    observations = []
    try:
        conn.send(json.dumps({"method": "subscribe",
                              "subscription": {"type": "activeAssetCtx", "coin": "ETH"}}))
        deadline = time.monotonic() + MAX_SECONDS
        while len(observations) < MAX_MESSAGES and time.monotonic() < deadline:
            conn.settimeout(min(3, max(0.1, deadline - time.monotonic())))
            try:
                raw = conn.recv()
            except websocket.WebSocketTimeoutException:
                continue
            if not isinstance(raw, str) or len(raw) > 200_000:
                continue
            try:
                msg = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if not isinstance(msg, dict):
                continue
            channel = msg.get("channel")
            if channel != "activeAssetCtx":
                continue
            data = msg.get("data")
            if not isinstance(data, dict) or data.get("coin") != "ETH":
                continue
            ctx = data.get("ctx")
            if not isinstance(ctx, dict):
                continue
            observations.append({"channel": channel, "data_keys": sorted(data),
                                 "ctx_keys": sorted(ctx),
                                 "mark_price_present": "markPx" in ctx})
    finally:
        conn.close()
    return observations

def main():
    result = {"probe": "read_only_mark_field_inventory",
              "exchange_mark_timestamp_verified": False,
              "mark_price_freshness_verified": False,
              "recommendation_eligible": False,
              "timestamp_semantics": "not_established"}
    try:
        result["rest"] = rest_fields()
        result["ws"] = ws_fields()
        result["status"] = "observed" if result["ws"] else "no_matching_ws_messages"
    except Exception as exc:
        result["status"] = "probe_failed"
        result["error_type"] = type(exc).__name__
    print(json.dumps(result, sort_keys=True))
    if result["status"] == "probe_failed":
        raise SystemExit(1)

if __name__ == "__main__":
    main()
