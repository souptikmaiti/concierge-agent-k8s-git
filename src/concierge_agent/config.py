"""Configuration for the independently deployed concierge agent."""

from dataclasses import dataclass, field
import os
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    git_agent_url: str = "http://127.0.0.1:8001"
    k8s_agent_url: str = "http://127.0.0.1:8002"
    public_base_url: str = "http://localhost:8000"
    model: str = "gemini-3.6-flash"
    # Google recommends the default 1.0 for Gemini 3 to avoid degraded reasoning.
    temperature: float = 1.0
    specialist_timeout_seconds: float = 180.0
    a2a_task_database_url: str = field(
        default="postgresql+asyncpg://postgres@127.0.0.1:5432/concierge_agent_tasks",
        repr=False,
    )
    port: int = 8000

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(Path.cwd() / ".env", override=False)
        load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
        settings = cls(
            git_agent_url=os.getenv("GIT_AGENT_URL", cls.git_agent_url).strip(),
            k8s_agent_url=os.getenv("K8S_AGENT_URL", cls.k8s_agent_url).strip(),
            public_base_url=os.getenv("CONCIERGE_BASE_URL", cls.public_base_url).strip(),
            model=os.getenv("CONCIERGE_MODEL", cls.model).strip(),
            temperature=float(os.getenv("CONCIERGE_TEMPERATURE", "1.0")),
            specialist_timeout_seconds=float(
                os.getenv("SPECIALIST_TIMEOUT_SECONDS", "180")
            ),
            a2a_task_database_url=os.getenv(
                "A2A_TASK_DATABASE_URL", cls.a2a_task_database_url
            ).strip(),
            port=int(os.getenv("PORT", "8000")),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        for name, value in (
            ("GIT_AGENT_URL", self.git_agent_url),
            ("K8S_AGENT_URL", self.k8s_agent_url),
            ("CONCIERGE_BASE_URL", self.public_base_url),
        ):
            parsed = urlparse(value)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError(f"{name} must be an HTTP(S) URL")
        if not self.model:
            raise ValueError("CONCIERGE_MODEL is required")
        if not 0 <= self.temperature <= 1:
            raise ValueError("CONCIERGE_TEMPERATURE must be between 0 and 1")
        if self.specialist_timeout_seconds <= 0:
            raise ValueError("SPECIALIST_TIMEOUT_SECONDS must be positive")
        if not 1 <= self.port <= 65535:
            raise ValueError("PORT must be between 1 and 65535")
        database = urlparse(self.a2a_task_database_url)
        if (
            database.scheme != "postgresql+asyncpg"
            or not database.hostname
            or not database.path.strip("/")
        ):
            raise ValueError("A2A_TASK_DATABASE_URL must be a postgresql+asyncpg URL with a host and database")

    @property
    def specialists(self) -> dict[str, str]:
        return {
            "git_agent": self.git_agent_url.rstrip("/"),
            "k8s_agent": self.k8s_agent_url.rstrip("/"),
        }

