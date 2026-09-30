"""A2A application and CLI entry point."""

from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
from google.adk.a2a.utils.agent_to_a2a import to_a2a
import uvicorn

from concierge_agent.agent import build_agent
from concierge_agent.config import Settings


def build_card(settings: Settings) -> AgentCard:
    return AgentCard(
        name="concierge_agent",
        description="Coordinate GitHub code research and Kubernetes inspection.",
        supported_interfaces=[
            AgentInterface(
                url=settings.public_base_url.rstrip("/"),
                protocol_binding="JSONRPC",
                protocol_version="1.0",
            )
        ],
        version="0.1.0",
        capabilities=AgentCapabilities(streaming=True),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[
            AgentSkill(
                id="cross_system_investigation",
                name="GitHub and Kubernetes investigation",
                description=(
                    "Combine repository implementation details with live Kubernetes "
                    "deployment, pod, service, and event information."
                ),
                tags=["github", "kubernetes", "orchestration"],
                examples=[
                    "Find the deployment in apiusage and explain how its repository implements its health check."
                ],
            ),
        ],
    )


def build_app(settings: Settings):
    settings.validate()
    return to_a2a(build_agent(settings), agent_card=build_card(settings), port=settings.port)


app = build_app(Settings.from_env())


def main() -> None:
    settings = Settings.from_env()
    uvicorn.run("concierge_agent.server:app", host="0.0.0.0", port=settings.port)

