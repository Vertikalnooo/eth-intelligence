"""Legacy Render WS test runner, now inert and health-only.

This compatibility entry point cannot launch a WebSocket probe, even if
its historical command is configured as a startup command or restarted.
Manual exchange probing always requires a separate explicit approval.
"""
from render_ws_stability_runner import main


if __name__ == "__main__":
    main()
