"""
Multi-user session manager for codex-api-bridge.

Maps user_id -> AppServerClient instance. Each user gets their own
codex app-server subprocess with isolated CODEX_HOME.
"""
import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional, Union

from .app_server_client import AppServerClient
from .config import settings

logger = logging.getLogger(__name__)


@dataclass
class UserSession:
    """Tracks a single user's codex app-server session."""
    user_id: str
    client: AppServerClient
    codex_home: Optional[Path]
    created_at: float = field(default_factory=time.monotonic)
    last_activity: float = field(default_factory=time.monotonic)

    def touch(self):
        """Update last activity timestamp."""
        self.last_activity = time.monotonic()

    @property
    def idle_seconds(self) -> float:
        """Seconds since last activity."""
        return time.monotonic() - self.last_activity


class SessionManager:
    """Manages per-user codex app-server sessions."""

    def __init__(self):
        self._sessions: Dict[str, UserSession] = {}
        self._locks: Dict[str, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()
        self._cleanup_task: Optional[asyncio.Task] = None

    async def get_client(self, user_id: str) -> AppServerClient:
        """Get or create an AppServerClient for the given user.

        Spawns a new codex app-server subprocess if one doesn't exist
        or if the existing one has died.

        Raises:
            RuntimeError: If max_sessions is reached.
        """
        # Fast path: session exists and process is alive
        session = self._sessions.get(user_id)
        if session is not None:
            if session.client.is_alive():
                session.touch()
                return session.client
            else:
                logger.warning(
                    "Session for user %s has a dead process, removing",
                    user_id,
                )
                await self._remove_session(user_id)

        # Slow path: need to create session (lock per user to avoid double-spawn)
        lock = await self._get_user_lock(user_id)
        async with lock:
            # Double-check after acquiring lock
            session = self._sessions.get(user_id)
            if session is not None and session.client.is_alive():
                session.touch()
                return session.client

            # Check capacity
            if (
                settings.max_sessions > 0
                and len(self._sessions) >= settings.max_sessions
            ):
                logger.error(
                    "Max sessions reached (%d), rejecting user %s",
                    settings.max_sessions,
                    user_id,
                )
                raise RuntimeError(
                    f"Maximum concurrent sessions ({settings.max_sessions}) reached"
                )

            # Create new session
            return await self._create_session(user_id)

    async def _get_user_lock(self, user_id: str) -> asyncio.Lock:
        """Get or create a per-user lock."""
        async with self._global_lock:
            if user_id not in self._locks:
                self._locks[user_id] = asyncio.Lock()
            return self._locks[user_id]

    async def _create_session(self, user_id: str) -> AppServerClient:
        """Create a new user session with isolated CODEX_HOME."""
        codex_home = settings.get_user_codex_home(user_id)

        # Create user directory if needed (None means default user using built-in ~/.codex)
        if codex_home is not None:
            try:
                codex_home.mkdir(parents=True, exist_ok=True)
                logger.info(
                    "Created CODEX_HOME for user %s: %s", user_id, codex_home
                )
            except OSError as e:
                logger.error(
                    "Failed to create CODEX_HOME for user %s: %s", user_id, e
                )
                raise RuntimeError(
                    f"Failed to create user directory: {e}"
                ) from e
        else:
            logger.info(
                "Using built-in CODEX_HOME for user %s", user_id
            )

        # Create per-user client with user-specific environment
        client = AppServerClient(
            subprocess_env=settings.get_user_subprocess_env(user_id),
            working_dir=settings.get_working_dir(),
        )

        session = UserSession(
            user_id=user_id,
            client=client,
            codex_home=codex_home,
        )
        self._sessions[user_id] = session

        logger.info(
            "Created session for user %s (total: %d)",
            user_id,
            len(self._sessions),
        )
        return client

    async def _remove_session(self, user_id: str):
        """Remove and clean up a user session."""
        session = self._sessions.pop(user_id, None)
        if session is not None:
            # Drain stderr before closing — may contain crash diagnostics
            if (
                session.client._process is not None
                and session.client._process.stderr is not None
            ):
                try:
                    stderr_data = await asyncio.wait_for(
                        session.client._process.stderr.read(4096),
                        timeout=1.0,
                    )
                    if stderr_data:
                        logger.warning(
                            "stderr from user %s process: %s",
                            user_id,
                            stderr_data.decode(errors="replace").strip()[:500],
                        )
                except (asyncio.TimeoutError, OSError):
                    pass  # Best-effort, don't block cleanup

            await session.client.close()
            logger.info(
                "Removed session for user %s (total: %d)",
                user_id,
                len(self._sessions),
            )

    async def cleanup_idle_sessions(self):
        """Remove sessions that have been idle beyond the timeout."""
        if settings.idle_timeout_seconds <= 0:
            return

        idle_users = [
            user_id
            for user_id, session in self._sessions.items()
            if session.idle_seconds > settings.idle_timeout_seconds
        ]

        for user_id in idle_users:
            logger.info(
                "Expiring idle session for user %s (idle %.0fs)",
                user_id,
                self._sessions[user_id].idle_seconds,
            )
            await self._remove_session(user_id)

    async def start_cleanup_loop(self):
        """Start the background cleanup task."""
        if settings.idle_timeout_seconds <= 0:
            logger.info("Idle timeout disabled, skipping cleanup loop")
            return

        async def _loop():
            while True:
                await asyncio.sleep(settings.cleanup_interval_seconds)
                try:
                    await self.cleanup_idle_sessions()
                    logger.info(
                        "Session cleanup tick: %d active sessions",
                        len(self._sessions),
                    )
                except Exception:
                    logger.exception("Error during session cleanup")

        self._cleanup_task = asyncio.create_task(_loop())
        logger.info(
            "Started cleanup loop (interval=%ds, timeout=%ds)",
            settings.cleanup_interval_seconds,
            settings.idle_timeout_seconds,
        )

    async def shutdown(self):
        """Shutdown all sessions and stop cleanup loop."""
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None

        user_ids = list(self._sessions.keys())
        for user_id in user_ids:
            await self._remove_session(user_id)

        logger.info("SessionManager shutdown complete")

    @property
    def active_session_count(self) -> int:
        """Number of active sessions."""
        return len(self._sessions)


# Global instance
session_manager = SessionManager()
