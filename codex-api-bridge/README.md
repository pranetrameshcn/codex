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
| `LOG_LEVEL` | No | INFO | Logging level |
