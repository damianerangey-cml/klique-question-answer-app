# Question and Answer App

A small question-and-answer app over a company's support articles, built to run on Klique AI as an application:
five services that work together, plus a pipeline that loads the articles.

| Service | What it is | Where its code is |
|---|---|---|
| answer-service | Takes a question, finds the closest articles, asks the chat model through guardrails. Serves the chat page. | `answer-service/` |
| guardrails | Screens questions before they reach the chat model. | `guardrails/` |
| chat-model | The chat model, served by a model deployment app. | a model, not code |
| embed-model | The embedding model, served by a model deployment app. | a model, not code |
| vector-store | Holds the articles' vectors. | the stock `qdrant/qdrant` image |

`pipeline/ingest_pipeline.py` versions `docs/` as a dataset, embeds every article and loads it into the vector store.

## How a change ships

1. Work on a branch. Run your copy of a service against the running ones to test it.
2. Open a pull request. When it merges, `.github/workflows/images.yml` builds each service's image and publishes it as
   `ghcr.io/damianerangey-cml/klique-question-answer-app/<service>:sha-<commit>`, with the standard labels
   `org.opencontainers.image.source` and `org.opencontainers.image.revision` naming this repository and that commit.
3. The service is moved to the new image on the platform. To go back, move it to the previous image.

## Settings

Every address comes from the service's environment; nothing is built in.

- answer-service: `EMBED_URL`, `VECTOR_URL`, `GUARDRAILS_URL`, `ROUTER_TOKEN`, optional `COLLECTION` (default `product-docs`). Port 8080.
- guardrails: `CHAT_URL`, `ROUTER_TOKEN`. Port 8000.
- pipeline: `EMBED_URL`, `VECTOR_URL`, `ROUTE_TOKEN`, optional `PROJECT`.

The support articles in `docs/` are made up, for a home router company.
