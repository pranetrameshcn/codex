"""
Codex API Bridge - FastAPI server.

Minimal implementation with 4 core features:
- POST /chat - New or continue conversation (SSE stream)
- GET /threads - List conversations
- GET /history - Get conversation history
- GET /status - Health check
"""
import json
import logging
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from typing import AsyncIterator, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .config import settings
from .keycloak_auth import KeycloakIntrospectionError, introspect_token
from .models import (
    ChatRequest,
    StatusResponse,
    ThreadHistoryResponse,
    ThreadInfo,
    ThreadsResponse,
)
from .session_manager import session_manager

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)


# =============================================================================
# Helpers
# =============================================================================

def get_user_id(request: Request) -> str:
    """Extract user_id from authenticated request.

    Returns 'default' when authentication is disabled.
    """
    auth = getattr(request.state, "auth", None)
    if auth and auth.get("sub"):
        return auth["sub"]
    if settings.security_method == "Keycloak":
        logger.warning(
            "Keycloak auth active but no 'sub' claim found in token for %s",
            request.url.path,
        )
    return "default"


# =============================================================================
# Middleware
# =============================================================================

class KeycloakAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if settings.security_method is None or settings.security_method == "None":
            return await call_next(request)

        if settings.security_method != "Keycloak":
            return await call_next(request)

        if request.url.path in ("/", "/status"):
            return await call_next(request)

        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            logger.warning("Missing or invalid Authorization header: %s", request.url.path)
            raise HTTPException(status_code=401, detail="Unauthorized")

        token = auth_header.removeprefix("Bearer ").strip()
        if not token:
            logger.warning("Empty bearer token: %s", request.url.path)
            raise HTTPException(status_code=401, detail="Unauthorized")

        try:
            data = await introspect_token(token)
        except KeycloakIntrospectionError:
            logger.warning("Keycloak introspection failed: %s", request.url.path)
            raise HTTPException(status_code=401, detail="Unauthorized")

        if not data.get("active"):
            logger.warning("Inactive token: %s", request.url.path)
            raise HTTPException(status_code=401, detail="Unauthorized")

        request.state.auth = {
            "sub": data.get("sub"),
            "username": data.get("username"),
            "scope": data.get("scope"),
            "exp": data.get("exp"),
            "raw": data,
        }

        logger.info(
            "Authenticated request: user=%s path=%s",
            data.get("sub") or data.get("username") or "unknown",
            request.url.path,
        )

        return await call_next(request)


# =============================================================================
# Lifespan
# =============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown."""
    logger.info("Starting Codex API Bridge")

    # Log configuration
    if settings.openai_api_key:
        logger.info("OpenAI API key: configured")
    else:
        logger.warning("OPENAI_API_KEY not set!")

    if settings.security_method == "Keycloak":
        if not settings.keycloak_base_url or not settings.keycloak_realm:
            logger.warning("Keycloak base URL or realm not configured.")
        if not settings.keycloak_client_id or not settings.keycloak_client_secret:
            logger.warning("Keycloak client credentials not configured.")
        if (
            not settings.keycloak_base_url
            or not settings.keycloak_realm
            or not settings.keycloak_client_id
            or not settings.keycloak_client_secret
        ):
            logger.warning("Keycloak is not fully configured; authentication will fail.")

    # Check codex binary availability
    from .app_server_client import AppServerClient
    temp_client = AppServerClient()
    available, version = temp_client.check_availability()
    if available:
        logger.info("Codex binary: %s", version)
    else:
        logger.warning("Codex binary not found!")

    # Log multi-user settings
    logger.info(
        "Multi-user: base_data_dir=%s, max_sessions=%d, idle_timeout=%ds",
        settings.base_data_dir,
        settings.max_sessions,
        settings.idle_timeout_seconds,
    )

    # Start session cleanup loop
    await session_manager.start_cleanup_loop()

    yield

    # Shutdown
    logger.info("Shutting down")
    await session_manager.shutdown()


# =============================================================================
# App
# =============================================================================

app = FastAPI(
    title="Codex API Bridge",
    description="HTTP API for Codex",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(KeycloakAuthMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =============================================================================
# Routes
# =============================================================================

@app.get("/")
async def root():
    """API info."""
    return {
        "name": "Codex API Bridge",
        "version": "0.1.0",
        "endpoints": {
            "POST /chat": "Send message (new or continue)",
            "GET /threads": "List conversations",
            "GET /history": "Get conversation history",
            "GET /status": "Health check",
        }
    }


@app.get("/status", response_model=StatusResponse)
async def get_status():
    """Health check and status."""
    from .app_server_client import AppServerClient
    temp_client = AppServerClient()
    available, version = temp_client.check_availability()

    if available and settings.openai_api_key:
        status = "ok"
    elif available or settings.openai_api_key:
        status = "degraded"
    else:
        status = "unavailable"

    return StatusResponse(
        status=status,
        codex_available=available,
        codex_version=version,
        api_key_configured=bool(settings.openai_api_key),
    )


@app.get("/threads", response_model=ThreadsResponse)
async def list_threads(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: Optional[str] = Query(default=None),
):
    """List all conversation threads for the authenticated user."""
    user_id = get_user_id(request)
    logger.info("Listing threads (user=%s, limit=%d)", user_id, limit)

    try:
        user_client = await session_manager.get_client(user_id)
        result = await user_client.thread_list(limit=limit, cursor=cursor)

        threads = []
        for item in result.get("data", []):
            threads.append(ThreadInfo(
                thread_id=item.get("id", ""),
                preview=item.get("preview"),
                created_at=datetime.fromtimestamp(item["createdAt"]) if item.get("createdAt") else None,
                updated_at=datetime.fromtimestamp(item["updatedAt"]) if item.get("updatedAt") else None,
            ))

        return ThreadsResponse(
            threads=threads,
            next_cursor=result.get("nextCursor"),
        )

    except RuntimeError as e:
        if "Maximum concurrent sessions" in str(e):
            raise HTTPException(status_code=503, detail=str(e))
        logger.exception("Failed to list threads: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

    except Exception as e:
        logger.exception("Failed to list threads: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/history", response_model=ThreadHistoryResponse)
async def get_history(
    request: Request,
    thread_id: str = Query(..., description="Thread ID"),
):
    """Get conversation history for a thread."""
    user_id = get_user_id(request)
    logger.info("Getting history (user=%s, thread=%s)", user_id, thread_id)

    try:
        user_client = await session_manager.get_client(user_id)
        result = await user_client.thread_read(thread_id)
        thread = result.get("thread", {})

        return ThreadHistoryResponse(
            thread_id=thread.get("id", thread_id),
            preview=thread.get("preview"),
            turns=thread.get("turns", []),
            created_at=datetime.fromtimestamp(thread["createdAt"]) if thread.get("createdAt") else None,
        )

    except RuntimeError as e:
        error_msg = str(e)
        if "not found" in error_msg.lower():
            raise HTTPException(status_code=404, detail=f"Thread not found: {thread_id}")
        if "Maximum concurrent sessions" in error_msg:
            raise HTTPException(status_code=503, detail=error_msg)
        logger.exception("Failed to get history: %s", e)
        raise HTTPException(status_code=500, detail=error_msg)

    except Exception as e:
        logger.exception("Failed to get history: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


async def sse_stream(
    user_client,
    thread_id: str,
    prompt: str,
    model: Optional[str],
) -> AsyncIterator[str]:
    """Generate SSE events from turn."""
    try:
        # Always send thread_id first so client knows which thread they're on
        yield f"data: {json.dumps({'type': 'session', 'thread_id': thread_id})}\n\n"

        async for event in user_client.turn_start_stream(thread_id, prompt, model):
            yield f"data: {json.dumps(event)}\n\n"

            # Stop on completion or error
            method = event.get("method", event.get("type", ""))
            if method in ("turn/completed", "error"):
                break

        yield "data: [DONE]\n\n"

    except Exception as e:
        logger.exception("Stream error: %s", e)
        yield f"data: {json.dumps({'type': 'error', 'thread_id': thread_id, 'message': str(e)})}\n\n"
        yield "data: [DONE]\n\n"


@app.post("/chat")
async def chat(request: Request, body: ChatRequest):
    """
    Send a message and get response.

    - Without thread_id: Creates new conversation
    - With thread_id: Continues existing conversation
    - stream=true (default): Returns SSE stream
    - stream=false: Returns complete response
    """
    user_id = get_user_id(request)
    logger.info("Chat (user=%s, thread_id=%s, messages=%d)", user_id, body.thread_id, len(body.messages))

    prompt = body.get_prompt()
    if not prompt.strip():
        raise HTTPException(status_code=400, detail="Empty message")

    try:
        user_client = await session_manager.get_client(user_id)

        # Get or create thread
        if body.thread_id:
            logger.debug("Resuming thread: %s", body.thread_id)
            try:
                result = await user_client.thread_resume(body.thread_id)
                thread_id = result.get("thread", {}).get("id")
                if not thread_id:
                    raise HTTPException(status_code=500, detail="Failed to resume thread")
            except RuntimeError as e:
                if "not found" in str(e).lower():
                    raise HTTPException(status_code=404, detail=f"Thread not found: {body.thread_id}")
                raise
        else:
            logger.debug("Creating new thread")
            result = await user_client.thread_start(model=body.model)
            thread_id = result.get("thread", {}).get("id")
            if not thread_id:
                raise HTTPException(status_code=500, detail="Failed to create thread")
            logger.info("Created thread: %s (user=%s)", thread_id, user_id)

        if body.stream:
            return StreamingResponse(
                sse_stream(user_client, thread_id, prompt, body.model),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                }
            )
        else:
            events = []
            final_message = None

            async for event in user_client.turn_start_stream(thread_id, prompt, body.model):
                events.append(event)
                if event.get("method") == "item/completed":
                    item = event.get("params", {}).get("item", {})
                    if item.get("type") == "agentMessage":
                        final_message = item.get("text", "")

            return {
                "thread_id": thread_id,
                "message": final_message,
                "events": events,
            }

    except HTTPException:
        raise
    except RuntimeError as e:
        if "Maximum concurrent sessions" in str(e):
            raise HTTPException(status_code=503, detail=str(e))
        logger.exception("Chat failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.exception("Chat failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Entry point
# =============================================================================

def run():
    """Run with uvicorn."""
    import uvicorn
    uvicorn.run(
        "src.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    run()
