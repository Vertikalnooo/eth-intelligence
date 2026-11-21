"""Read-only ETH mark field inventory. No tick provenance or trading authorization."""
import json
import time
from urllib.request import Request, urlopen

REST = "https://api.hyperliquid.xyz/info"
WS = "wss://api.hyperliquid.xyz/ws"
MAX_MESSAGES = 5
MAX_SECONDS = 15
MAX_WS_BYTES = 200_000
MAX_WS_SESSION_BYTES = 1_000_000

def context_keys(payload):
    if not isinstance(payload, list) or len(payload) != 2:
        raise ValueError("invalid_rest_shape")
    meta, contexts = payload
    universe = meta.get("universe") if isinstance(meta, dict) else None
    if not isinstance(universe, list) or not isinstance(contexts, list) or len(universe) != len(contexts):
        raise ValueError("invalid_universe")
    matches = [i for i, asset in enumerate(universe)
               if isinstance(asset, dict) and asset.get("name") == "ETH"]
    if len(matches) != 1 or not isinstance(contexts[matches[0]], dict):
        raise ValueError("invalid_eth_context")
    return {"source": "metaAndAssetCtxs", "eth_context_keys": sorted(contexts[matches[0]]),
            "mark_price_present": "markPx" in contexts[matches[0]]}

def parse_ws_message(raw):
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_WS_BYTES:
        return None
    try:
        message = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(message, dict) or message.get("channel") != "activeAssetCtx":
        return None
    data = message.get("data")
    if not isinstance(data, dict) or data.get("coin") != "ETH":
        return None
    ctx = data.get("ctx")
    if not isinstance(ctx, dict):
        return None
    return {"channel": "activeAssetCtx", "data_keys": sorted(data),
            "ctx_keys": sorted(ctx), "mark_price_present": "markPx" in ctx}

def rest_fields(timeout):
    request = Request(REST, data=b'{"type":"metaAndAssetCtxs"}',
                      headers={"Content-Type": "application/json",
                               "User-Agent": "eth-intelligence-read-only-probe/0.2"},
                      method="POST")
    with urlopen(request, timeout=timeout) as response:
        if response.headers.get("Content-Length") and int(response.headers["Content-Length"]) > 2_000_000:
            raise ValueError("rest_payload_too_large")
        raw = response.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError("rest_payload_too_large")
    return context_keys(json.loads(raw))

def enforce_frame_budget(conn, *, max_frame_bytes=MAX_WS_BYTES,
                         max_session_bytes=MAX_WS_SESSION_BYTES):
    """Reject oversized frame payloads before they are read into memory.

    websocket-client 1.8.0 exposes the frame_buffer length parser. Fail closed
    if that interface changes; fragmentation is bounded by a session budget.
    """
    frame_buffer = getattr(conn, "frame_buffer", None)
    original = getattr(frame_buffer, "recv_length", None)
    if not callable(original):
        raise RuntimeError("websocket_frame_limit_unavailable")
    consumed = 0

    def bounded_length():
        nonlocal consumed
        original()
        length = getattr(frame_buffer, "length", None)
        if not isinstance(length, int) or isinstance(length, bool) or length < 0:
            raise ValueError("invalid_websocket_frame_length")
        if length > max_frame_bytes or consumed + length > max_session_bytes:
            raise ValueError("websocket_frame_budget_exceeded")
        consumed += length

    frame_buffer.recv_length = bounded_length


def ws_fields(deadline):
    import websocket
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("deadline_expired")
    conn = websocket.create_connection(WS, timeout=min(5, remaining),
                                       enable_multithread=True)
    observations = []
    try:
        enforce_frame_budget(conn)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("deadline_expired")
        conn.settimeout(min(2, remaining))
        conn.send(json.dumps({"method": "subscribe",
                              "subscription": {"type": "activeAssetCtx", "coin": "ETH"}}))
        while len(observations) < MAX_MESSAGES:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            conn.settimeout(min(2, remaining))
            try:
                raw = conn.recv()
            except websocket.WebSocketTimeoutException:
                continue
            if time.monotonic() > deadline:
                raise TimeoutError("deadline_expired")
            parsed = parse_ws_message(raw)
            if parsed is not None:
                observations.append(parsed)
    finally:
        conn.close()
    return observations

def validate_report(report):
    return (isinstance(report, dict)
            and report.get("exchange_mark_timestamp_verified") is False
            and report.get("mark_price_freshness_verified") is False
            and report.get("recommendation_eligible") is False
            and report.get("timestamp_semantics") == "not_established"
            and report.get("rest_status") in ("ok", "error")
            and report.get("ws_status") in ("ok", "error", "skipped"))

def collect(rest=rest_fields, ws=ws_fields, clock=time.monotonic):
    deadline = clock() + MAX_SECONDS
    report = {"probe": "read_only_mark_field_inventory",
              "exchange_mark_timestamp_verified": False,
              "mark_price_freshness_verified": False,
              "recommendation_eligible": False, "timestamp_semantics": "not_established",
              "rest_status": "error", "ws_status": "skipped"}
    try:
        remaining = deadline - clock()
        if remaining <= 0:
            raise TimeoutError("deadline_expired")
        report["rest"] = rest(min(8, remaining))
        report["rest_status"] = "ok"
    except Exception as exc:
        report["rest_error_type"] = type(exc).__name__
    try:
        if deadline - clock() <= 0:
            raise TimeoutError("deadline_expired")
        report["ws"] = ws(deadline)
        report["ws_status"] = "ok"
    except Exception as exc:
        report["ws_status"] = "error"
        report["ws_error_type"] = type(exc).__name__
    return report

def main():
    report = collect()
    if not validate_report(report):
        raise ValueError("invalid_probe_report")
    print(json.dumps(report, sort_keys=True))
    if report["rest_status"] != "ok" or report["ws_status"] != "ok":
        raise SystemExit(1)

if __name__ == "__main__":
    main()
