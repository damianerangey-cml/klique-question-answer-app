"""answer-service: answers a question from the product docs.

Embeds the question, finds the closest docs in the vector store, and asks the chat model through the guardrails
server. Every address comes from the environment, as a team would configure a real service.
"""
import os

import requests
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

EMBED_URL = os.environ["EMBED_URL"]              # embedding endpoint (OpenAI-compatible)
VECTOR_URL = os.environ["VECTOR_URL"]            # vector store
GUARDRAILS_URL = os.environ["GUARDRAILS_URL"]    # guardrails server (OpenAI-compatible)
# ask for plain responses: one hop through the router labels a body gzip without compressing it
HEADERS = {"Authorization": f"Bearer {os.environ['ROUTER_TOKEN']}", "Accept-Encoding": "identity"}
COLLECTION = os.environ.get("COLLECTION", "product-docs")

app = FastAPI(title="answer-service")


class Question(BaseModel):
    question: str


@app.get("/", response_class=HTMLResponse)
def chat_page():
    return open("/app/chat.html").read()


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/ask")
def ask(q: Question):
    vector = requests.post(f"{EMBED_URL}/v1/embeddings", headers=HEADERS, timeout=60,
                           json={"model": "embed", "input": f"search_query: {q.question}"}).json()["data"][0]["embedding"]
    hits = requests.post(f"{VECTOR_URL}/collections/{COLLECTION}/points/search", headers=HEADERS, timeout=30,
                         json={"vector": vector, "limit": 3, "with_payload": True}).json()["result"]
    context = "\n\n".join(h["payload"]["text"] for h in hits)
    reply = requests.post(f"{GUARDRAILS_URL}/v1/chat/completions", headers=HEADERS, timeout=180, json={
        "config_id": "support",
        # the articles go in a system message, so the input check screens only what the customer asked
        "messages": [{"role": "system", "content": f"Answer using only these support articles.\n\n{context}"},
                     {"role": "user", "content": q.question}],
    }).json()
    answer = reply["messages"][-1]["content"] if "messages" in reply else reply["choices"][0]["message"]["content"]
    return {"answer": answer, "sources": [h["payload"]["title"] for h in hits]}
