"""Typed settings: `config.yaml` for structure, environment variables (or `.env`) for secrets and overrides."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from collections.abc import Mapping

import yaml
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config.yaml"


def load_dotenv(path: Path) -> None:
    """Minimal .env loader. Non-empty environment variables win over the file.

    Reads UTF-8 with or without a byte-order mark, so files saved by Windows Notepad load too.
    """
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not os.environ.get(key):
            os.environ[key] = value.strip().strip('"').strip("'")


def missing_key_help(folder: Path | None = None) -> str:
    """Say where NEBIUS_API_KEY was looked for and the likely fix."""
    folder = folder or Path.cwd()
    env_file = folder / ".env"
    if env_file.exists():
        text = f"NEBIUS_API_KEY is empty in {env_file}: add the line NEBIUS_API_KEY=<your key> with no # in front."
    else:
        text = (f"No .env file in {folder}. Run this from the Ledgermind folder, or copy .env.example to .env "
                "and set NEBIUS_API_KEY=<your key>.")
    if (folder / ".env.txt").exists():
        text += " There is a .env.txt here: rename it to .env (Notepad adds .txt when saving)."
    return text


class ProviderConfig(BaseModel):
    name: str = "Nebius Token Factory"
    base_url: str = "https://api.tokenfactory.nebius.com/v1/"
    timeout_s: float = 60
    retries: int = 2
    backoff_s: float = 1.0
    max_retry_after_s: float = 20


class ModelConfig(BaseModel):
    id: str
    label: str
    price_in: float = 0.0
    price_out: float = 0.0
    thinking: bool = False


class AgentConfig(BaseModel):
    max_iterations: int = 6
    history_turns: int = 6
    memory_top_k: int = 8
    max_tokens: int = 1500
    temperature: float = 0.3


class PrivacyConfig(BaseModel):
    redact: bool = True
    strict_mode: bool = False
    max_rows_per_tool: int = 8


class MemoryConfig(BaseModel):
    min_confidence: float = 0.5
    stale_days: int = 120


class ProactiveConfig(BaseModel):
    enabled: bool = True
    interval_minutes: int = 60
    budget_alert_pct: int = 80


class Settings(BaseModel):
    provider: ProviderConfig = Field(default_factory=ProviderConfig)
    models: dict[str, ModelConfig]
    roles: dict[str, list[str]]
    agent: AgentConfig = Field(default_factory=AgentConfig)
    privacy: PrivacyConfig = Field(default_factory=PrivacyConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    proactive: ProactiveConfig = Field(default_factory=ProactiveConfig)
    currency: str = "INR"
    db_path: str = "ledgermind.db"
    api_key: str = ""
    force_mock: bool = False
    multiuser: bool = False
    data_dir: str = "data"

    @property
    def mock(self) -> bool:
        """Offline mode: no key configured, or explicitly forced."""
        return self.force_mock or not self.api_key

    def chain(self, role: str) -> list[ModelConfig]:
        """Ordered fallback chain of models for a role."""
        if role not in self.roles:
            raise KeyError(f"unknown model role {role!r}; known: {sorted(self.roles)}")
        return [self.models[alias] for alias in self.roles[role]]


def load_settings(path: Path | None = None, env: Mapping[str, str] | None = None) -> Settings:
    """Read config.yaml, then apply environment overrides."""
    env = os.environ if env is None else env
    path = Path(env.get("LEDGERMIND_CONFIG", path or DEFAULT_CONFIG))
    raw = {k: v for k, v in (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).items() if v is not None}
    settings = Settings(**raw)
    for alias, model in settings.models.items():
        override = env.get(f"LEDGERMIND_MODEL_{alias.upper()}")
        if override:
            model.id = override
    settings.api_key = env.get("NEBIUS_API_KEY", "")
    settings.provider.base_url = env.get("NEBIUS_BASE_URL", settings.provider.base_url)
    settings.force_mock = env.get("LEDGERMIND_MOCK", "0") == "1"
    settings.db_path = env.get("LEDGERMIND_DB", settings.db_path)
    settings.currency = env.get("LEDGERMIND_CURRENCY", settings.currency).upper()
    settings.multiuser = env.get("LEDGERMIND_MULTIUSER", "0") == "1"
    settings.data_dir = env.get("LEDGERMIND_DATA_DIR", settings.data_dir)
    if "LEDGERMIND_STRICT" in env:
        settings.privacy.strict_mode = env["LEDGERMIND_STRICT"] == "1"
    if "LEDGERMIND_PROACTIVE" in env:
        settings.proactive.enabled = env["LEDGERMIND_PROACTIVE"] == "1"
    return settings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings, loaded once (tests call `get_settings.cache_clear()`).

    `.env` is read from the current folder, then from the repo root, so scripts work from either.
    """
    load_dotenv(Path.cwd() / ".env")
    load_dotenv(DEFAULT_CONFIG.parent / ".env")
    return load_settings()
