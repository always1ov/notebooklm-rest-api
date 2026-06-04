# notebooklm-rest-api

> A REST API wrapper for Google NotebookLM powered by `notebooklm-py`

Exposes NotebookLM notebook management, source ingestion, Q&A, artifact generation, and audio transcription as a clean HTTP API. Includes built-in Chinese (Simplified) default prompts and automatic session refresh.

---

## Features

### Notebook Management
- Create, list, get, rename, delete notebooks
- Get summary and description

### Source Management
- Add URL, YouTube, raw text, or file sources
- Get full text and source guide
- Delete sources

### Chat
- Ask questions against notebook content (auto-prefixed with Chinese reply instruction)

### Artifact Generation
- Audio, Video, Report, Quiz, Flashcards, Slide Deck, Infographic, Data Table, Mind Map
- Task polling and file download
- Default Chinese prompts per artifact type, overridable via environment variables

### Audio Transcription
- `POST /v1/transcribe` — upload an audio file, get a Chinese simplified verbatim transcript in one call

### Session Auto-Refresh
- Built-in background task refreshes `storage_state.json` every 12 hours — no separate container needed

### Optional API Key Protection

---

## Architecture

```
Client (REST)
    ↓
FastAPI  +  Background session refresh (every 12 h)
    ↓
notebooklm-py
    ↓
NotebookLM (Web API)
```

---

## Requirements

- Python 3.10+
- Google account with NotebookLM access
- `storage_state.json` from a one-time browser login

---

## Installation

### 1. Create virtual environment

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
playwright install chromium
```

### 3. Authenticate (one-time)

```bash
notebooklm login
```

Saves session to `~/.notebooklm/storage_state.json` by default. Override with:

```bash
export NOTEBOOKLM_STORAGE_PATH=/path/to/storage_state.json
```

---

## Run

```bash
uvicorn app:app --host 0.0.0.0 --port 8000
```

Swagger UI: `http://localhost:8000/docs`

---

## Docker

Single container — session refresh runs inside the API process:

```yaml
services:
  notebooklm-api:
    image: ghcr.io/always1ov/notebooklm-rest-api:latest
    ports:
      - "8000:8000"
    volumes:
      - ./auth:/auth
    environment:
      - NOTEBOOKLM_STORAGE_PATH=/auth/storage_state.json
      - NOTEBOOKLM_REST_API_KEY=your-secret-key
      - HTTP_PROXY=${HTTP_PROXY:-}
      - HTTPS_PROXY=${HTTPS_PROXY:-}
    restart: unless-stopped
```

---

## Environment Variables

| Variable | Description |
|---|---|
| `NOTEBOOKLM_STORAGE_PATH` | Path to `storage_state.json` |
| `NOTEBOOKLM_AUTH_JSON` | Inject auth JSON directly |
| `NOTEBOOKLM_HOME` | Base notebooklm directory |
| `NOTEBOOKLM_REST_API_KEY` | API key for request authentication |
| `CHAT_LANGUAGE_PREFIX` | Prefix prepended to every chat question (default: `请用中文回答。`) |
| `TRANSCRIBE_PROMPT` | Default transcription instruction |
| `PROMPT_<TYPE>` | Override default prompt for an artifact type, e.g. `PROMPT_REPORT` |
| `OUTPUT_FORMAT_<TYPE>` | Override output format for an artifact type, e.g. `OUTPUT_FORMAT_QUIZ=markdown` |

---

## API Examples

### List Notebooks
```
GET /v1/notebooks
```

### Create Notebook
```json
POST /v1/notebooks
{ "title": "My Research" }
```

### Add URL Source
```json
POST /v1/notebooks/{notebook_id}/sources/url
{ "url": "https://example.com", "wait": true }
```

### Ask Question
```json
POST /v1/notebooks/{notebook_id}/chat/ask
{ "question": "总结关键观点" }
```

### Generate Artifact
```json
POST /v1/notebooks/{notebook_id}/artifacts/generate
{ "type": "report", "options": {} }
```

### Poll Task
```
GET /v1/notebooks/{notebook_id}/artifacts/tasks/{task_id}
```

### Download Artifact
```
GET /v1/notebooks/{notebook_id}/artifacts/download?type=report
```

### Transcribe Audio
```bash
curl -X POST /v1/transcribe \
  -F "upload=@recording.mp3"
# Returns: { "ok": true, "transcription": "..." }
```

Optional parameters: `notebook_id`, `prompt`, `keep_notebook`, `source_wait_seconds` (default 8).

---

## API Key Protection

Set `NOTEBOOKLM_REST_API_KEY` and pass the header:

```
X-API-Key: your-secret-key
```

---

## Disclaimer

This project is **not an official Google NotebookLM API**. It relies on `notebooklm-py`, which automates NotebookLM web interactions. Behavior may change if Google updates internal APIs.

---

## License

MIT
