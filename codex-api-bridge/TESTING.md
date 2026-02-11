# Codex API Bridge - Testing Guide

This document provides testing instructions for all API endpoints.

## Prerequisites

### System Requirements

| Component | Version | Purpose |
|-----------|---------|---------|
| **Rust** | 1.85+ (nightly) | Build Codex binary |
| **Cargo** | (comes with Rust) | Rust package manager |
| **Python** | 3.11+ | Run API bridge |
| **OpenAI API Key** | - | Authentication for Codex |

### 1. Install Build Tools

Rust requires a C compiler/linker for native dependencies.

**Linux (Ubuntu/Debian):**
```bash
sudo apt update
sudo apt install -y build-essential pkg-config libssl-dev cmake golang clang libclang-dev libc6-dev
```

**Linux (RHEL/CentOS/Fedora):**
```bash
sudo yum groupinstall -y "Development Tools"
sudo yum install -y openssl-devel pkg-config
```

**macOS:**
```bash
xcode-select --install
```

**Windows:**
Install [Visual Studio Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/) and select "Desktop development with C++".

### 2. Install Rust (if not installed)

**Linux/macOS:**
```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
source "$HOME/.cargo/env"
rustup install nightly
rustup default nightly
```

**Windows:**
Download and run [rustup-init.exe](https://rustup.rs/), then:
```powershell
rustup install nightly
rustup default nightly
```

Verify:
```bash
rustc --version   # Should show 1.85+ or nightly
cargo --version
```

### 3. Build Codex Binary

**Debug build (faster, recommended for testing):**
```bash
cd codex-rs
cargo build -p codex-cli
```

**Release build (slower, optimized):**
```bash
cd codex-rs
cargo build --release -p codex-cli
```

**Note:** Full workspace builds can be slow/memory-intensive. Using `-p codex-cli` builds only the CLI.

Binary location:
- **Debug:** `codex-rs/target/debug/codex` (Linux/macOS) or `codex.exe` (Windows)
- **Release:** `codex-rs/target/release/codex` (Linux/macOS) or `codex.exe` (Windows)

Verify:
```bash
./target/debug/codex --version
# or
./target/release/codex --version
```

### 4. Install Python Dependencies

```bash
cd codex-api-bridge

# With uv (recommended)
uv venv
uv pip install -e .

# Or with pip
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
# .\.venv\Scripts\Activate.ps1  # Windows
pip install -e .
```

### 5. Configure Environment

```bash
cp .env.example .env
```

Edit `.env`:
```env
# Required
OPENAI_API_KEY=sk-your-key-here

# Optional (if codex not in PATH)
CODEX_BINARY_PATH=/path/to/codex-rs/target/release/codex

# Development
DEBUG=true
LOG_LEVEL=INFO
```

### 6. Start the Server

```bash
cd codex-api-bridge
python -m src.main
```

Expected output:
```
INFO - Starting Codex API Bridge
INFO - OpenAI API key: configured
INFO - Codex binary: codex 0.1.0 (abc1234)
INFO - Uvicorn running on http://0.0.0.0:8000
```

---

## Security Mode Test Matrix

This section covers testing all supported security modes and user_id override configurations.

### Mode A: SECURITY_METHOD=None (default, single-user)

**Env:**
```env
SECURITY_METHOD=None
ALLOW_USER_ID_OVERRIDE=false
```

**Expected behavior:**
- No Authorization header required
- `user_id` is ignored
- All requests use the single `default` session

**Smoke test:**
```bash
curl http://localhost:8000/threads
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"content": "Hello"}], "stream": false}'
```

### Mode B: SECURITY_METHOD=None + user_id override (multi-user without Keycloak)

**Env:**
```env
SECURITY_METHOD=None
ALLOW_USER_ID_OVERRIDE=true
```

**Expected behavior:**
- No Authorization header required
- `user_id` required via query/header/body
- Requests are routed by `user_id`

**Smoke test (query param):**
```bash
curl "http://localhost:8000/threads?user_id=test-user-1"
```

**Smoke test (header):**
```bash
curl http://localhost:8000/threads \
  -H "X-User-Id: test-user-1"
```

**Smoke test (body for /chat):**
```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"user_id": "test-user-1", "messages": [{"content": "Hello"}], "stream": false}'
```

**Expected errors:**
- Missing `user_id` in multi-user (override) mode: `400`

### Mode C: SECURITY_METHOD=Keycloak (Keycloak + MongoDB user validation)

**Env:**
```env
SECURITY_METHOD=Keycloak
KEYCLOAK_BASE_URL=...
KEYCLOAK_REALM=...
KEYCLOAK_CLIENT_ID=...
KEYCLOAK_CLIENT_SECRET=...
USER_MONGODB_URL=...
USER_MONGODB_DATABASE=users
USER_MONGODB_COLLECTION=users
```

**Expected behavior:**
- Authorization header is required
- `user_id` is required via query/header/body
- Request is allowed only when:
  - JWT `sub` == user document `keycloak_id`
  - provided `user_id` == user document `_id` (ObjectId)

**Smoke test (header user_id):**
```bash
curl http://localhost:8000/threads \
  -H "Authorization: Bearer <access_token>" \
  -H "X-User-Id: <app_user_id>"
```

**Smoke test (/chat user_id in body):**
```bash
curl -X POST http://localhost:8000/chat \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "<app_user_id>", "messages": [{"content": "Hello"}], "stream": false}'
```

**Expected errors:**
- Missing Authorization header: `401`
- Missing `user_id`: `400`
- No MongoDB user match: `403`
- MongoDB unavailable or not configured: `503`

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

**Mode A (SECURITY_METHOD=None):**
```bash
curl http://localhost:8000/threads
```

**Mode B (None + user_id override):**
```bash
curl "http://localhost:8000/threads?user_id=test-user-1"
```

**Mode C (Keycloak + MongoDB):**
```bash
curl http://localhost:8000/threads \
  -H "Authorization: Bearer <access_token>" \
  -H "X-User-Id: <app_user_id>"
```

**With pagination (Mode B/C):**
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

**Mode A (SECURITY_METHOD=None):**
```bash
curl "http://localhost:8000/history?thread_id=YOUR_THREAD_ID"
```

**Mode B (None + user_id override):**
```bash
curl "http://localhost:8000/history?thread_id=YOUR_THREAD_ID&user_id=test-user-1"
```

**Mode C (Keycloak + MongoDB):**
```bash
curl "http://localhost:8000/history?thread_id=YOUR_THREAD_ID" \
  -H "Authorization: Bearer <access_token>" \
  -H "X-User-Id: <app_user_id>"
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

**Mode A (SECURITY_METHOD=None):**
```bash
curl -N -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"content": "Hello, who are you?"}]
  }'
```

**Mode B (None + user_id override):**
```bash
curl -N -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "test-user-1",
    "messages": [{"content": "Hello, who are you?"}]
  }'
```

**Mode C (Keycloak + MongoDB):**
```bash
curl -N -X POST http://localhost:8000/chat \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "<app_user_id>",
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

**Mode A (SECURITY_METHOD=None):**
```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"content": "What is 2+2?"}],
    "stream": false
  }'
```

**Mode B (None + user_id override):**
```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "test-user-1",
    "messages": [{"content": "What is 2+2?"}],
    "stream": false
  }'
```

**Mode C (Keycloak + MongoDB):**
```bash
curl -X POST http://localhost:8000/chat \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "<app_user_id>",
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

**Mode A (SECURITY_METHOD=None):**
```bash
curl -N -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "thread_id": "YOUR_THREAD_ID",
    "messages": [{"content": "Now multiply that by 3"}]
  }'
```

**Mode B (None + user_id override):**
```bash
curl -N -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "test-user-1",
    "thread_id": "YOUR_THREAD_ID",
    "messages": [{"content": "Now multiply that by 3"}]
  }'
```

**Mode C (Keycloak + MongoDB):**
```bash
curl -N -X POST http://localhost:8000/chat \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "<app_user_id>",
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

**Mode A (SECURITY_METHOD=None):**
```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "thread_id": "YOUR_THREAD_ID",
    "messages": [{"content": "Now multiply that by 3"}],
    "stream": false
  }'
```

**Mode B (None + user_id override):**
```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "test-user-1",
    "thread_id": "YOUR_THREAD_ID",
    "messages": [{"content": "Now multiply that by 3"}],
    "stream": false
  }'
```

**Mode C (Keycloak + MongoDB):**
```bash
curl -X POST http://localhost:8000/chat \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "<app_user_id>",
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

**Mode A (SECURITY_METHOD=None):**
```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"content": "Hello"}],
    "model": "gpt-4o",
    "stream": false
  }'
```

**Mode B (None + user_id override):**
```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "test-user-1",
    "messages": [{"content": "Hello"}],
    "model": "gpt-4o",
    "stream": false
  }'
```

**Mode C (Keycloak + MongoDB):**
```bash
curl -X POST http://localhost:8000/chat \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "<app_user_id>",
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
