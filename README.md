# Concierge agent

A separate Google ADK service that exposes an A2A front door for the
`git-agent` and `k8s-agent` services. It communicates with their **A2A**
endpoints, not with their MCP servers. Each specialist remains an independent
repository and can be replaced with another A2A-compatible implementation.

At request time the concierge reads each specialist's A2A Agent Card and
advertised skills. The model chooses which specialist to ask. It can send up to
four independent specialist requests in one concurrent batch, including
multiple requests to the same specialist. For dependent questions it waits for
the first results and makes another call. It combines successful results into
a concise answer and identifies any specialist that could not be reached.

## Run locally

Start `git-agent` on port 8001 and `k8s-agent` on port 8002. Then from this
repository:

```sh
cp .env.example .env
# Set GOOGLE_API_KEY in .env. Adjust the specialist URLs if needed.
uv sync --locked
uv run --locked concierge-agent
```

The concierge listens on port 8000. Inspect its A2A card and try a request:

```sh
a2a card get http://127.0.0.1:8000
a2a send -a http://127.0.0.1:8000 \
  "List Deployments in apiusage and find the GitHub repository that implements apiusage-ingest-service."
```

| Variable | Default | Meaning |
| --- | --- | --- |
| `GOOGLE_API_KEY` | unset | Google model credential for the concierge |
| `GIT_AGENT_URL` | `http://127.0.0.1:8001` | Git specialist A2A base URL |
| `K8S_AGENT_URL` | `http://127.0.0.1:8002` | Kubernetes specialist A2A base URL |
| `CONCIERGE_BASE_URL` | `http://localhost:8000` | URL advertised in the concierge card |
| `CONCIERGE_MODEL` | `gemini-3.6-flash` | ADK model name |
| `CONCIERGE_TEMPERATURE` | `1.0` | Model sampling temperature |
| `SPECIALIST_TIMEOUT_SECONDS` | `180` | Total wait per specialist request |
| `PORT` | `8000` | A2A listening port |

The URLs must be reachable **from the concierge process**. In Docker or
Kubernetes, do not use `127.0.0.1` for separately running specialist services.
Each specialist's Agent Card must advertise a URL that the concierge can also
reach. Keep `.env` out of Git.

## Container and Helm

```sh
docker build -t concierge-agent:0.1.0 .
helm upgrade --install concierge-agent charts/concierge-agent \
  -n agent-tools --create-namespace \
  --set image.repository=YOUR_REGISTRY/concierge-agent \
  --set image.tag=0.1.0 \
  --set gitAgentUrl=http://git-agent.agent-tools.svc.cluster.local:8001 \
  --set k8sAgentUrl=http://k8s-agent.agent-tools.svc.cluster.local:8002 \
  --set existingSecret=YOUR_MODEL_SECRET
```

The referenced Kubernetes Secret needs a `GOOGLE_API_KEY` key. The chart
deploys only the concierge service and expects both specialists to be deployed
separately. The A2A task and session storage is in memory; use shared storage
before scaling beyond one replica.

## Limits

- The concierge only calls the two configured specialists. Their own tools and
  permissions determine what they can inspect.
- A2A card discovery happens at request time, so unavailable specialists can
  still be reported while the concierge starts normally.
- The model is instructed to batch independent work and make follow-up calls
  for dependent work. The delegation tool executes every request within a
  batch concurrently; it does not infer dependencies inside a batch.

