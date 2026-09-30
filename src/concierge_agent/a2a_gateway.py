"""A2A card discovery and bounded requests to specialist services."""

import asyncio
from uuid import uuid4

from a2a.client import ClientConfig, ClientFactory
from a2a.client.card_resolver import A2ACardResolver
from a2a.types import GetTaskRequest, Message, Part, Role, SendMessageRequest, TaskState
import httpx


def _message_text(message: Message) -> str:
    return "\n".join(part.text for part in message.parts if part.text).strip()


def _task_text(task) -> str:
    """
    extracts the best available text answer from a task. 
    It checks artifacts first, then the status message, 
    then agent messages in the task history. 
    It returns the text as a string, or an empty string if none is found.
    """
    artifact_text = [
        part.text
        for artifact in task.artifacts
        for part in artifact.parts
        if part.text
    ]
    if artifact_text:
        return "\n".join(artifact_text).strip()
    status_text = _message_text(task.status.message)
    if status_text:
        return status_text
    return "\n".join(
        text
        for message in task.history
        if message.role == Role.ROLE_AGENT
        if (text := _message_text(message))
    ).strip()


class A2AGateway:
    def __init__(
        self,
        specialists: dict[str, str],
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.specialists = specialists
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    async def discover(self, agent_name: str) -> dict:
        async with httpx.AsyncClient(timeout=10, transport=self.transport) as http_client:
            card = await A2ACardResolver(
                http_client, self.specialists[agent_name]
            ).get_agent_card()
        return {
            "agent": agent_name,
            "name": card.name,
            "description": card.description,
            "skills": [
                {
                    "id": skill.id,
                    "name": skill.name,
                    "description": skill.description,
                    "tags": list(skill.tags),
                }
                for skill in card.skills
            ],
        }

    async def ask(self, agent_name: str, question: str) -> dict:
        if agent_name not in self.specialists:
            raise ValueError(f"Unknown specialist: {agent_name}")
        async with asyncio.timeout(self.timeout_seconds):
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds, transport=self.transport
            ) as http_client:
                
                # A2ACardResolver discover the agent's card - metadata that describes the agent, its skills, and how to communicate with it.
                card = await A2ACardResolver(
                    http_client, self.specialists[agent_name]
                ).get_agent_card()

                # ClientFactory turns the card into a usable connection to the agent.
                client = ClientFactory(
                    ClientConfig(streaming=False, httpx_client=http_client)
                ).create(card)

                # SendMessageRequest is the A2A request object to send a message to the agent.
                request = SendMessageRequest(
                    message=Message(
                        message_id=str(uuid4()),
                        role=Role.ROLE_USER,
                        parts=[Part(text=question)],
                    )
                )

                # client.send_message() sends the request to the agent and yields responses as they arrive.
                async for response in client.send_message(request):
                    # streaming=False  means a successful client.send_message(request) response contains either:
                    # message : the specialist replied directly.
                    # task : the specialist returned a task, whose status and result may need to be checked.

                    if response.HasField("message"):
                        answer = _message_text(response.message)
                        if answer:
                            return {"ok": True, "answer": answer}
                        return {"ok": False, "error": "Specialist returned an empty message"}
                    if response.HasField("task"):
                        task = response.task

                        # polls the specialist until its task is no longer pending or working.
                        while task.status.state in {
                            TaskState.TASK_STATE_SUBMITTED,
                            TaskState.TASK_STATE_WORKING,
                        }:
                            await asyncio.sleep(0.5)
                            task = await client.get_task(GetTaskRequest(id=task.id))
                        
                        state = TaskState.Name(task.status.state)
                        answer = _task_text(task)
                        if task.status.state == TaskState.TASK_STATE_COMPLETED:
                            if answer:
                                return {"ok": True, "status": state, "answer": answer}
                            return {
                                "ok": False,
                                "status": state,
                                "error": "Specialist completed without an answer",
                            }
                        return {
                            "ok": False,
                            "status": state,
                            "error": (
                                _message_text(task.status.message)
                                or answer
                                or f"Specialist task ended with {state}"
                            ),
                        }
                return {"ok": False, "error": "Specialist returned no A2A response"}
