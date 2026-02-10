"""
Minimal JSON-RPC client for codex app-server.

Handles the 4 core operations:
- thread/start (new chat)
- thread/resume (continue chat)
- thread/list (list chats)
- thread/read (get history)
- turn/start (send message)
"""
import asyncio
import json
import logging
import subprocess
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Optional

from .config import settings

logger = logging.getLogger(__name__)


class AppServerClient:
    """Simple client for codex app-server JSON-RPC over stdio."""

    def __init__(
        self,
        subprocess_env: Optional[Dict[str, str]] = None,
        working_dir: Optional[Path] = None,
    ):
        self._process: Optional[asyncio.subprocess.Process] = None
        self._request_id: int = 0
        self._lock = asyncio.Lock()
        self._initialized = False
        self._subprocess_env = subprocess_env
        self._working_dir = working_dir

    async def _ensure_connected(self):
        """Start app-server process if not running."""
        if self._process is not None and self._process.returncode is None:
            return

        logger.info("Starting codex app-server process")
        codex_binary = settings.get_codex_binary()
        cwd = self._working_dir or settings.get_working_dir()
        env = self._subprocess_env or settings.get_subprocess_env()

        self._process = await asyncio.create_subprocess_exec(
            codex_binary, "app-server",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd),
            env=env,
        )

        # Initialize connection (required by app-server protocol)
        await self._initialize()

    async def _initialize(self):
        """Send initialize handshake and authenticate."""
        response = await self._send_request("initialize", {
            "clientInfo": {
                "name": "codex_api_bridge",
                "title": "Codex API Bridge",
                "version": "0.1.0"
            }
        })

        if "error" in response:
            raise RuntimeError(f"Failed to initialize: {response['error']}")

        # Send initialized notification
        await self._send_notification("initialized", {})
        logger.info("App-server initialized")

        # Authenticate with API key
        if settings.openai_api_key:
            auth_response = await self._send_request("account/login/start", {
                "type": "apiKey",
                "apiKey": settings.openai_api_key
            })
            if "error" in auth_response:
                logger.error(f"Authentication failed: {auth_response['error']}")
                raise RuntimeError(f"Failed to authenticate: {auth_response['error']}")
            logger.info("Authenticated with API key")

        self._initialized = True

    async def _send_request(
        self,
        method: str,
        params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Send request and wait for response."""
        async with self._lock:
            self._request_id += 1
            request_id = self._request_id

            request = {"method": method, "id": request_id}
            if params:
                request["params"] = params

            line = json.dumps(request) + "\n"
            logger.debug(f"Sending: {line.strip()}")

            self._process.stdin.write(line.encode())
            await self._process.stdin.drain()

            # Read lines until we get response with matching id
            while True:
                response_line = await self._process.stdout.readline()
                if not response_line:
                    raise RuntimeError("App-server process closed")

                decoded = response_line.decode().strip()
                if not decoded:
                    continue

                try:
                    data = json.loads(decoded)
                    logger.debug(f"Received: {decoded[:200]}")

                    # Check if this is our response
                    if data.get("id") == request_id:
                        return data
                    # Otherwise it's a notification, skip it for now

                except json.JSONDecodeError as e:
                    logger.warning(f"Invalid JSON: {decoded[:100]} - {e}")

    async def _send_notification(self, method: str, params: Optional[Dict] = None):
        """Send notification (no response expected)."""
        request = {"method": method}
        if params:
            request["params"] = params

        line = json.dumps(request) + "\n"
        self._process.stdin.write(line.encode())
        await self._process.stdin.drain()

    # =========================================================================
    # Core Operations
    # =========================================================================

    async def thread_start(self, model: Optional[str] = None) -> Dict[str, Any]:
        """
        Start a new thread.

        Returns: {"thread": {"id": "...", ...}}
        """
        await self._ensure_connected()

        params = {"approvalPolicy": "never"}  # Auto-approve for API
        if model:
            params["model"] = model

        response = await self._send_request("thread/start", params)

        if "error" in response:
            raise RuntimeError(f"thread/start failed: {response['error']}")

        return response.get("result", {})

    async def thread_resume(self, thread_id: str) -> Dict[str, Any]:
        """
        Resume an existing thread.

        Returns: {"thread": {"id": "...", ...}}
        """
        await self._ensure_connected()

        response = await self._send_request("thread/resume", {
            "threadId": thread_id
        })

        if "error" in response:
            raise RuntimeError(f"thread/resume failed: {response['error']}")

        return response.get("result", {})

    async def thread_list(
        self,
        limit: int = 50,
        cursor: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        List threads.

        Returns: {"data": [...], "nextCursor": ...}
        """
        await self._ensure_connected()

        params = {"limit": limit, "sortKey": "created_at"}
        if cursor:
            params["cursor"] = cursor

        response = await self._send_request("thread/list", params)

        if "error" in response:
            raise RuntimeError(f"thread/list failed: {response['error']}")

        return response.get("result", {})

    async def thread_read(self, thread_id: str) -> Dict[str, Any]:
        """
        Read thread with history.

        Returns: {"thread": {"id": "...", "turns": [...], ...}}
        """
        await self._ensure_connected()

        response = await self._send_request("thread/read", {
            "threadId": thread_id,
            "includeTurns": True
        })

        if "error" in response:
            raise RuntimeError(f"thread/read failed: {response['error']}")

        return response.get("result", {})

    async def turn_start_stream(
        self,
        thread_id: str,
        prompt: str,
        model: Optional[str] = None
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        Start a turn and stream events.

        Yields notification dicts until turn completes.
        """
        await self._ensure_connected()

        # Send turn/start request
        self._request_id += 1
        request_id = self._request_id

        params = {
            "threadId": thread_id,
            "input": [{"type": "text", "text": prompt}]
        }
        if model:
            params["model"] = model

        request = {"method": "turn/start", "id": request_id, "params": params}
        line = json.dumps(request) + "\n"

        logger.debug(f"Starting turn: {line.strip()}")
        self._process.stdin.write(line.encode())
        await self._process.stdin.drain()

        # Read and yield events until turn/completed
        while True:
            response_line = await self._process.stdout.readline()
            if not response_line:
                logger.error("App-server closed during turn")
                yield {"type": "error", "message": "App-server closed"}
                break

            decoded = response_line.decode().strip()
            if not decoded:
                continue

            try:
                data = json.loads(decoded)
                logger.debug(f"Turn event: {decoded[:150]}")

                # Check if this is our initial response
                if data.get("id") == request_id:
                    if "error" in data:
                        yield {"type": "error", "error": data["error"]}
                        break
                    # Yield the turn started info
                    yield {
                        "type": "turn.started",
                        "turn": data.get("result", {}).get("turn", {})
                    }
                    continue

                # It's a notification - yield it
                yield data

                # Check for turn completion
                method = data.get("method", "")
                if method == "turn/completed":
                    break

            except json.JSONDecodeError as e:
                logger.warning(f"Invalid JSON in stream: {decoded[:100]} - {e}")

    def check_availability(self) -> tuple[bool, Optional[str]]:
        """Check if codex binary is available."""
        try:
            binary = settings.get_codex_binary()
            result = subprocess.run(
                [binary, "--version"],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                return True, result.stdout.strip()
            return False, None
        except Exception as e:
            logger.error(f"Availability check failed: {e}")
            return False, None

    async def close(self):
        """Shutdown app-server process."""
        if self._process and self._process.returncode is None:
            logger.info("Shutting down app-server")
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._process.kill()
            self._process = None
            self._initialized = False

    def is_alive(self) -> bool:
        """Check if the app-server process is still running."""
        return self._process is not None and self._process.returncode is None


# Global client instance
client = AppServerClient()
