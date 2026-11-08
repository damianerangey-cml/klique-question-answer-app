"""A service step: a pipeline step that leaves a service running.

A normal pipeline step runs to the end and stops. A service step makes sure one service is running as described, waits
until it answers, and finishes with the service's address as its output. The service keeps running after the
pipeline ends. Steps after it take the address as an input, the same way they take any other step's output.

Running the pipeline again applies it again. For each service step:

  * the same description and the service is running: it is left alone (no restart)
  * a changed description: a new instance is started, and once it answers the old one is stopped
  * `state="stopped"`: the running instance is stopped

The description is the app and its launch settings. Its fingerprint is kept on the instance as a system tag
(`spec:<hash>`), next to one naming the step (`service:<pipeline>/<step>`), so a later run finds the instance it
started. System tags, not tags: they are the pipeline's bookkeeping, and people's own tags stay theirs.

Each service gets a route of its own, created once and kept, so its address never changes, not even when the
instance behind it is replaced. Services that call other services get a router token for exactly the routes they
call. The token is added to the service's environment and left out of the fingerprint.

This file is a prototype of what the platform would provide as a step kind. `service` is written as one function
because a function step's code is copied into the step task as source.
"""


def service(pipeline: str, name: str, app: str, launch: str, route: str, path: str, health: str = "/",
            env: str = "", token_env: str = "", token_routes: str = "", open_to: str = "",
            state: str = "running") -> str:
    """Make sure the service `name` is running as described, and return its address.

    pipeline: the pipeline's name, so two pipelines can each have a step of the same name
    app: the catalogue app that runs the service (for example `containerlaunch`), or `task` for a task run straight
        from git
    launch: the app's launch settings, as JSON (what its launch form sends). For `task`: name, project, queue,
        repository, branch, commit (or default_commit when commit is empty), entry_point, working_dir, requirements
        (pip, one per line) and image. The task
        reads its settings from its Environment section (env, plus ROUTE, PORT and CALLS), gets its own router token
        for the routes in CALLS, and asks for its address on ROUTE (answer-service/serve.py shows how).
    route, path: the route's name and path under the router. The route is created if it does not exist.
    health: the path that answers 2xx once the service is ready
    env: extra environment variables, one `NAME=value` per line, added to the launch settings'
        `environment_vars_list` (for `task`: to its Environment section). This is where other steps' outputs come in.
    token_env, token_routes: put a router token for these routes (comma separated names) into the variable token_env.
        A `task` gets the route names as CALLS instead and issues its own token.
    open_to: a user group allowed to open the route in a browser (empty: API tokens only)
    state: `running` or `stopped`
    """
    import hashlib
    import json
    import time

    import requests
    from clearml import Task

    task = Task.current_task()
    session = task.session
    log = task.get_logger()

    def call(endpoint: str, body: dict) -> dict:
        service_name, action = endpoint.split(".", 1)
        r = session.send_request(service_name, action, json=body)
        if not r.ok:
            raise RuntimeError(f"{endpoint}: {r.status_code} {r.text[:300]}")
        return r.json()["data"]

    def say(text: str) -> None:
        print(f"[{name}] {text}", flush=True)

    def route_by_name(route_name: str) -> dict | None:
        found = call("routers.get_routes", {"name": f"^{route_name}$"}).get("routes") or []
        return next((r for r in found if r.get("name") == route_name), None)

    def ensure_route() -> dict:
        found = route_by_name(route)
        if found:
            return found
        body = {"name": route, "type": "path", "path": path.strip("/"), "auth_enabled": True,
                "load_balancer": {"enabled": False}, "enabled": True}
        if open_to:
            groups = [g["id"] for g in call("permissions.get_user_groups", {}).get("groups", [])
                      if g.get("name") == open_to]
            if not groups:
                raise RuntimeError(f"no user group named {open_to}")
            body["user_groups"] = groups
        call("routers.add_or_update_route", body)
        say(f"route {route} created at /{path.strip('/')}")
        return route_by_name(route)

    def router_token(route_names: list[str], label: str, seconds: int) -> str:
        ids = []
        for n in route_names:
            r = route_by_name(n)
            if not r:
                raise RuntimeError(f"route {n} does not exist (is the step that owns it before this one?)")
            ids.append(r["id"])
        return call("auth.generate_endpoint_router_token",
                    {"label": label, "expiration_sec": seconds, "endpoints": ids})["token"]

    def start_task(t: dict, bookkeeping: list[str]) -> str:
        """Creates the service as a task run straight from git, and enqueues it"""
        import re
        found = call("projects.get_all_ex", {"name": f"^{re.escape(t['project'])}$",
                                             "only_fields": ["id", "name"]}).get("projects") or []
        project = next((p["id"] for p in found if p["name"] == t["project"]), None) \
            or call("projects.create", {"name": t["project"]})["id"]
        created = call("tasks.create", {
            "name": t["name"], "project": project, "type": "service", "system_tags": bookkeeping,
            "comment": f"Started by the {pipeline!r} pipeline (step {name}). Run the pipeline again to change it.",
            "script": {"repository": t["repository"], "branch": t.get("branch", "main"), "version_num": t["commit"],
                       "entry_point": t["entry_point"], "working_dir": t.get("working_dir", "."), "binary": "python3",
                       "requirements": {"pip": t.get("requirements", "")}},
            "container": {"image": t.get("image", "python:3.11-slim")},
            "hyperparams": {"Environment": {k: {"section": "Environment", "name": k, "value": v}
                                            for k, v in t["environment"].items()}},
        })["id"]
        call("tasks.enqueue", {"task": created, "queue_name": t["queue"]})
        return created

    key = f"{pipeline}/{name}"
    running = call("tasks.get_all_ex", {
        "system_tags": [f"service:{key}"], "status": ["created", "queued", "in_progress"], "search_hidden": True,
        "only_fields": ["id", "status", "system_tags", "name"]}).get("tasks") or []

    if state == "stopped":
        for t in running:
            call("tasks.stop", {"task": t["id"], "force": True})
            say(f"stopped {t['id']}")
        # later steps reference these, so they are set (empty) for a stopped service too
        task.set_parameter("Service/address", "")
        task.set_parameter("Service/instance", "")
        task.set_parameter("Service/change", "stopped" if running else "unchanged")
        return ""

    settings = json.loads(launch)
    extra = [(k.strip(), v.strip()) for k, v in (line.split("=", 1) for line in env.splitlines() if "=" in line)]
    if app == "task":
        # an empty commit means the default (the pipeline's own), settled here so the fingerprint sees the real one
        settings["commit"] = settings.get("commit") or settings.get("default_commit", "")
        settings.pop("default_commit", None)
        if not settings["commit"]:
            raise RuntimeError(f"{name}: no commit to run")
        settings["environment"] = {**dict(extra), "ROUTE": route, "PORT": str(settings.get("port", 8080)),
                                   **({"CALLS": token_routes} if token_routes else {})}
    else:
        settings["route_name"] = route
        if extra:
            settings["environment_vars_list"] = [*settings.get("environment_vars_list", []),
                                                 *({"env_key": k, "env_val": v} for k, v in extra)]
    # the fingerprint covers what was asked for, not the token (a new token every run would restart every service)
    spec = hashlib.sha256(json.dumps({"app": app, "launch": settings, "route": route, "path": path,
                                      "open_to": open_to}, sort_keys=True).encode()).hexdigest()[:12]

    the_route = ensure_route()
    same = [t for t in running if f"spec:{spec}" in (t.get("system_tags") or [])]
    older = [t for t in running if t not in same]

    if same:
        instance = same[0]["id"]
        older += same[1:]
        say(f"unchanged: {instance} already runs spec {spec}")
        change = "unchanged"
    else:
        if app == "task":
            instance = start_task(settings, [f"service:{key}", f"spec:{spec}"])
        else:
            if token_env and token_routes:
                settings["environment_vars_list"] = [*settings.get("environment_vars_list", []), {
                    "env_key": token_env,
                    "env_val": router_token([r.strip() for r in token_routes.split(",") if r.strip()],
                                            f"{key} calls", 30 * 24 * 3600)}]
            instance = call("apps.launch_instance", {"app": app, "launch_params": settings})["instance"]
            own = call("tasks.get_all_ex", {"id": [instance], "search_hidden": True,
                                            "only_fields": ["system_tags"]})["tasks"][0].get("system_tags") or []
            call("tasks.update", {"task": instance, "system_tags": [*own, f"service:{key}", f"spec:{spec}"]})
        change = "replaced" if older else "started"
        say(f"{change}: launched {instance} with spec {spec}" + (f", replacing {', '.join(t['id'] for t in older)}" if older else ""))

    # wait until the instance runs, the route has an address, and the address answers
    check = router_token([route], f"{key} health check", 3600)
    deadline = time.time() + 45 * 60
    address, last = "", ""
    while True:
        info = call("tasks.get_all_ex", {"id": [instance], "search_hidden": True,
                                         "only_fields": ["status", "status_message"]})["tasks"][0]
        if info["status"] in ("failed", "stopped", "closed", "completed"):
            raise RuntimeError(f"{name} ({instance}) is {info['status']}: {info.get('status_message') or ''}")
        if not address:
            urls = (route_by_name(route) or {}).get("urls") or []
            address = (urls[0] if urls else "").rstrip("/")
        if info["status"] == "in_progress" and address:
            try:
                r = requests.get(address + health, timeout=20,
                                 headers={"Authorization": f"Bearer {check}", "Accept-Encoding": "identity"})
                if r.ok:
                    break
                now = f"{r.status_code} from {health}"
            except requests.RequestException as e:
                now = type(e).__name__
        else:
            now = info["status"] + ("" if address else ", no address yet")
        if now != last:
            say(f"waiting: {now}")
            last = now
        if time.time() > deadline:
            raise RuntimeError(f"{name} did not answer on {health} within 45 minutes ({last})")
        time.sleep(15)
    say(f"healthy at {address}")

    for t in older:
        call("tasks.stop", {"task": t["id"], "force": True})
        say(f"stopped the old instance {t['id']}")

    task.set_parameter("Service/address", address)
    task.set_parameter("Service/instance", instance)
    task.set_parameter("Service/spec", spec)
    task.set_parameter("Service/change", change)
    log.report_text(f"{name}: {change}, {instance}, {address}")
    return address
