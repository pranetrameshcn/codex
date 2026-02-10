"""All models for Codex API Bridge."""
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


# =============================================================================
# HTTP Request Models
# =============================================================================

class ChatMessage(BaseModel):
    """A chat message."""
    role: str = "user"
    content: str


class RenameRequest(BaseModel):
    """Request for PATCH /threads/{thread_id}."""
    name: str


class ChatRequest(BaseModel):
    """Request for POST /chat."""
    thread_id: Optional[str] = None  # None = new conversation
    messages: List[ChatMessage]
    model: Optional[str] = None
    stream: bool = True

    def get_prompt(self) -> str:
        """Combine messages into prompt."""
        return "\n".join(m.content for m in self.messages if m.content)


# =============================================================================
# HTTP Response Models
# =============================================================================

class ThreadInfo(BaseModel):
    """Thread summary for list endpoint."""
    thread_id: str
    preview: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ThreadsResponse(BaseModel):
    """Response for GET /threads."""
    threads: List[ThreadInfo]
    next_cursor: Optional[str] = None


class ThreadHistoryResponse(BaseModel):
    """Response for GET /history."""
    thread_id: str
    preview: Optional[str] = None
    turns: List[Dict[str, Any]] = []
    created_at: Optional[datetime] = None


class StatusResponse(BaseModel):
    """Response for GET /status."""
    status: str
    codex_available: bool
    codex_version: Optional[str] = None
    api_key_configured: bool
