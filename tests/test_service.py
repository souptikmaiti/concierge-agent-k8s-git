import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import urlparse

from a2a.server.tasks import DatabaseTaskStore
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
from google.adk.sessions import DatabaseSessionService
from google.adk.sessions.state import State
import httpx
from starlette.testclient import TestClient

from concierge_agent.a2a_gateway import A2AGateway
from concierge_agent.agent import build_agent, make_tools
from concierge_agent.config import Settings
from concierge_agent.server import build_app


def test_a2a_runner_uses_postgresql_session_service():
    with patch("concierge_agent.server.to_a2a") as to_a2a:
        build_app(Settings())

    task_store = to_a2a.call_args.kwargs["task_store"]
    runner = to_a2a.call_args.kwargs["runner"]
    assert isinstance(runner.session_service, DatabaseSessionService)
    assert runner.session_service.db_engine is task_store.engine

    async def close():
        await runner.close()
        await task_store.engine.dispose()

    asyncio.run(close())


def test_card_and_agent_tools():
    settings = Settings()
    with (
        patch.object(DatabaseTaskStore, "initialize", new_callable=AsyncMock),
        patch.object(DatabaseSessionService, "prepare_tables", new_callable=AsyncMock),
        TestClient(build_app(settings)) as client,
    ):
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
            context_id="git-context-1",
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
        if len([method for method, _ in calls if method == "POST"]) == 1:
            assert "contextId" not in payload["params"]["message"]
        else:
            assert payload["params"]["message"]["contextId"] == "git-context-1"
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
        follow_up = await gateway.ask(
            "git_agent", "Find repo X", context_id=result["context_id"]
        )
        return discovery, result, follow_up

    discovery, result, follow_up = asyncio.run(check())
    assert discovery["skills"][0]["id"] == "code"
    assert result["ok"] is True
    assert result["answer"] == "Found repo X"
    assert result["context_id"] == follow_up["context_id"] == "git-context-1"
    assert [method for method, _ in calls] == ["GET", "GET", "POST", "GET", "POST"]
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

        async def ask(self, name, question, context_id=None):
            self.calls.append((name, question, context_id))
            call_number = len(self.calls)
            self.active += 1
            self.peak = max(self.peak, self.active)
            await asyncio.sleep(0.01)
            self.active -= 1
            return {
                "ok": True,
                "answer": f"{name}: {question}",
                "context_id": f"{name}-context-{call_number}",
            }

    gateway = FakeGateway()
    discover_agents, delegate_requests = make_tools(gateway)
    state_delta = {}
    tool_context = SimpleNamespace(state=State({}, state_delta))

    async def check():
        discovered = await discover_agents()
        first = await delegate_requests(
            [
                {"agent": "git_agent", "question": "Find repo"},
                {"agent": "k8s_agent", "question": "List deployments"},
                {"agent": "git_agent", "question": "Read implementation"},
            ],
            tool_context,
        )
        second = await delegate_requests(
            [
                {"agent": "k8s_agent", "question": "Check related service"},
                {"agent": "git_agent", "question": "Check another file"},
            ],
            tool_context,
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
    assert len(gateway.calls) == 5
    assert [context_id for _, _, context_id in gateway.calls] == [
        None,
        None,
        None,
        "k8s_agent-context-2",
        "git_agent-context-1",
    ]
    assert state_delta == {
        "specialist_context:git_agent": "git_agent-context-5",
        "specialist_context:k8s_agent": "k8s_agent-context-4",
    }
    assert all("context_id" not in item for item in first["results"] + second["results"])


def test_parallel_secondary_call_does_not_replace_saved_context():
    class FakeGateway:
        specialists = {"git_agent": "http://git.test"}

        async def ask(self, name, question, context_id=None):
            if question == "Primary question":
                assert context_id == "saved-context"
                raise RuntimeError("Primary request failed")
            assert context_id is None
            return {"ok": True, "answer": "Secondary answer", "context_id": "new-context"}

    _, delegate_requests = make_tools(FakeGateway())
    state_delta = {}
    tool_context = SimpleNamespace(
        state=State({"specialist_context:git_agent": "saved-context"}, state_delta)
    )
    result = asyncio.run(
        delegate_requests(
            [
                {"agent": "git_agent", "question": "Primary question"},
                {"agent": "git_agent", "question": "Secondary question"},
            ],
            tool_context,
        )
    )

    assert result["results"][0]["ok"] is False
    assert result["results"][1]["answer"] == "Secondary answer"
    assert tool_context.state["specialist_context:git_agent"] == "saved-context"
    assert state_delta == {}
