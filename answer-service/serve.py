"""Runs answer-service as a task, straight from this repository.

The platform clones the repository at the commit the task names and runs this file, so the task page shows the
repository, branch and commit the service runs. This file:

  1. reads the service's settings from the task's Environment section (Configuration tab): EMBED_URL, VECTOR_URL,
     CHAT_URL and the rest, as app.py documents them
  2. gets a router token for exactly the routes it calls (CALLS, comma separated route names) and renews it every
     day, so no token is handed to the service or written into its settings
  3. asks the platform for its address: on the static route ROUTE when set (an address that survives a replaced
     task), else on a route of its own
  4. serves app.py on PORT (default 8080)

The image in the Dockerfile runs app.py directly instead; both run the same code.
"""
import os
import threading
import time

from clearml import Task

task = Task.init()  # the task the platform is running
for name, value in (task.get_parameters_as_dict().get("Environment") or {}).items():
    os.environ.setdefault(name, str(value))
session = task.session


def router_token(route_names: list[str], seconds: int) -> str:
    routes = session.send_request("routers", "get_routes", json={}).json()["data"]["routes"]
    ids = [r["id"] for r in routes if r["name"] in route_names]
    missing = set(route_names) - {r["name"] for r in routes}
    if missing:
        raise RuntimeError(f"routes not found: {', '.join(sorted(missing))}")
    return session.send_request("auth", "generate_endpoint_router_token", json={
        "label": f"{task.name} calls", "expiration_sec": seconds, "endpoints": ids}).json()["data"]["token"]


calls = [r.strip() for r in os.environ.get("CALLS", "").split(",") if r.strip()]
os.environ["ROUTER_TOKEN"] = router_token(calls, 2 * 24 * 3600) if calls else ""

import app  # noqa: E402  (reads its settings from the environment at import)


def renew() -> None:
    while True:
        time.sleep(24 * 3600)
        try:
            app.HEADERS["Authorization"] = f"Bearer {router_token(calls, 2 * 24 * 3600)}"
        except Exception as e:  # keep serving on the old token; it is valid for another day
            print(f"router token renewal failed: {e}", flush=True)


if calls:
    threading.Thread(target=renew, daemon=True).start()

port = int(os.environ.get("PORT", "8080"))
task.request_external_endpoint(port=port, protocol="http", static_route=os.environ.get("ROUTE") or None)

import uvicorn  # noqa: E402

uvicorn.run(app.app, host="0.0.0.0", port=port)
