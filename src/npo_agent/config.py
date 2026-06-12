from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="NPO_",
        extra="ignore",
    )

    anthropic_api_key: str = Field(default="", validation_alias="ANTHROPIC_API_KEY")
    db_path: Path = Path("data/npo.sqlite")
    admin_token: str = "change-me-in-production"

    # Canadian residency flag — surfaces in /health so partners (BCSS, etc.) can audit.
    # When using Bedrock/Vertex this would map to ca-central-1 / northamerica-northeast1.
    data_residency: str = "ca-central"

    model_default: str = "claude-opus-4-7"
    # Haiku is cheaper for high-volume RAG answering; Opus is the default for grant drafting.
    model_fast: str = "claude-haiku-4-5"

    # Demo mode. When enabled, the API exposes /demo routes backed by a
    # pre-seeded BCSS tenant. Off by default — production deployments
    # should leave this off.
    demo_mode: bool = False
    demo_tenant_name: str = "BCSS Demo"

    # FitCoach demo mode. When enabled, the API auto-seeds the gym demo tenant
    # (locations + members + check-in history) and serves a known demo key to
    # the /coach and /dashboard UIs so a one-link deploy works with no login.
    fitness_demo_mode: bool = False
    fitness_demo_key: str = "npo_fitcoach_demo"


@lru_cache
def get_settings() -> Settings:
    return Settings()
