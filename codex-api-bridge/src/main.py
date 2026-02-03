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

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .config import settings
from .models import (
    ChatRequest,
    StatusResponse,
    ThreadHistoryResponse,
    ThreadInfo,
    ThreadsResponse,
)
from .app_server_client import client

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown."""
    logger.info("Starting Codex API Bridge")

    # Check configuration
    if settings.openai_api_key:
        logger.info("OpenAI API key: configured")
    else:
        logger.warning("OPENAI_API_KEY not set!")

    available, version = client.check_availability()
    if available:
        logger.info(f"Codex binary: {version}")
    else:
        logger.warning("Codex binary not found!")

    yield

    # Shutdown
    logger.info("Shutting down")
    await client.close()


app = FastAPI(
    title="Codex API Bridge",
    description="HTTP API for Codex",
    version="0.1.0",
    lifespan=lifespan,
)

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
    available, version = client.check_availability()

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
    limit: int = Query(default=50, ge=1, le=200),
    cursor: Optional[str] = Query(default=None),
):
    """List all conversation threads."""
    logger.info(f"Listing threads (limit={limit})")

    try:
        result = await client.thread_list(limit=limit, cursor=cursor)

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

    except Exception as e:
        logger.exception(f"Failed to list threads: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/history", response_model=ThreadHistoryResponse)
async def get_history(
    thread_id: str = Query(..., description="Thread ID"),
):
    """Get conversation history for a thread."""
    logger.info(f"Getting history for: {thread_id}")

    try:
        result = await client.thread_read(thread_id)
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
        logger.exception(f"Failed to get history: {e}")
        raise HTTPException(status_code=500, detail=error_msg)

    except Exception as e:
        logger.exception(f"Failed to get history: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def sse_stream(thread_id: str, prompt: str, model: Optional[str]) -> AsyncIterator[str]:
    """Generate SSE events from turn."""
    try:
        # Always send thread_id first so client knows which thread they're on
        yield f"data: {json.dumps({'type': 'session', 'thread_id': thread_id})}\n\n"

        async for event in client.turn_start_stream(thread_id, prompt, model):
            yield f"data: {json.dumps(event)}\n\n"

            # Stop on completion or error
            method = event.get("method", event.get("type", ""))
            if method in ("turn/completed", "error"):
                break

        yield "data: [DONE]\n\n"

    except Exception as e:
        logger.exception(f"Stream error: {e}")
        yield f"data: {json.dumps({'type': 'error', 'thread_id': thread_id, 'message': str(e)})}\n\n"
        yield "data: [DONE]\n\n"


@app.post("/chat")
async def chat(request: ChatRequest):
    """
    Send a message and get response.

    - Without thread_id: Creates new conversation
    - With thread_id: Continues existing conversation
    - stream=true (default): Returns SSE stream
    - stream=false: Returns complete response
    """
    logger.info(f"Chat: thread_id={request.thread_id}, messages={len(request.messages)}")

    prompt = request.get_prompt()
    if not prompt.strip():
        raise HTTPException(status_code=400, detail="Empty message")

    try:
        # Get or create thread
        if request.thread_id:
            # Continue existing thread
            logger.debug(f"Resuming thread: {request.thread_id}")
            try:
                result = await client.thread_resume(request.thread_id)
                # Always use ID from Codex response, not user input
                thread_id = result.get("thread", {}).get("id")
                if not thread_id:
                    raise HTTPException(status_code=500, detail="Failed to resume thread")
            except RuntimeError as e:
                if "not found" in str(e).lower():
                    raise HTTPException(status_code=404, detail=f"Thread not found: {request.thread_id}")
                raise
        else:
            # Create new thread
            logger.debug("Creating new thread")
            result = await client.thread_start(model=request.model)
            # Always use ID from Codex response
            thread_id = result.get("thread", {}).get("id")
            if not thread_id:
                raise HTTPException(status_code=500, detail="Failed to create thread")
            logger.info(f"Created thread: {thread_id}")

        if request.stream:
            # Return SSE stream
            return StreamingResponse(
                sse_stream(thread_id, prompt, request.model),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                }
            )
        else:
            # Collect all events and return final response
            events = []
            final_message = None

            async for event in client.turn_start_stream(thread_id, prompt, request.model):
                events.append(event)

                # Extract agent message
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
    except Exception as e:
        logger.exception(f"Chat failed: {e}")
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
