from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import (
    BackgroundTasks,
    FastAPI,
    File,
    Form,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
WEB_DIR = ROOT / "web"
GAMES_DIR = ROOT / "games"
UPLOADS_DIR = ROOT / ".uploads"
GAMES_DIR.mkdir(exist_ok=True)
UPLOADS_DIR.mkdir(exist_ok=True)

XAI_API_KEY = os.getenv("XAI_API_KEY")
XAI_STT_MODEL = os.getenv("XAI_STT_MODEL", "grok-voice-transcribe-2.0")
XAI_GAME_MODEL = os.getenv("XAI_GAME_MODEL", "grok-4.6")

app = FastAPI(title="Grow-a-Game", version="0.1.0")
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
app.mount("/games", StaticFiles(directory=GAMES_DIR, html=True), name="games")

jobs: dict[str, dict[str, Any]] = {}


class GameSockets:
    def __init__(self) -> None:
        self.games: dict[str, set[WebSocket]] = {}

    async def connect(self, game_id: str, socket: WebSocket) -> None:
        await socket.accept()
        self.games.setdefault(game_id, set()).add(socket)

    def disconnect(self, game_id: str, socket: WebSocket) -> None:
        self.games.get(game_id, set()).discard(socket)

    async def broadcast(self, game_id: str, message: dict[str, Any]) -> None:
        dead: list[WebSocket] = []
        for socket in self.games.get(game_id, set()):
            try:
                await socket.send_json(message)
            except Exception:
                dead.append(socket)
        for socket in dead:
            self.disconnect(game_id, socket)


sockets = GameSockets()


@app.get("/", include_in_schema=False)
async def home() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/controller", include_in_schema=False)
async def controller() -> FileResponse:
    return FileResponse(WEB_DIR / "controller.html")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/games/generate", status_code=202)
async def generate_game(
    background_tasks: BackgroundTasks,
    prompt: str | None = Form(default=None),
    audio: UploadFile | None = File(default=None),
) -> dict[str, str]:
    if not prompt and not audio:
        raise HTTPException(400, "Provide a prompt or an audio file.")

    game_id = uuid.uuid4().hex[:10]
    audio_path: Path | None = None
    if audio:
        extension = Path(audio.filename or "recording.wav").suffix or ".wav"
        audio_path = UPLOADS_DIR / f"{game_id}{extension}"
        with audio_path.open("wb") as target:
            shutil.copyfileobj(audio.file, target)

    jobs[game_id] = {
        "game_id": game_id,
        "status": "generating",
        "prompt": prompt,
        "transcript": None,
        "error": None,
    }
    background_tasks.add_task(build_game, game_id, prompt, audio_path)
    return {"game_id": game_id, "status": "generating"}


@app.get("/api/games/{game_id}/status")
async def game_status(game_id: str) -> dict[str, Any]:
    job = jobs.get(game_id)
    game_dir = GAMES_DIR / game_id
    if not job and (game_dir / "metadata.json").exists():
        job = {
            "game_id": game_id,
            "status": "ready",
            "transcript": None,
            "error": None,
        }
    if not job:
        raise HTTPException(404, "Unknown game ID.")

    response = dict(job)
    if job["status"] == "ready":
        response.update(
            {
                "game_url": f"/games/{game_id}/",
                "controller_url": f"/games/{game_id}/controller.json",
                "player1_url": f"/controller?game={game_id}&player=1",
                "player2_url": f"/controller?game={game_id}&player=2",
            }
        )
    return response


@app.websocket("/ws/games/{game_id}")
async def game_socket(websocket: WebSocket, game_id: str) -> None:
    await sockets.connect(game_id, websocket)
    try:
        while True:
            message = await websocket.receive_json()
            await sockets.broadcast(game_id, message)
    except WebSocketDisconnect:
        sockets.disconnect(game_id, websocket)


async def build_game(
    game_id: str, supplied_prompt: str | None, audio_path: Path | None
) -> None:
    try:
        transcript = supplied_prompt
        if audio_path:
            transcript = await transcribe(audio_path)
        if not transcript:
            raise ValueError("The recording did not contain a game prompt.")

        jobs[game_id]["transcript"] = transcript
        package = (
            await generate_with_xai(transcript)
            if XAI_API_KEY
            else demo_package(transcript)
        )
        write_package(game_id, transcript, package)
        jobs[game_id]["status"] = "ready"
    except Exception as exc:
        jobs[game_id]["status"] = "failed"
        jobs[game_id]["error"] = str(exc)
    finally:
        if audio_path:
            audio_path.unlink(missing_ok=True)


async def transcribe(audio_path: Path) -> str:
    if not XAI_API_KEY:
        raise RuntimeError(
            "Audio transcription needs XAI_API_KEY. Use the text prompt demo meanwhile."
        )
    headers = {"Authorization": f"Bearer {XAI_API_KEY}"}
    data = {"model": XAI_STT_MODEL, "language": "en", "format": "true"}
    async with httpx.AsyncClient(timeout=120) as client:
        with audio_path.open("rb") as audio_file:
            response = await client.post(
                "https://api.x.ai/v1/stt",
                headers=headers,
                data=data,
                files={"file": (audio_path.name, audio_file)},
            )
    response.raise_for_status()
    return response.json()["text"].strip()


async def generate_with_xai(prompt: str) -> dict[str, Any]:
    system_prompt = (WEB_DIR / "game-generator-prompt.txt").read_text()
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "players": {"type": "integer", "minimum": 1, "maximum": 2},
            "game_js": {"type": "string"},
            "controller": {"type": "object"},
        },
        "required": ["name", "players", "game_js", "controller"],
        "additionalProperties": False,
    }
    payload = {
        "model": XAI_GAME_MODEL,
        "reasoning": {"effort": "low"},
        "input": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "generated_game",
                "strict": True,
                "schema": schema,
            }
        },
    }
    async with httpx.AsyncClient(timeout=300) as client:
        response = await client.post(
            "https://api.x.ai/v1/responses",
            headers={
                "Authorization": f"Bearer {XAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
    if response.is_error:
        try:
            detail = response.json().get("error", response.text)
        except ValueError:
            detail = response.text
        raise RuntimeError(f"xAI game generation failed ({response.status_code}): {detail}")
    body = response.json()
    output_text = body.get("output_text")
    if not output_text:
        output_text = next(
            item["text"]
            for output in body.get("output", [])
            for item in output.get("content", [])
            if item.get("type") in {"output_text", "text"} and item.get("text")
        )
    return json.loads(output_text)


def write_package(game_id: str, prompt: str, package: dict[str, Any]) -> None:
    game_dir = GAMES_DIR / game_id
    game_dir.mkdir(parents=True, exist_ok=False)
    players = max(1, min(2, int(package.get("players", 2))))
    metadata = {
        "game_id": game_id,
        "name": str(package.get("name", "Generated Game"))[:80],
        "players": players,
        "orientation": "landscape",
        "prompt": prompt,
    }
    controller = normalize_controller(package.get("controller"), players)
    (game_dir / "index.html").write_text(game_html(metadata["name"]))
    (game_dir / "game.js").write_text(str(package["game_js"]))
    (game_dir / "metadata.json").write_text(json.dumps(metadata, indent=2))
    (game_dir / "controller.json").write_text(json.dumps(controller, indent=2))


def normalize_controller(controller: Any, players: int) -> dict[str, Any]:
    if not isinstance(controller, dict):
        controller = {}
    result: dict[str, Any] = {"players": players}
    defaults = [
        {"type": "joystick", "action": "move", "label": "Move"},
        {"type": "button", "action": "action", "label": "Action"},
    ]
    for player in range(1, players + 1):
        controls = controller.get(f"player{player}", defaults)
        result[f"player{player}"] = controls if isinstance(controls, list) else defaults
    return result


def game_html(name: str) -> str:
    safe_name = re.sub(r"[<>]", "", name)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8"><meta name="viewport" content="width=device-width">
  <title>{safe_name}</title>
  <link rel="stylesheet" href="/static/styles.css">
</head>
<body class="game-page">
  <div id="game"></div>
  <div class="game-hud">
    <strong>{safe_name}</strong>
    <span id="connection">Connecting controllers…</span>
    <a href="/">Console</a>
  </div>
  <script type="importmap">{{"imports":{{"three":"https://cdn.jsdelivr.net/npm/three@0.180.0/build/three.module.js"}}}}</script>
  <script type="module" src="./game.js"></script>
</body>
</html>"""


def demo_package(prompt: str) -> dict[str, Any]:
    words = prompt.lower()
    players = 2 if any(word in words for word in ("two", "2", "multiplayer")) else 1
    name = "Neon Grow Arena"
    if "fish" in words:
        name = "Pocket Fishing"
    elif "race" in words:
        name = "Block Racer"
    elif "dinosaur" in words:
        name = "Volcano Escape"
    return {
        "name": name,
        "players": players,
        "controller": {
            f"player{player}": [
                {"type": "joystick", "action": "move", "label": "Move"},
                {"type": "button", "action": "action", "label": "Grow"},
            ]
            for player in range(1, players + 1)
        },
        "game_js": (WEB_DIR / "demo-game.js").read_text(),
    }

