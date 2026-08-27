"""Application configuration loaded from environment variables."""

from pydantic_settings import BaseSettings, SettingsConfigDict

from agentcore.config.approval import ApprovalSettings
from agentcore.config.auth import AuthSettings
from agentcore.config.checkpoint import CheckpointSettings
from agentcore.config.database import DatabaseSettings
from agentcore.config.demo_tape import DemoTapeSettings
from agentcore.config.engine import EngineSettings
from agentcore.config.features import FeatureSettings
from agentcore.config.paths import ENV_FILE  # AGENTCORE_ENV → see paths.resolve_env_file
from agentcore.config.persistence import PersistenceSettings
from agentcore.config.platform import PlatformSettings
from agentcore.config.quota import QuotaSettings
from agentcore.config.search import SearchSettings
from agentcore.config.server import ServerSettings
from agentcore.config.workspace import WorkspaceSettings


class Settings(
    DatabaseSettings,
    PlatformSettings,
    SearchSettings,
    AuthSettings,
    ApprovalSettings,
    CheckpointSettings,
    EngineSettings,
    FeatureSettings,
    PersistenceSettings,
    QuotaSettings,
    ServerSettings,
    WorkspaceSettings,
    DemoTapeSettings,
    BaseSettings,
):
    """Flat settings object — fields grouped by domain mixin, env-loaded as one unit."""

    # env_file path is selected by AGENTCORE_ENV (paths.py); mixin field tables untouched.
    model_config = SettingsConfigDict(env_file=ENV_FILE, env_file_encoding="utf-8")


settings = Settings()
