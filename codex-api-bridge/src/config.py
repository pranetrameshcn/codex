"""Configuration management for Codex API Bridge."""
import os
import shutil
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings from environment variables."""

    # OpenAI API Key (required for Codex)
    openai_api_key: Optional[str] = None

    # Codex binary path (auto-detects if not set)
    codex_binary_path: Optional[str] = None

    # Working directory for Codex sessions
    codex_working_dir: Optional[Path] = None

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


settings = Settings()
