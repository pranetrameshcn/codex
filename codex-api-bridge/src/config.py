"""Configuration management for Codex API Bridge."""
import os
import shutil
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings from environment variables."""

    # Security settings
    security_method: Optional[str] = None  # None | "Keycloak"

    # Keycloak settings
    keycloak_base_url: Optional[str] = None
    keycloak_realm: Optional[str] = None
    keycloak_client_id: Optional[str] = None
    keycloak_client_secret: Optional[str] = None
    keycloak_introspection_url: Optional[str] = None
    keycloak_timeout_seconds: int = 5

    # OpenAI API Key (required for Codex)
    openai_api_key: Optional[str] = None

    # Codex binary path (auto-detects if not set)
    codex_binary_path: Optional[str] = None

    # Working directory for Codex sessions
    codex_working_dir: Optional[Path] = None

    # Multi-user settings
    base_data_dir: Path = Path("./data/codex")
    max_sessions: int = 50
    idle_timeout_seconds: int = 300
    cleanup_interval_seconds: int = 60

    # Server settings
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False
    log_level: str = "INFO"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

    def get_codex_binary(self) -> str:
        """Get path to codex binary."""
        if self.codex_binary_path:
            path = Path(self.codex_binary_path)
            if path.exists():
                return str(path)

        # Auto-detect from PATH
        codex_path = shutil.which("codex")
        if codex_path:
            return codex_path

        raise RuntimeError(
            "Codex binary not found. Set CODEX_BINARY_PATH or add codex to PATH."
        )

    def get_working_dir(self) -> Path:
        """Get working directory for sessions."""
        if self.codex_working_dir:
            return self.codex_working_dir
        return Path.cwd()

    def get_subprocess_env(self) -> dict:
        """Get environment for app-server subprocess."""
        env = os.environ.copy()
        if self.openai_api_key:
            env["OPENAI_API_KEY"] = self.openai_api_key
        return env

    def get_user_codex_home(self, user_id: str) -> Path:
        """Get CODEX_HOME directory for a specific user.

        Returns {base_data_dir}/users/{user_id}/.codex/
        For the 'default' user in single-user mode, respects CODEX_HOME env var.
        """
        if user_id == "default":
            env_home = os.environ.get("CODEX_HOME")
            if env_home:
                return Path(env_home)
        return self.base_data_dir / "users" / user_id / ".codex"

    def get_user_subprocess_env(self, user_id: str) -> dict:
        """Get environment for a user's app-server subprocess."""
        env = os.environ.copy()
        if self.openai_api_key:
            env["OPENAI_API_KEY"] = self.openai_api_key
        env["CODEX_HOME"] = str(self.get_user_codex_home(user_id))
        return env

    def get_keycloak_introspection_url(self) -> str:
        """Get Keycloak token introspection URL."""
        if self.keycloak_introspection_url:
            return self.keycloak_introspection_url
        if not self.keycloak_base_url or not self.keycloak_realm:
            raise RuntimeError("Keycloak base URL or realm not configured.")
        base = self.keycloak_base_url.rstrip("/")
        return f"{base}/realms/{self.keycloak_realm}/protocol/openid-connect/token/introspect"


settings = Settings()
