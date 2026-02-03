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

### Get history

```bash
curl "http://localhost:8000/history?thread_id=YOUR_THREAD_ID"
```

### Non-streaming response

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"content": "What is 2+2?"}], "stream": false}'
```

## Architecture

```
HTTP Client ──► FastAPI Bridge ──► codex app-server (JSON-RPC/stdio)
     ◄── SSE ──────┘                      └── Notifications ──┘
```

The bridge spawns `codex app-server` as a subprocess and translates:
- HTTP requests → JSON-RPC requests (stdin)
- JSON-RPC responses/notifications (stdout) → HTTP responses/SSE

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

## Detailed Setup

### Prerequisites

- Python 3.11+
- Codex binary built from `codex-rs/`

### 1. Build Codex (if not already)

```bash
cd ../codex-rs
cargo build --release
```

Binary location:
- Linux/Mac: `codex-rs/target/release/codex`
- Windows: `codex-rs/target/release/codex.exe`

### 2. Install dependencies

**With uv (fast):**
```bash
uv venv
uv pip install -e .
```

**With pip:**
```bash
# Create venv
python -m venv .venv

# Activate (Windows PowerShell)
.\.venv\Scripts\Activate.ps1

# Activate (Linux/Mac)
source .venv/bin/activate

# Install
pip install -e .
```

### 3. Configure .env

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

### 4. Run

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

### 5. Verify

```bash
curl http://localhost:8000/status
```

Should return:
```json
{"status":"ok","codex_available":true,"codex_version":"...","api_key_configured":true}
```
