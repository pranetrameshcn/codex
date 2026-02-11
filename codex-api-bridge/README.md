# Codex API Bridge

HTTP API for Codex, bridging to `codex app-server` via JSON-RPC over stdio.

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure
cp .env.example .env
# Edit .env with your OPENAI_API_KEY

# 3. Run
python -m src.main
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/chat` | POST | Send message (new or continue conversation) |
| `/threads` | GET | List all conversations |
| `/history` | GET | Get conversation history |
| `/status` | GET | Health check |

## Usage Examples

### New conversation (streaming)

```bash
curl -N -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"content": "Hello!"}]}'
```

Expected Response (SSE stream, truncated):
```
data: {"type":"delta","response_id":"resp_123","content":{"thread_id":"a1b2..."}}

data: {"type":"delta","response_id":"resp_123","content":{"text":"Hello!"}}

data: {"type":"done","response_id":"resp_123"}
```

### Continue conversation

```bash
curl -N -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"thread_id": "YOUR_THREAD_ID", "messages": [{"content": "Tell me more"}]}'
```

### List conversations

```bash
curl http://localhost:8000/threads
```

Expected Response:
```json
{
  "threads": [
    {
      "thread_id": "a1b2...",
      "chat_name": "Hello! How can I help you today?",
      "created_at": "2025-01-15T10:30:00",
      "updated_at": "2025-01-15T10:35:00",
      "message_count": 0,
      "last_message_preview": "Hello! How can I help you today?",
      "agent_type": "codex",
      "project_id": null,
      "project_name": null
    }
  ],
  "total_count": 1
}
```

### Get history

```bash
curl "http://localhost:8000/history?thread_id=YOUR_THREAD_ID"
```

Expected Response:
```json
{
  "messages": [
    {"role": "user", "content": "Hello"},
    {"role": "assistant", "content": "Hello! How can I help you today?"}
  ],
  "user_id": "default",
  "thread_id": "a1b2...",
  "message_count": 2,
  "chat_name": "Hello! How can I help you today?",
  "agent_type": "codex",
  "project_id": null,
  "project_name": null,
  "usage_metadata": null
}
```

### Non-streaming response

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"content": "What is 2+2?"}], "stream": false}'
```

Expected Response:
```json
{
  "id": "resp_123",
  "type": "response",
  "created_at": "2025-02-11T12:00:00Z",
  "status": "ok",
  "messages": [
    {
      "id": "msg_1",
      "role": "assistant",
      "content": {"type": "text", "text": "2+2 equals 4."}
    }
  ],
  "actions": null,
  "metadata": {
    "user_id": "default",
    "thread_id": "a1b2...",
    "agent_type": "codex",
    "project_id": null,
    "project_name": null
  }
}
```

## Architecture

```
HTTP Client ──► FastAPI Bridge ──► SessionManager ──► codex app-server (JSON-RPC/stdio)
     ◄── SSE ──────┘                    │                   └── Notifications ──┘
                                         └── per-user AppServerClient instances
```

The bridge manages per-user `codex app-server` subprocesses via the `SessionManager`:
- Each authenticated user gets their own subprocess with isolated `CODEX_HOME`
- HTTP requests → routed by user_id → JSON-RPC requests (stdin)
- JSON-RPC responses/notifications (stdout) → HTTP responses/SSE
- Idle sessions are automatically cleaned up after the configured timeout

## Configuration

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OPENAI_API_KEY` | Yes | - | OpenAI API key |
| `CODEX_BINARY_PATH` | No | auto-detect | Path to codex binary |
| `CODEX_WORKING_DIR` | No | current dir | Working directory |
| `HOST` | No | 0.0.0.0 | Server bind address |
| `PORT` | No | 8000 | Server port |
| `DEBUG` | No | false | Enable auto-reload |
| `LOG_LEVEL` | No | INFO | Logging level |
| `BASE_DATA_DIR` | No | ./data/codex | Base directory for per-user data |
| `MAX_SESSIONS` | No | 50 | Maximum concurrent user sessions |
| `IDLE_TIMEOUT_SECONDS` | No | 300 | Seconds before idle sessions are cleaned up |
| `CLEANUP_INTERVAL_SECONDS` | No | 60 | Interval between cleanup sweeps |
| `ALLOW_USER_ID_OVERRIDE` | No | false | Allow user_id via query param / header (testing) |
| `USER_MONGODB_URL` | No | - | MongoDB URL for users collection (Keycloak only) |
| `USER_MONGODB_DATABASE` | No | users | MongoDB database name (Keycloak only) |
| `USER_MONGODB_COLLECTION` | No | users | MongoDB collection name (Keycloak only) |

## Security (Keycloak)

Set `SECURITY_METHOD=Keycloak` to enforce authentication via Keycloak token introspection.
Set `SECURITY_METHOD=None` (default) to disable authentication.

Required env vars when `SECURITY_METHOD=Keycloak`:
- `KEYCLOAK_BASE_URL`
- `KEYCLOAK_REALM`
- `KEYCLOAK_CLIENT_ID`
- `KEYCLOAK_CLIENT_SECRET`
- `USER_MONGODB_URL`
- `USER_MONGODB_DATABASE`
- `USER_MONGODB_COLLECTION`

Optional:
- `KEYCLOAK_INTROSPECTION_URL`
- `KEYCLOAK_TIMEOUT_SECONDS`

Example:
```bash
curl -H "Authorization: Bearer <access_token>" \
  -H "X-User-Id: <app_user_id>" \
  http://localhost:8000/threads
```

## Multi-User Mode

When `SECURITY_METHOD=Keycloak` is set, the bridge operates in multi-user mode:

- Each request must include the app `user_id` (query/header/body) which is verified against MongoDB
- Requests are authorized only when `keycloak_id` (JWT `sub`) matches the MongoDB user record `_id`
- Each verified user gets their own `codex app-server` subprocess
- User data is isolated under `{BASE_DATA_DIR}/users/{user_id}/`
- Sessions are created on first request and cleaned up after `IDLE_TIMEOUT_SECONDS` of inactivity
- The `MAX_SESSIONS` setting caps the total number of concurrent user sessions (HTTP 503 when full)

When `SECURITY_METHOD=None` (default), all requests use a single `default` user session, preserving backwards compatibility.

### Data Directory Structure

```
{BASE_DATA_DIR}/
└── users/
    ├── {user_id_1}/    # CODEX_HOME for user 1
    ├── {user_id_2}/    # CODEX_HOME for user 2
    └── ...
```

## Detailed Setup

### Prerequisites

| Component | Version | Purpose |
|-----------|---------|---------|
| **Build tools** | - | C compiler, linker |
| **Rust** | 1.85+ (nightly) | Build Codex binary |
| **Python** | 3.11+ | Run API bridge |
| **OpenAI API Key** | - | Authentication |

### 1. Install Build Tools

**Linux (Ubuntu/Debian):**
```bash
sudo apt update
sudo apt install -y build-essential pkg-config libssl-dev cmake golang clang libclang-dev libc6-dev
```

**Linux (RHEL/CentOS/Fedora):**
```bash
sudo yum groupinstall -y "Development Tools"
sudo yum install -y openssl-devel pkg-config cmake golang clang
```

**macOS:**
```bash
xcode-select --install
brew install cmake go
```

**Windows:**
Install [Visual Studio Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/) with "Desktop development with C++".

### 2. Install Rust

**Linux/macOS:**
```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
source "$HOME/.cargo/env"
rustup install nightly
rustup default nightly
```

**Windows:**
Download [rustup-init.exe](https://rustup.rs/), then:
```powershell
rustup install nightly
rustup default nightly
```

Verify:
```bash
rustc --version
cargo --version
```

### 3. Build Codex Binary

**Debug build (faster, for testing):**
```bash
cd codex-rs
cargo build -p codex-cli
```

**Release build (optimized, for production):**
```bash
cd codex-rs
cargo build --release -p codex-cli
```

**Note:** If build fails due to memory, use single-threaded:
```bash
CARGO_BUILD_JOBS=1 cargo build -p codex-cli
```

Binary location:
- **Debug:** `codex-rs/target/debug/codex`
- **Release:** `codex-rs/target/release/codex`
- **Windows:** Add `.exe` extension

Verify:
```bash
./target/debug/codex --version
# or
./target/release/codex --version
```

### 4. Install Python Dependencies

**Note:** You may need to install `venv` separately depending on your Python installation:
```bash
# Ubuntu/Debian
sudo apt install python3-venv
```

**With uv (recommended):**
```bash
cd codex-api-bridge
uv venv
uv pip install -e .
```

**With pip:**
```bash
cd codex-api-bridge
python -m venv .venv

# Activate (Linux/Mac)
source .venv/bin/activate

# Activate (Windows PowerShell)
.\.venv\Scripts\Activate.ps1

# Install
pip install -e .
```

### 5. Configure .env

```bash
cp .env.example .env
```

Example `.env`:
```env
# REQUIRED
OPENAI_API_KEY=sk-your-key-here

# OPTIONAL - set if codex is not in PATH
CODEX_BINARY_PATH=C:\path\to\codex-rs\target\release\codex.exe

# OPTIONAL - working directory for agent
CODEX_WORKING_DIR=C:\your\project

# Server
HOST=0.0.0.0
PORT=8000
DEBUG=true
LOG_LEVEL=INFO
```

### 6. Run

**Development (auto-reload):**
```bash
# Set DEBUG=true in .env
python -m src.main

# Or with uv
uv run python -m src.main
```

**Production:**
```bash
# Set DEBUG=false in .env
python -m src.main

# Or with gunicorn (Linux)
gunicorn src.main:app -w 4 -k uvicorn.workers.UvicornWorker -b 0.0.0.0:8000
```

### 7. Verify

```bash
curl http://localhost:8000/status
```

Should return:
```json
{"status":"ok","codex_available":true,"codex_version":"...","api_key_configured":true}
```
