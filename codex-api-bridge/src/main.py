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
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import AsyncIterator, Optional

from pydantic import BaseModel
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.middleware.base import BaseHTTPMiddleware

from .config import settings
from .keycloak_auth import KeycloakIntrospectionError, introspect_token
from .agui_models import AGUIThreadInfo, AGUIThreadsResponse, HistoryResponse
from .agui_translate import (
    build_agui_response,
    build_done_delta,
    build_error_delta,
    build_history_response,
    build_initial_delta,
    translate_event,
)
from .models import (
    ChatRequest,
    StatusResponse,
)
from .session_manager import session_manager
from .user_store import close_users_collection, init_users_collection, verify_user_identity

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)
bearer_scheme = HTTPBearer(auto_error=False)


# =============================================================================
# Helpers
# =============================================================================

def _get_requested_user_id(request: Request, body_user_id: Optional[str] = None) -> Optional[str]:
    uid = request.query_params.get("user_id") or request.headers.get("X-User-Id")
    if uid and uid.strip():
        return uid.strip()
    if body_user_id and body_user_id.strip():
        return body_user_id.strip()
    return None


async def get_user_id(request: Request, body_user_id: Optional[str] = None) -> str:
    """Extract user_id from authenticated request.

    Resolution order:
    1. Keycloak mode: require user_id (query/header/body) and verify against MongoDB
    2. Non-Keycloak mode: Keycloak JWT 'sub' claim (if present)
    3. Multi-user mode (non-Keycloak): query/header
    4. Single-user mode: return 'default'
    """
    auth = getattr(request.state, "auth", None)
    if settings.security_method == "Keycloak":
        keycloak_id = auth.get("sub") if auth else None
        if not keycloak_id:
            raise HTTPException(status_code=401, detail="Unauthorized")
        requested_user_id = _get_requested_user_id(request, body_user_id)
        if not requested_user_id:
            raise HTTPException(
                status_code=400,
                detail="user_id is required. Provide via query param (?user_id=), X-User-Id header, or request body.",
            )
        await verify_user_identity(keycloak_id, requested_user_id)
        return requested_user_id

    if auth and auth.get("sub"):
        return auth["sub"]
    if settings.is_multi_user:
        requested_user_id = _get_requested_user_id(request, body_user_id)
        if requested_user_id:
            return requested_user_id
        raise HTTPException(
            status_code=400,
            detail="user_id is required. Provide via query param (?user_id=), X-User-Id header, or request body.",
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

        if request.url.path in ("/", "/status", "/docs", "/redoc", "/openapi.json"):
            return await call_next(request)

        unauthorized = JSONResponse(status_code=401, content={"detail": "Unauthorized"})

        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            logger.warning("Missing or invalid Authorization header: %s", request.url.path)
            return unauthorized

        token = auth_header.removeprefix("Bearer ").strip()
        if not token:
            logger.warning("Empty bearer token: %s", request.url.path)
            return unauthorized

        try:
            data = await introspect_token(token)
        except KeycloakIntrospectionError:
            logger.warning("Keycloak introspection failed: %s", request.url.path)
            return unauthorized

        if not data.get("active"):
            logger.warning("Inactive token: %s", request.url.path)
            return unauthorized

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

    if settings.security_method == "Keycloak":
        await init_users_collection()

    # Start session cleanup loop
    await session_manager.start_cleanup_loop()

    yield

    # Shutdown
    logger.info("Shutting down")
    await session_manager.shutdown()
    if settings.security_method == "Keycloak":
        await close_users_collection()


# =============================================================================
# App
# =============================================================================

app = FastAPI(
    title="Codex API Bridge",
    description="HTTP API for Codex",
    version="0.1.0",
    lifespan=lifespan,
)


# =============================================================================
# Stub request models (compatibility with cogentai-agent)
# =============================================================================

class RenameRequest(BaseModel):
    new_chat_name: str


class SearchRequest(BaseModel):
    query: str
    limit: int = 20

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


@app.get("/models")
async def list_models(
    request: Request,
    user_id: Optional[str] = Query(default=None, description="User ID"),
    _auth: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
):
    """Stub: List models (compatibility with cogentai-agent)."""
    _ = await get_user_id(request, user_id)
    logger.warning("models stub called")
    return {
        "providers": {
            "openai": [{
                "provider": "openai",
                "id": "gpt-5.2-codex",
                "name": "gpt-5.2-codex",
                "capabilities": {},
            }]
        },
        "default_model": "gpt-5.2-codex",
        "model_selection_enabled": False,
    }


@app.get("/threads", response_model=AGUIThreadsResponse)
async def list_threads(
    request: Request,
    user_id: Optional[str] = Query(default=None, description="User ID"),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: Optional[str] = Query(default=None),
    project_id: Optional[str] = Query(default=None, description="Project ID"),
    _auth: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
):
    """List all conversation threads for the authenticated user."""
    if project_id:
        raise HTTPException(status_code=400, detail="project_id is not supported by codex-api-bridge")
    user_id = await get_user_id(request)
    logger.info("Listing threads (user=%s, limit=%d)", user_id, limit)

    try:
        user_client = await session_manager.get_client(user_id)
        result = await user_client.thread_list(limit=limit, cursor=cursor)

        threads = []
        for item in result.get("data", []):
            threads.append(AGUIThreadInfo(
                thread_id=item.get("id", ""),
                chat_name=item.get("preview"),
                created_at=datetime.fromtimestamp(item["createdAt"]) if item.get("createdAt") else None,
                updated_at=datetime.fromtimestamp(item["updatedAt"]) if item.get("updatedAt") else None,
                message_count=0,
                last_message_preview=item.get("preview"),
                agent_type="codex",
                project_id=None,
                project_name=None,
            ))

        return AGUIThreadsResponse(
            threads=threads,
            total_count=len(threads),
        )

    except RuntimeError as e:
        if "Maximum concurrent sessions" in str(e):
            raise HTTPException(status_code=503, detail=str(e))
        logger.exception("Failed to list threads: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

    except Exception as e:
        logger.exception("Failed to list threads: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/conversations/{thread_id}/rename")
async def rename_conversation(
    request: Request,
    thread_id: str,
    body: RenameRequest,
    user_id: Optional[str] = Query(default=None, description="User ID"),
    _auth: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
):
    """Stub: Rename a conversation (compatibility with cogentai-agent)."""
    _ = await get_user_id(request, user_id)
    logger.warning("rename_conversation stub called for thread=%s", thread_id)
    return {"success": False, "detail": "Not implemented in codex-api-bridge"}


@app.delete("/conversations/{thread_id}")
async def delete_conversation(
    request: Request,
    thread_id: str,
    user_id: Optional[str] = Query(default=None, description="User ID"),
    _auth: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
):
    """Stub: Delete a conversation (compatibility with cogentai-agent)."""
    _ = await get_user_id(request, user_id)
    logger.warning("delete_conversation stub called for thread=%s", thread_id)
    return {"success": False, "detail": "Not implemented in codex-api-bridge"}


@app.post("/thread/search")
async def search_conversations(
    request: Request,
    body: SearchRequest,
    user_id: Optional[str] = Query(default=None, description="User ID"),
    _auth: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
):
    """Stub: Search conversations (compatibility with cogentai-agent)."""
    _ = await get_user_id(request, user_id)
    logger.warning("thread_search stub called for query=%s", body.query)
    return {"conversations": [], "total_found": 0, "query": body.query}


@app.get("/history", response_model=HistoryResponse)
async def get_history(
    request: Request,
    thread_id: str = Query(..., description="Thread ID"),
    user_id: Optional[str] = Query(default=None, description="User ID"),
    _auth: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
):
    """Get conversation history for a thread."""
    user_id = await get_user_id(request)
    logger.info("Getting history (user=%s, thread=%s)", user_id, thread_id)

    try:
        user_client = await session_manager.get_client(user_id)
        result = await user_client.thread_read(thread_id)
        thread = result.get("thread", {})

        return build_history_response(thread, thread_id, user_id)

    except RuntimeError as e:
        error_msg = str(e)
        if "Maximum concurrent sessions" in error_msg:
            raise HTTPException(status_code=503, detail=error_msg)
        # Default to 404 for thread operations (covers "not found", "not loaded", etc.)
        logger.warning("Thread error (user=%s, thread=%s): %s", user_id, thread_id, e)
        raise HTTPException(status_code=404, detail=f"Thread not found: {thread_id}")

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
    response_id = str(uuid.uuid4())
    reasoning_id = str(uuid.uuid4())
    state = {"reasoning_started": False}
    stream_done = False
    try:
        # Always send thread_id first so client knows which thread they're on
        yield f"data: {json.dumps(build_initial_delta(thread_id, response_id))}\n\n"

        async for event in user_client.turn_start_stream(thread_id, prompt, model):
            for ag_event in translate_event(event, response_id, reasoning_id, state):
                yield f"data: {json.dumps(ag_event)}\n\n"
                if ag_event.get("type") == "done":
                    stream_done = True
            if stream_done:
                break

        if not stream_done:
            yield f"data: {json.dumps(build_done_delta(response_id))}\n\n"

    except Exception as e:
        logger.exception("Stream error: %s", e)
        yield f"data: {json.dumps(build_error_delta(response_id, str(e)))}\n\n"
        yield f"data: {json.dumps(build_done_delta(response_id))}\n\n"


@app.post("/chat")
async def chat(request: Request, body: ChatRequest, _auth: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme)):
    """
    Send a message and get response.

    - Without thread_id: Creates new conversation
    - With thread_id: Continues existing conversation
    - stream=true (default): Returns SSE stream
    - stream=false: Returns complete response
    """
    # For /chat, body.user_id is also accepted as a fallback
    try:
        user_id = await get_user_id(request, body.user_id)
    except HTTPException:
        raise
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
                if "Maximum concurrent sessions" in str(e):
                    raise
                # Default to 404 for thread resume errors
                raise HTTPException(status_code=404, detail=f"Thread not found: {body.thread_id}")
        else:
            logger.debug("Creating new thread")
            # NOTE: model override intentionally ignored for now.
            result = await user_client.thread_start()
            thread_id = result.get("thread", {}).get("id")
            if not thread_id:
                raise HTTPException(status_code=500, detail="Failed to create thread")
            logger.info("Created thread: %s (user=%s)", thread_id, user_id)

        if body.stream:
            return StreamingResponse(
                # NOTE: model override intentionally ignored for now.
                sse_stream(user_client, thread_id, prompt, None),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                }
            )
        else:
            events = []
            # NOTE: model override intentionally ignored for now.
            async for event in user_client.turn_start_stream(thread_id, prompt, None):
                events.append(event)
            return build_agui_response(events, thread_id, user_id)

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
