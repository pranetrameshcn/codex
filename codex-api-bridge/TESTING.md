# Codex API Bridge - Testing Guide

This document provides testing instructions for all API endpoints.

## Prerequisites

1. Server running at `http://localhost:8000`
2. Valid `OPENAI_API_KEY` in `.env`
3. Codex binary built and accessible

Start the server:
```bash
python -m src.main
```

---

## Endpoints

### 1. GET / - API Info

Returns basic API information.

**Request:**
```bash
curl http://localhost:8000/
```

**Expected Response:**
```json
{
  "name": "Codex API Bridge",
  "version": "0.1.0",
  "endpoints": {
    "POST /chat": "Send message (new or continue)",
    "GET /threads": "List conversations",
    "GET /history": "Get conversation history",
    "GET /status": "Health check"
  }
}
```

---

### 2. GET /status - Health Check

Returns server status and Codex availability.

**Request:**
```bash
curl http://localhost:8000/status
```

**Expected Response (healthy):**
```json
{
  "status": "ok",
  "codex_available": true,
  "codex_version": "codex 0.1.0 (abcd123)",
  "api_key_configured": true
}
```

**Possible `status` values:**
| Status | Meaning |
|--------|---------|
| `ok` | Codex available AND API key configured |
| `degraded` | Only one of the above is true |
| `unavailable` | Neither available |

---

### 3. GET /threads - List Conversations

Returns a list of conversation threads.

**Request:**
```bash
curl http://localhost:8000/threads
```

**With pagination:**
```bash
curl "http://localhost:8000/threads?limit=10"
curl "http://localhost:8000/threads?limit=10&cursor=CURSOR_FROM_PREVIOUS"
```

**Expected Response:**
```json
{
  "threads": [
    {
      "thread_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
      "preview": "Hello! How can I help you today?",
      "created_at": "2025-01-15T10:30:00",
      "updated_at": "2025-01-15T10:35:00"
    },
    {
      "thread_id": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
      "preview": "Write a Python function...",
      "created_at": "2025-01-14T09:00:00",
      "updated_at": "2025-01-14T09:15:00"
    }
  ],
  "next_cursor": null
}
```

**Notes:**
- Returns empty `threads` array if no conversations exist
- `next_cursor` is `null` when no more pages

---

### 4. GET /history - Get Conversation History

Returns full history for a specific thread.

**Request:**
```bash
curl "http://localhost:8000/history?thread_id=YOUR_THREAD_ID"
```

**Expected Response:**
```json
{
  "thread_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "preview": "Hello! How can I help you today?",
  "turns": [
    {
      "id": "turn_001",
      "status": "completed",
      "items": [
        {
          "type": "userMessage",
          "id": "item_001",
          "content": [{"type": "text", "text": "Hello"}]
        },
        {
          "type": "agentMessage",
          "id": "item_002",
          "text": "Hello! How can I help you today?"
        }
      ]
    }
  ],
  "created_at": "2025-01-15T10:30:00"
}
```

**Error Response (thread not found):**
```json
{
  "detail": "Thread not found: invalid-thread-id"
}
```
HTTP Status: `404`

---

### 5. POST /chat - Send Message

Send a message to start a new conversation or continue an existing one.

#### 5a. New Conversation (Streaming)

**Request:**
```bash
curl -N -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"content": "Hello, who are you?"}]
  }'
```

**JSON Body:**
```json
{
  "messages": [
    {"content": "Hello, who are you?"}
  ]
}
```

**Expected Response (SSE stream):**
```
data: {"type": "session", "thread_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"}

data: {"type": "turn.started", "turn": {"id": "turn_001", "status": "inProgress", "items": []}}

data: {"method": "item/started", "params": {"item": {"type": "agentMessage", "id": "item_001", "text": ""}}}

data: {"method": "item/agentMessage/delta", "params": {"itemId": "item_001", "delta": "Hello"}}

data: {"method": "item/agentMessage/delta", "params": {"itemId": "item_001", "delta": "! I'm"}}

data: {"method": "item/agentMessage/delta", "params": {"itemId": "item_001", "delta": " Codex"}}

data: {"method": "item/completed", "params": {"item": {"type": "agentMessage", "id": "item_001", "text": "Hello! I'm Codex, an AI assistant."}}}

data: {"method": "turn/completed", "params": {"turn": {"id": "turn_001", "status": "completed"}}}

data: [DONE]
```

**Important:** Save the `thread_id` from the first `session` event to continue the conversation.

---

#### 5b. New Conversation (Non-Streaming)

**Request:**
```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"content": "What is 2+2?"}],
    "stream": false
  }'
```

**JSON Body:**
```json
{
  "messages": [
    {"content": "What is 2+2?"}
  ],
  "stream": false
}
```

**Expected Response:**
```json
{
  "thread_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "message": "2+2 equals 4.",
  "events": [
    {"type": "turn.started", "turn": {"id": "turn_001", "status": "inProgress"}},
    {"method": "item/started", "params": {"item": {"type": "agentMessage", "id": "item_001"}}},
    {"method": "item/completed", "params": {"item": {"type": "agentMessage", "id": "item_001", "text": "2+2 equals 4."}}},
    {"method": "turn/completed", "params": {"turn": {"id": "turn_001", "status": "completed"}}}
  ]
}
```

---

#### 5c. Continue Conversation (Streaming)

**Request:**
```bash
curl -N -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "thread_id": "YOUR_THREAD_ID",
    "messages": [{"content": "Now multiply that by 3"}]
  }'
```

**JSON Body:**
```json
{
  "thread_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "messages": [
    {"content": "Now multiply that by 3"}
  ]
}
```

**Expected Response:** Same SSE format as 5a, with same `thread_id` in session event.

---

#### 5d. Continue Conversation (Non-Streaming)

**Request:**
```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "thread_id": "YOUR_THREAD_ID",
    "messages": [{"content": "Now multiply that by 3"}],
    "stream": false
  }'
```

**JSON Body:**
```json
{
  "thread_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "messages": [
    {"content": "Now multiply that by 3"}
  ],
  "stream": false
}
```

**Expected Response:** Same JSON format as 5b.

---

#### 5e. With Model Override

**Request:**
```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"content": "Hello"}],
    "model": "gpt-4o",
    "stream": false
  }'
```

**JSON Body:**
```json
{
  "messages": [
    {"content": "Hello"}
  ],
  "model": "gpt-4o",
  "stream": false
}
```

---

## Error Responses

### Empty Message

**Request:**
```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"content": ""}]}'
```

**Response:**
```json
{
  "detail": "Empty message"
}
```
HTTP Status: `400`

---

### Thread Not Found

**Request:**
```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"thread_id": "invalid-id", "messages": [{"content": "Hello"}]}'
```

**Response:**
```json
{
  "detail": "Thread not found: invalid-id"
}
```
HTTP Status: `404`

---

### Server Error

**Response:**
```json
{
  "detail": "Error message describing what went wrong"
}
```
HTTP Status: `500`

---

## Complete Test Flow

Run these commands in sequence to test the full flow:

```bash
# 1. Check server status
curl http://localhost:8000/status

# 2. List existing threads
curl http://localhost:8000/threads

# 3. Start new conversation (non-streaming to capture thread_id easily)
RESPONSE=$(curl -s -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"content": "What is 2+2?"}], "stream": false}')
echo "$RESPONSE"

# 4. Extract thread_id (requires jq or python)
THREAD_ID=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['thread_id'])")
echo "Thread ID: $THREAD_ID"

# 5. Continue conversation
curl -s -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d "{\"thread_id\": \"$THREAD_ID\", \"messages\": [{\"content\": \"Multiply that by 3\"}], \"stream\": false}"

# 6. Get history
curl "http://localhost:8000/history?thread_id=$THREAD_ID"

# 7. Verify thread appears in list
curl http://localhost:8000/threads
```

---

## SSE Event Types Reference

| Event Type | Description |
|------------|-------------|
| `session` | First event, contains `thread_id` |
| `turn.started` | Turn began processing |
| `item/started` | New item (message, command, etc.) started |
| `item/agentMessage/delta` | Incremental text from agent |
| `item/completed` | Item finished |
| `turn/completed` | Turn finished |
| `error` | Error occurred |
| `[DONE]` | Stream ended |
