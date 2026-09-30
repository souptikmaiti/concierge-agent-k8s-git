import asyncio
import json
from urllib.parse import urlparse

from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    Artifact,
    Message,
    Part,
    Role,
    SendMessageResponse,
    Task,
    TaskState,
    TaskStatus,
)
from google.protobuf.json_format import MessageToDict
import httpx
from starlette.testclient import TestClient

from concierge_agent.a2a_gateway import A2AGateway
from concierge_agent.agent import build_agent, make_tools
from concierge_agent.config import Settings
from concierge_agent.server import build_app


def test_card_and_agent_tools():
    settings = Settings()
    with TestClient(build_app(settings)) as client:
        response = client.get("/.well-known/agent-card.json")

    assert response.status_code == 200
    assert response.json()["skills"][0]["id"] == "cross_system_investigation"
    assert response.json()["supportedInterfaces"][0]["url"] == "http://localhost:8000"
    assert [tool.__name__ for tool in build_agent(settings).tools] == [
        "discover_agents",
        "delegate_requests",
    ]


def test_gateway_discovers_card_and_reads_completed_task():
    calls = []
    card = AgentCard(
        name="git_agent",
        description="Research GitHub code",
        version="0.1.0",
        capabilities=AgentCapabilities(streaming=True),
        supported_interfaces=[
            AgentInterface(
                url="http://agent.test",
                protocol_binding="JSONRPC",
                protocol_version="1.0",
            )
        ],
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[AgentSkill(id="code", name="Code research", description="Read code")],
    )
    completed = SendMessageResponse(
        task=Task(
            id="task-1",
            status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
            artifacts=[Artifact(artifact_id="answer", parts=[Part(text="Found repo X")])],
        )
    )

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, urlparse(str(request.url)).path))
        if request.method == "GET":
            return httpx.Response(200, json=MessageToDict(card))
        payload = json.loads(request.content)
        assert payload["method"] == "SendMessage"
        assert payload["params"]["message"]["parts"][0]["text"] == "Find repo X"
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": MessageToDict(completed),
            },
        )

    gateway = A2AGateway(
        {"git_agent": "http://agent.test"},
        timeout_seconds=10,
        transport=httpx.MockTransport(handle),
    )

    async def check():
        discovery = await gateway.discover("git_agent")
        result = await gateway.ask("git_agent", "Find repo X")
        return discovery, result

    discovery, result = asyncio.run(check())
    assert discovery["skills"][0]["id"] == "code"
    assert result["ok"] is True
    assert result["answer"] == "Found repo X"
    assert [method for method, _ in calls] == ["GET", "GET", "POST"]
    assert calls[0][1] == "/.well-known/agent-card.json"


def test_gateway_does_not_report_failed_task_as_success():
    failed = SendMessageResponse(
        task=Task(
            id="task-2",
            status=TaskStatus(
                state=TaskState.TASK_STATE_FAILED,
                message=Message(
                    message_id="error-1",
                    role=Role.ROLE_AGENT,
                    parts=[Part(text="Cluster unavailable")],
                ),
            ),
        )
    )
    card = AgentCard(
        name="k8s_agent",
        description="Inspect Kubernetes",
        version="0.1.0",
        capabilities=AgentCapabilities(),
        supported_interfaces=[
            AgentInterface(
                url="http://agent.test",
                protocol_binding="JSONRPC",
                protocol_version="1.0",
            )
        ],
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
    )

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=MessageToDict(card))
        payload = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": MessageToDict(failed),
            },
        )

    gateway = A2AGateway(
        {"k8s_agent": "http://agent.test"},
        timeout_seconds=10,
        transport=httpx.MockTransport(handle),
    )
    result = asyncio.run(gateway.ask("k8s_agent", "List deployments"))
    assert result["ok"] is False
    assert result["status"] == "TASK_STATE_FAILED"
    assert result["error"] == "Cluster unavailable"


def test_delegate_batches_run_concurrently_and_can_repeat_agent():
    class FakeGateway:
        specialists = {"git_agent": "http://git.test", "k8s_agent": "http://k8s.test"}

        def __init__(self):
            self.active = 0
            self.peak = 0
            self.calls = []

        async def discover(self, name):
            return {"agent": name, "skills": [{"id": name}]}

        async def ask(self, name, question):
            self.calls.append((name, question))
            self.active += 1
            self.peak = max(self.peak, self.active)
            await asyncio.sleep(0.01)
            self.active -= 1
            return {"ok": True, "answer": f"{name}: {question}"}

    gateway = FakeGateway()
    discover_agents, delegate_requests = make_tools(gateway)

    async def check():
        discovered = await discover_agents()
        first = await delegate_requests(
            [
                {"agent": "git_agent", "question": "Find repo"},
                {"agent": "k8s_agent", "question": "List deployments"},
                {"agent": "git_agent", "question": "Read implementation"},
            ]
        )
        second = await delegate_requests(
            [{"agent": "k8s_agent", "question": "Check related service"}]
        )
        return discovered, first, second

    discovered, first, second = asyncio.run(check())
    assert {entry["agent"] for entry in discovered["agents"]} == {
        "git_agent",
        "k8s_agent",
    }
    assert gateway.peak == 3
    assert [result["agent"] for result in first["results"]] == [
        "git_agent",
        "k8s_agent",
        "git_agent",
    ]
    assert second["results"][0]["answer"] == "k8s_agent: Check related service"
    assert len(gateway.calls) == 4
