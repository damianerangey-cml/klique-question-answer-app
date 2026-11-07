"""The whole Question and Answer App as one pipeline.

Four service steps and two job steps. The service steps leave their services running (see service_step.py); the job
steps run and finish, as pipeline steps always have:

    vector-store ──┐
    embed-model  ──┼──> answer-service      (service: calls the other three)
    chat-model   ──┘
    ingest-docs ─────> load-docs            (job: versions the docs, then loads them into the vector store)
    (load-docs also waits for embed-model and vector-store)

Running the pipeline applies it: a service whose description has not changed keeps running untouched, a changed one is
replaced once its new instance answers, and a service whose state is set to `stopped` is stopped. The addresses travel
from step to step as step outputs, so no service needs another's address written in by hand.

What can be changed from the pipeline's parameters, without touching the code:
  answer_service_image   the image answer-service runs (its CI tags one per commit: sha-<commit>)
  chat_model             the chat model (a GGUF file on Hugging Face)
  <service>_state        running or stopped, per service

Run it from a clone of this repository, or as a task created from it: python pipeline/application.py
"""
import glob
import json
import os

from clearml import PipelineController

from ingest_pipeline import embed_docs, ingest_docs
from service_step import service

PIPELINE = "Question and Answer App"
PROJECT = os.environ.get("PROJECT", "Question and Answer App")
QUEUE = os.environ.get("QUEUE", "1XCPU")
DOCS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs")
IMAGES = "ghcr.io/damianerangey-cml/klique-question-answer-app"
DEFAULT_IMAGE = f"{IMAGES}/answer-service:sha-b4a19fda88f368276f9d905bb330c5fff10f7c87"
CHAT_MODEL = "ggml-org/gemma-3-1b-it-GGUF/gemma-3-1b-it-Q4_K_M.gguf"
EMBED_MODEL = "nomic-ai/nomic-embed-text-v1.5-GGUF/nomic-embed-text-v1.5.Q4_K_M.gguf"


def load_docs(dataset_id: str, embed_url: str, vector_url: str, routes: str) -> int:
    """Loads the versioned docs into the vector store, with a router token for exactly the routes it calls."""
    import os
    from clearml import Task
    session = Task.current_task().session
    found = session.send_request("routers", "get_routes", json={}).json()["data"]["routes"]
    ids = [r["id"] for r in found if r["name"] in routes.split(",")]
    os.environ["ROUTE_TOKEN"] = session.send_request("auth", "generate_endpoint_router_token", json={
        "label": "load-docs", "expiration_sec": 3600, "endpoints": ids}).json()["data"]["token"]
    return embed_docs(dataset_id, embed_url, vector_url)


def llama(name: str, model: str, alias: str, embedding: bool = False) -> str:
    """Launch settings of the llama.cpp catalogue app, as its launch form sends them."""
    return json.dumps({
        "task_name": name, "project": PROJECT, "queue_name": QUEUE,
        "config_per_instance": [{
            "cli": "", "verbose": False, "no_mmap": False, "cont_batching": False, "embedding": embedding,
            "model": model, "alias": alias, "ngl": 9999, "temp": 0.8, "top_k": 40, "top_p": 0.9, "min_p": 0.9,
            "xtc_probability": 0, "xtc_threshold": 0.1, "typical": 1, "repeat_last_n": 64, "ctx_size": 8192,
            "rope_scaling": None, "rope_freq_base": None, "rope_freq_scale": None, "yarn_ext_factor": 1,
            "yarn_attn_factor": 1, "yarn_beta_fast": 32, "yarn_beta_slow": 1, "threads": None, "threads_batch": None,
            "tensor_split": "", "parallel": 1, "max_concurrent_requests": "10", "numa": None, "split_mode": "layer",
            "main_gpu": "",
        }],
        "debug": False, "log_disable": False, "enable_automatic_cpu_offloading": True,
        "enable_disk_model_swapping": False, "hf_token": "", "max_cuda_mem": "", "cuda_mem_mng_min_thr": "",
        "environment_vars_list": [], "max_idle_time_hour": "None", "last_activity_report_interval_seconds": 300,
    })


def container(name: str, image: str, port: int, command: str = "", working_dir: str = "") -> str:
    """Launch settings of the Containerized Application Launcher."""
    return json.dumps({
        "session_name": name, "project": PROJECT, "container": image, "command_line": command,
        "working_dir": working_dir, "queue_name": QUEUE, "router_type": "http", "router_internal_port": port,
        "max_idle_time_hour": "None", "storage_session": None, "environment_vars_list": [], "config_files": [],
        "run_as_root": True, "disable_clearml_venv": True, "continuous_console_logging": True,
        "monitor_endpoint": True, "setup_shell_script": None, "container_args": None, "session_tags": None,
        "continue_session": "", "storage_ref_session_id": "", "network_threshold": 0.01, "cpu_threshold": 10.0,
        "gpu_threshold": 10.0,
    })


def address(step: str) -> str:
    return "${" + step + ".parameters.Service/address}"


if __name__ == "__main__":
    docs = {os.path.basename(p): open(p).read() for p in sorted(glob.glob(os.path.join(DOCS_DIR, "*.md")))}
    pipe = PipelineController(name=PIPELINE, project=PROJECT, version="1.0.0", add_pipeline_tags=False)
    pipe.add_parameter("answer_service_image", DEFAULT_IMAGE, "the image answer-service runs")
    pipe.add_parameter("chat_model", CHAT_MODEL, "the chat model")
    pipe.add_parameter("docs_json", json.dumps(docs), "the product docs, from docs/ in the repository")
    for s in ("vector_store", "embed_model", "chat_model", "answer_service"):
        pipe.add_parameter(f"{s}_state", "running", f"{s.replace('_', '-')}: running or stopped")

    def service_step(step: str, parents: list[str] | None = None, **kwargs) -> None:
        name = step.replace("_", "-")
        pipe.add_function_step(
            name=step, function=service, parents=parents or [], cache_executed_step=False,
            function_kwargs=dict(pipeline=PIPELINE, name=name, route=f"qa-{name}", path=f"question-answer/{name}",
                                 state="${pipeline." + step + "_state}", **kwargs),
            function_return=["address"])

    service_step("vector_store", app="containerlaunch", health="/healthz",
                 launch=container("vector-store", "qdrant/qdrant:v1.12.4", 6333, "/qdrant/qdrant", "/qdrant"))
    service_step("embed_model", app="llama-cpp-model-deployment", health="/embed/v1/models",
                 launch=llama("embed-model", EMBED_MODEL, "embed", embedding=True))
    service_step("chat_model", app="llama-cpp-model-deployment", health="/chat/v1/models",
                 launch=llama("chat-model", "${pipeline.chat_model}", "chat"))
    service_step("answer_service", parents=["vector_store", "embed_model", "chat_model"],
                 app="containerlaunch", health="/", open_to="Users",
                 launch=container("answer-service", "${pipeline.answer_service_image}", 8080,
                                  "uvicorn app:app --host 0.0.0.0 --port 8080 --app-dir /app"),
                 env="\n".join([f"CHAT_URL={address('chat_model')}/chat",
                                f"EMBED_URL={address('embed_model')}/embed",
                                f"VECTOR_URL={address('vector_store')}"]),
                 token_env="ROUTER_TOKEN", token_routes="qa-chat-model,qa-embed-model,qa-vector-store")

    pipe.add_function_step(name="ingest_docs", function=ingest_docs, function_kwargs=dict(docs_json="${pipeline.docs_json}"),
                           function_return=["dataset_id"], cache_executed_step=False)
    pipe.add_function_step(name="load_docs", function=load_docs, helper_functions=[embed_docs],
                           parents=["ingest_docs", "embed_model", "vector_store"], cache_executed_step=False,
                           function_kwargs=dict(dataset_id="${ingest_docs.dataset_id}", embed_url=address("embed_model") + "/embed",
                                                vector_url=address("vector_store"), routes="qa-embed-model,qa-vector-store"),
                           function_return=["points"])
    pipe.start_locally(run_pipeline_steps_locally=True)
