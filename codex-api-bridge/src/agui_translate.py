"""Codex -> AG-UI translation helpers."""
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .agui_models import ContentObject

logger = logging.getLogger(__name__)


def _ts() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _uuid() -> str:
    return str(uuid.uuid4())


# -----------------------------------------------------------------------------
# AG-UI delta helpers (no extra fields)
# -----------------------------------------------------------------------------

def build_initial_delta(thread_id: str, response_id: str) -> Dict[str, Any]:
    return {
        "type": "delta",
        "response_id": response_id,
        "content": {"thread_id": thread_id},
    }


def build_text_delta(response_id: str, text: str) -> Dict[str, Any]:
    return {
        "type": "delta",
        "response_id": response_id,
        "content": {"text": text},
    }


def build_done_delta(response_id: str) -> Dict[str, Any]:
    return {
        "type": "done",
        "response_id": response_id,
    }


def build_error_delta(response_id: str, message: str) -> Dict[str, Any]:
    return {
        "type": "delta",
        "response_id": response_id,
        "content": {"error": message},
    }


def build_usage_metadata_delta(response_id: str, usage_metadata: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "delta",
        "response_id": response_id,
        "content": {"usage_metadata": usage_metadata},
    }


# -----------------------------------------------------------------------------
# Tool classification and normalization
# -----------------------------------------------------------------------------

TOOL_ITEM_TYPES = {
    "mcpToolCall",
    "commandExecution",
    "fileChange",
    "collabToolCall",
    "collabAgentToolCall",
    "webSearch",
}

TOOL_NAME_MAP = {
    "commandExecution": "command_execution",
    "fileChange": "file_change",
    "collabToolCall": "collab_tool",
    "collabAgentToolCall": "collab_tool",
    "webSearch": "web_search",
}

DROPPED_ITEM_TYPES = {
    "contextCompaction",
    "plan",
    "imageView",
    "enteredReviewMode",
    "exitedReviewMode",
}


def is_tool_item(item: Dict[str, Any]) -> bool:
    return item.get("type") in TOOL_ITEM_TYPES


def normalize_tool_name(item: Dict[str, Any]) -> str:
    item_type = item.get("type", "")
    if item_type == "mcpToolCall":
        return item.get("tool", "unknown_mcp_tool")
    return TOOL_NAME_MAP.get(item_type, item_type)


def extract_tool_call_id(item: Dict[str, Any]) -> str:
    return item.get("id", _uuid())


# -----------------------------------------------------------------------------
# Tool event helpers (dual format)
# -----------------------------------------------------------------------------

def build_tool_call_start(item: Dict[str, Any], response_id: str) -> Dict[str, Any]:
    return {
        "type": "TOOL_CALL_START",
        "eventId": _uuid(),
        "event_id": _uuid(),
        "tool_call_id": extract_tool_call_id(item),
        "tool_call_name": normalize_tool_name(item),
        "parent_message_id": response_id,
        "timestamp": _ts(),
    }


def build_tool_call_args(item: Dict[str, Any]) -> Dict[str, Any]:
    args = _extract_tool_args(item)
    parsed_args: Dict[str, Any]
    if isinstance(args, str):
        try:
            parsed_args = json.loads(args) if args else {}
        except json.JSONDecodeError:
            parsed_args = {"raw": args}
    elif isinstance(args, dict):
        parsed_args = args
    else:
        logger.warning("Unexpected tool args type in build_tool_call_args: %s", type(args))
        parsed_args = {}
    return {
        "type": "TOOL_CALL_ARGS",
        "eventId": _uuid(),
        "event_id": _uuid(),
        "tool_call_id": extract_tool_call_id(item),
        "delta": args,
        "arguments": parsed_args,
        "timestamp": _ts(),
    }


def build_tool_call_end(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "TOOL_CALL_END",
        "eventId": _uuid(),
        "event_id": _uuid(),
        "tool_call_id": extract_tool_call_id(item),
        "status": item.get("status", "completed"),
        "result": _extract_tool_result(item),
        "timestamp": _ts(),
    }


def build_tool_call_delta(item: Dict[str, Any], response_id: str) -> Dict[str, Any]:
    """AGUIDelta with content.tool_call (dual format)."""
    return {
        "type": "delta",
        "response_id": response_id,
        "content": {
            "tool_call": {
                "id": extract_tool_call_id(item),
                "name": normalize_tool_name(item),
                "args": _extract_tool_args(item),
            }
        },
    }


def build_tool_result_delta(item: Dict[str, Any], response_id: str) -> Dict[str, Any]:
    """AGUIDelta with content.tool_result (dual format)."""
    return {
        "type": "delta",
        "response_id": response_id,
        "content": {
            "tool_result": {
                "id": extract_tool_call_id(item),
                "name": normalize_tool_name(item),
                "content": _extract_tool_result(item),
            }
        },
    }


def build_tool_calls_batch(item: Dict[str, Any], response_id: str) -> Dict[str, Any]:
    """Batch wrapper used by cogentai UI (tool_calls)."""
    return {
        "type": "tool_calls",
        "data": {
            "thread_id": None,
            "run_id": response_id,
            "timestamp": _ts(),
            "tool_calls": [{
                "id": extract_tool_call_id(item),
                "name": normalize_tool_name(item),
                "args": _extract_tool_args(item),
            }],
        },
    }


def build_tool_results_batch(item: Dict[str, Any], response_id: str) -> Dict[str, Any]:
    """Batch wrapper used by cogentai UI (tool_results)."""
    return {
        "type": "tool_results",
        "data": {
            "thread_id": None,
            "run_id": response_id,
            "timestamp": _ts(),
            "tool_results": [{
                "id": extract_tool_call_id(item),
                "name": normalize_tool_name(item),
                "content": _extract_tool_result(item),
            }],
        },
    }


def build_tool_output_delta(params: Dict[str, Any]) -> Dict[str, Any]:
    """For commandExecution/outputDelta and fileChange/outputDelta."""
    delta = params.get("delta", "")
    if not delta:
        logger.debug("outputDelta missing delta field: %s", params)
    return {
        "type": "TOOL_CALL_ARGS",
        "eventId": _uuid(),
        "event_id": _uuid(),
        "tool_call_id": params.get("itemId", ""),
        "delta": delta,
        "arguments": {},
        "timestamp": _ts(),
    }


def build_error_event(response_id: str, message: str, details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Non-delta error event for UI compatibility (cogentai-style)."""
    return {
        "type": "error",
        "thread_id": None,
        "run_id": response_id,
        "error": message,
        "details": details or {},
        "timestamp": _ts(),
    }


def _extract_tool_args(item: Dict[str, Any]) -> str:
    item_type = item.get("type", "")
    if item_type == "commandExecution":
        return json.dumps({"command": item.get("command", ""), "cwd": item.get("cwd", "")})
    if item_type == "fileChange":
        return json.dumps({"changes": item.get("changes", [])})
    if item_type == "mcpToolCall":
        return json.dumps(item.get("arguments", {}))
    if item_type == "webSearch":
        return json.dumps({"query": item.get("query", "")})
    if item_type in ("collabToolCall", "collabAgentToolCall"):
        return json.dumps({"tool": item.get("tool", ""), "prompt": item.get("prompt", "")})
    return "{}"


def _extract_tool_result(item: Dict[str, Any]) -> str:
    item_type = item.get("type", "")
    if item_type == "commandExecution":
        output = item.get("aggregatedOutput", "")
        exit_code = item.get("exitCode")
        return f"{output}\n[exit code: {exit_code}]" if exit_code is not None else output
    if item_type == "fileChange":
        return json.dumps({"changes": item.get("changes", []), "status": item.get("status", "")})
    if item_type == "mcpToolCall":
        return item.get("result") or item.get("error") or ""
    if item_type == "webSearch":
        return json.dumps(item.get("action", ""))
    if item_type in ("collabToolCall", "collabAgentToolCall"):
        return json.dumps({"status": item.get("status", ""), "agents_states": item.get("agentsStates", [])})
    return ""


# -----------------------------------------------------------------------------
# Reasoning helpers (stream-only)
# -----------------------------------------------------------------------------

def build_reasoning_start(reasoning_id: str) -> Dict[str, Any]:
    return {
        "type": "REASONING_START",
        "reasoning_id": reasoning_id,
        "reasoning_type": "analysis",
        "context": "Processing request with extended reasoning",
        "timestamp": _ts(),
    }


def build_reasoning_content(reasoning_id: str, text: str) -> Dict[str, Any]:
    return {
        "type": "REASONING_CONTENT",
        "reasoning_id": reasoning_id,
        "content": text,
        "step": "reasoning",
        "confidence": 0.8,
        "timestamp": _ts(),
    }


def build_reasoning_end(reasoning_id: str, conclusion: str, success: bool) -> Dict[str, Any]:
    return {
        "type": "REASONING_END",
        "reasoning_id": reasoning_id,
        "conclusion": conclusion,
        "confidence": 0.9 if success else 0.0,
        "success": success,
        "timestamp": _ts(),
    }


# -----------------------------------------------------------------------------
# Message builders
# -----------------------------------------------------------------------------

def build_content_object(text: str) -> ContentObject:
    return ContentObject(type="text", text=text)


def build_agui_message(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Convert a Codex ThreadItem to an AGUIMessage dict. Returns None for dropped types."""
    item_type = item.get("type", "")
    item_id = item.get("id", _uuid())

    if item_type == "userMessage":
        parts = item.get("content", [])
        text = "\n".join(p.get("text", "") for p in parts if p.get("type") == "text")
        return {"id": item_id, "role": "user", "content": build_content_object(text).dict()}

    if item_type == "agentMessage":
        return {"id": item_id, "role": "assistant", "content": build_content_object(item.get("text", "")).dict()}

    if item_type == "reasoning":
        return None

    if item_type in TOOL_ITEM_TYPES:
        tool_call_id = extract_tool_call_id(item)
        tool_name = normalize_tool_name(item)
        args = _extract_tool_args(item)
        result = _extract_tool_result(item)
        return [
            {
                "id": _uuid(),
                "role": "assistant",
                "content": build_content_object("").dict(),
                "tool_calls": [{
                    "id": tool_call_id,
                    "type": "function",
                    "function": {"name": tool_name, "arguments": args},
                }],
            },
            {
                "id": _uuid(),
                "role": "tool",
                "content": build_content_object(result).dict(),
                "tool_call_id": tool_call_id,
                "name": tool_name,
            },
        ]

    if item_type in DROPPED_ITEM_TYPES:
        return None

    logger.warning("Unknown item type in AGUI message builder: %s", item_type)
    return None


def build_openai_history_message(item: Dict[str, Any]) -> Optional[List[Dict[str, Any]] | Dict[str, Any]]:
    """Convert a Codex ThreadItem to OpenAI-style message dict(s)."""
    item_type = item.get("type", "")

    if item_type == "userMessage":
        parts = item.get("content", [])
        text = "\n".join(p.get("text", "") for p in parts if p.get("type") == "text")
        return {"role": "user", "content": text}

    if item_type == "agentMessage":
        return {"role": "assistant", "content": item.get("text", "")}

    if item_type in TOOL_ITEM_TYPES:
        tool_call_id = extract_tool_call_id(item)
        tool_name = normalize_tool_name(item)
        args = _extract_tool_args(item)
        result = _extract_tool_result(item)
        return [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": tool_call_id,
                    "type": "function",
                    "function": {"name": tool_name, "arguments": args},
                }],
            },
            {
                "role": "tool",
                "content": result,
                "tool_call_id": tool_call_id,
                "name": tool_name,
            },
        ]

    if item_type in DROPPED_ITEM_TYPES:
        return None

    logger.warning("Unknown item type in history builder: %s", item_type)
    return None


# -----------------------------------------------------------------------------
# Event translation
# -----------------------------------------------------------------------------

def translate_event(
    event: Dict[str, Any],
    response_id: str,
    reasoning_id: str,
    state: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Translate a single Codex event into zero or more AG-UI events."""
    # Synthetic events (no "method")
    if "method" not in event:
        event_type = event.get("type", "")
        if event_type == "turn.started":
            return []
        if event_type == "error":
            msg = event.get("message") or str(event.get("error", "Unknown error"))
            return [
                build_error_delta(response_id, msg),
                build_error_event(response_id, msg),
                build_done_delta(response_id),
            ]
        logger.debug("Unknown synthetic event type: %s", event_type)
        return []

    # Server requests (approvals) - must not be silently dropped
    if "id" in event and "result" not in event:
        logger.warning(
            "Unexpected server request (approval?): %s",
            event.get("method"),
        )
        return []

    method = event.get("method", "")
    params = event.get("params", {})

    # Text streaming
    if method == "item/agentMessage/delta":
        return [build_text_delta(response_id, params.get("delta", ""))]

    # Tool item lifecycle
    if method == "item/started":
        item = params.get("item", {})
        if is_tool_item(item):
            return [
                build_tool_call_delta(item, response_id),
                build_tool_call_start(item, response_id),
                build_tool_call_args(item),
                build_tool_calls_batch(item, response_id),
            ]
        if item.get("type") == "reasoning" and not state.get("reasoning_started"):
            state["reasoning_started"] = True
            return [build_reasoning_start(reasoning_id)]
        return []

    if method == "item/completed":
        item = params.get("item", {})
        if is_tool_item(item):
            return [
                build_tool_result_delta(item, response_id),
                build_tool_results_batch(item, response_id),
                build_tool_call_end(item),
            ]
        if item.get("type") == "reasoning" and state.get("reasoning_started"):
            state["reasoning_started"] = False
            return [build_reasoning_end(reasoning_id, "Reasoning completed", True)]
        return []

    # Tool output deltas
    if method in ("item/commandExecution/outputDelta", "item/fileChange/outputDelta"):
        return [build_tool_output_delta(params)]

    # Reasoning deltas
    if method == "item/reasoning/summaryTextDelta":
        results: List[Dict[str, Any]] = []
        if not state.get("reasoning_started"):
            state["reasoning_started"] = True
            results.append(build_reasoning_start(reasoning_id))
        results.append(build_reasoning_content(reasoning_id, params.get("delta", "")))
        return results

    # Error notifications
    if method == "error":
        if params.get("willRetry", params.get("will_retry", False)):
            logger.debug("Transient error (will retry): %s", params.get("error", {}).get("message"))
            return []
        msg = params.get("error", {}).get("message", "Unknown error")
        return [build_error_delta(response_id, msg), build_error_event(response_id, msg, params.get("error", {}))]

    # Token usage updates
    if method == "thread/tokenUsage/updated":
        return [build_usage_metadata_delta(response_id, params)]

    # Turn completed
    if method == "turn/completed":
        turn = params.get("turn", {})
        status = turn.get("status", "completed")
        results = []
        if status == "failed":
            error = turn.get("error", {})
            results.append(build_error_delta(response_id, error.get("message", "Turn failed")))
        results.append(build_done_delta(response_id))
        return results

    # Explicitly ignored
    if method in (
        "turn/diff/updated",
        "turn/plan/updated",
        "thread/name/updated",
        "turn/started",
        "item/reasoning/textDelta",
        "item/reasoning/summaryPartAdded",
        "item/plan/delta",
        "item/mcpToolCall/progress",
    ):
        return []

    logger.warning("Unmapped event method: %s", method)
    return []


# -----------------------------------------------------------------------------
# Response builders
# -----------------------------------------------------------------------------

def build_agui_response(events: List[Dict[str, Any]], thread_id: str, user_id: str) -> Dict[str, Any]:
    """Build AGUIResponse dict from collected Codex events."""
    messages: List[Dict[str, Any]] = []
    status = "ok"

    for event in events:
        if event.get("method") == "item/completed":
            item = event.get("params", {}).get("item", {})
            result = build_agui_message(item)
            if result is None:
                continue
            if isinstance(result, list):
                messages.extend(result)
            else:
                messages.append(result)
        if event.get("method") == "turn/completed":
            turn = event.get("params", {}).get("turn", {})
            if turn.get("status") == "failed":
                status = "error"

    return {
        "id": _uuid(),
        "type": "response",
        "created_at": _ts(),
        "status": status,
        "messages": messages,
        "actions": None,
        "metadata": {
            "user_id": user_id,
            "thread_id": thread_id,
            "agent_type": "codex",
            "project_id": None,
            "project_name": None,
        },
    }


def build_history_response(thread: Dict[str, Any], thread_id: str, user_id: str) -> Dict[str, Any]:
    """Build HistoryResponse dict from Codex thread/read result."""
    messages: List[Dict[str, Any]] = []
    for turn in thread.get("turns", []):
        for item in turn.get("items", []):
            result = build_openai_history_message(item)
            if result is None:
                continue
            if isinstance(result, list):
                messages.extend(result)
            else:
                messages.append(result)

    return {
        "messages": messages,
        "user_id": user_id,
        "thread_id": thread_id,
        "message_count": len(messages),
        "chat_name": thread.get("preview", "New Chat"),
        "agent_type": "codex",
        "project_id": None,
        "project_name": None,
        "usage_metadata": None,
    }
