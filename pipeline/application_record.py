"""The application record step: groups the pipeline's services as one application.

Project > Services in the experimental UI shows services as one application when a record says so: one hidden,
never-run task in the project (system tag `klique-application`) whose `Members` section lists the member task ids
(name = id, value = service name) and whose `Connections` section lists who calls whom (name = "<called id>><caller id>",
value = "uses"). This step writes that record from the service steps' outputs, so the record always names the
instances that are running now, including after a service was replaced. Nothing is written onto the services.
"""


def application_record(project: str, application: str, members: str, connections: str) -> str:
    """Create or update the application record, and return its id.

    members: one `service=<instance id>` per line (an empty id, a stopped service, is left out)
    connections: one `caller>called` per line, by service name
    """
    import re
    from clearml import Task

    session = Task.current_task().session

    def call(endpoint: str, body: dict) -> dict:
        service_name, action = endpoint.split(".", 1)
        r = session.send_request(service_name, action, json=body)
        if not r.ok:
            raise RuntimeError(f"{endpoint}: {r.status_code} {r.text[:300]}")
        return r.json()["data"]

    ids = {k.strip(): v.strip() for k, v in (line.split("=", 1) for line in members.splitlines() if "=" in line)
           if v.strip()}
    edges = [tuple(x.strip() for x in line.split(">", 1)) for line in connections.splitlines() if ">" in line]

    found = call("projects.get_all_ex", {"name": f"^{re.escape(project)}$", "only_fields": ["id", "name"]}).get("projects") or []
    project_id = next(p["id"] for p in found if p["name"] == project)
    records = call("tasks.get_all_ex", {"project": [project_id], "system_tags": ["klique-application"],
                                        "search_hidden": True, "only_fields": ["id", "name"]}).get("tasks") or []
    record = next((r["id"] for r in records if r["name"] == application), None)
    if not record:
        record = call("tasks.create", {
            "name": application, "project": project_id, "type": "custom",
            "system_tags": ["klique-application", "hidden"],
            "comment": "The application record: its members and connections. Written by the application's pipeline; "
                       "do not run."})["id"]
        print(f"application record {record} created", flush=True)
    call("tasks.edit_hyper_params", {"task": record, "replace_hyperparams": "section", "hyperparams": [
        *({"section": "Members", "name": iid, "value": name} for name, iid in ids.items()),
        *({"section": "Connections", "name": f"{ids[called]}>{ids[caller]}", "value": "uses"}
          for caller, called in edges if caller in ids and called in ids),
    ]})
    print(f"application {application!r} ({record}): {len(ids)} services, "
          f"{sum(1 for a, b in edges if a in ids and b in ids)} connections", flush=True)
    return record
