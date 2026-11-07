"""Docs ingestion: versions the product docs in docs/ as a dataset, then embeds every doc through the embedding model and
loads it into the vector store.

Run it from a clone of this repository (or as a task created from it): python pipeline/ingest_pipeline.py
The addresses come from the environment: EMBED_URL (the embedding model's route) and VECTOR_URL (the vector store's
route), and the route token as ROUTE_TOKEN. The pipeline lands in the project named by PROJECT.
The models may still be starting when this runs, so embedding waits until both services answer.
"""
import glob
import json
import os

from clearml import PipelineController

PROJECT = os.environ.get("PROJECT", "Question and Answer App")
DOCS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs")


def ingest_docs(docs_json: str) -> str:
    import json, os, tempfile
    from clearml import Dataset
    folder = tempfile.mkdtemp()
    for name, text in json.loads(docs_json).items():
        with open(os.path.join(folder, name), "w") as f:
            f.write(text)
    ds = Dataset.create(dataset_project=os.environ.get("PROJECT", "Question and Answer App"), dataset_name="Product docs")
    ds.add_files(folder)
    ds.upload()
    ds.finalize()
    return ds.id


def embed_docs(dataset_id: str, embed_url: str, vector_url: str) -> int:
    import glob, os, time, requests
    from clearml import Dataset
    headers = {"Authorization": f"Bearer {os.environ['ROUTE_TOKEN']}", "Accept-Encoding": "identity"}
    # wait for both services: they are launched with this task and take a few minutes to come up
    deadline = time.time() + 40 * 60
    while True:
        try:
            ok = (requests.get(f"{embed_url}/v1/models", headers=headers, timeout=20).ok
                  and requests.get(f"{vector_url}/collections", headers=headers, timeout=20).ok)
        except requests.RequestException:
            ok = False
        if ok:
            break
        if time.time() > deadline:
            raise RuntimeError("the embedding model or the vector store did not answer within 40 minutes")
        time.sleep(20)
    folder = Dataset.get(dataset_id=dataset_id, alias="product_docs").get_local_copy()
    docs = []
    for path in sorted(glob.glob(os.path.join(folder, "**", "*.md"), recursive=True)):
        text = open(path).read()
        docs.append({"title": text.splitlines()[0].lstrip("# ").strip(), "text": text})
    vectors = [requests.post(f"{embed_url}/v1/embeddings", headers=headers, timeout=120,
                             json={"model": "embed", "input": f"search_document: {d['text']}"}).json()["data"][0]["embedding"]
               for d in docs]
    requests.delete(f"{vector_url}/collections/product-docs", headers=headers, timeout=30)
    requests.put(f"{vector_url}/collections/product-docs", headers=headers, timeout=30,
                 json={"vectors": {"size": len(vectors[0]), "distance": "Cosine"}}).raise_for_status()
    points = [{"id": i, "vector": v, "payload": d} for i, (v, d) in enumerate(zip(vectors, docs))]
    requests.put(f"{vector_url}/collections/product-docs/points?wait=true", headers=headers, timeout=60,
                 json={"points": points}).raise_for_status()
    return len(points)


if __name__ == "__main__":
    assert os.environ.get("ROUTE_TOKEN"), "ROUTE_TOKEN is not set"
    docs = {os.path.basename(p): open(p).read() for p in sorted(glob.glob(os.path.join(DOCS_DIR, "*.md")))}
    pipe = PipelineController(name="Docs ingestion", project=PROJECT, version="1.0.0", add_pipeline_tags=False)
    pipe.add_parameter("docs_json", json.dumps(docs))
    pipe.add_parameter("embed_url", os.environ["EMBED_URL"])
    pipe.add_parameter("vector_url", os.environ["VECTOR_URL"])
    pipe.add_function_step(name="ingest_docs", function=ingest_docs, function_kwargs=dict(docs_json="${pipeline.docs_json}"),
                           function_return=["dataset_id"], cache_executed_step=False)
    pipe.add_function_step(name="embed_docs", function=embed_docs, parents=["ingest_docs"],
                           function_kwargs=dict(dataset_id="${ingest_docs.dataset_id}", embed_url="${pipeline.embed_url}",
                                                vector_url="${pipeline.vector_url}"),
                           function_return=["points"], cache_executed_step=False)
    pipe.start_locally(run_pipeline_steps_locally=True)
    print("pipeline done")
