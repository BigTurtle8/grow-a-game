from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv
from fastapi import (
    BackgroundTasks,
    Body,
    FastAPI,
    File,
    Form,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
WEB_DIR = ROOT / "web"
GAMES_DIR = ROOT / "games"
UPLOADS_DIR = ROOT / ".uploads"
ASSET_CACHE_DIR = ROOT / ".cache" / "assets"
BRAND_LOGO_PATH = WEB_DIR / "brand-logo.png"
for directory in (GAMES_DIR, UPLOADS_DIR, ASSET_CACHE_DIR):
    directory.mkdir(parents=True, exist_ok=True)

XAI_API_KEY = os.getenv("XAI_API_KEY")
XAI_STT_MODEL = os.getenv("XAI_STT_MODEL", "grok-voice-transcribe-2.0")
XAI_GAME_MODEL = os.getenv("XAI_GAME_MODEL", "grok-4.6")
XAI_REASONING_EFFORT = os.getenv("XAI_REASONING_EFFORT", "low")

MAX_REPAIRS = 3
MAX_ASSET_BYTES = 8 * 1024 * 1024
CONTROL_TYPES = ("dpad", "joystick", "button", "choice")
MAX_CONTROLS = 8
MAX_CHOICE_OPTIONS = 12
DEFAULT_CONTROL_IDS = {"dpad": "move", "joystick": "stick", "button": "a", "choice": "choice"}
DEFAULT_CONTROL_LABELS = {"dpad": "Move", "joystick": "Move", "button": "A", "choice": "Choose"}
DEFAULT_CONTROLS = [
    {"type": "dpad", "id": "move", "label": "Move"},
    {"type": "button", "id": "a", "label": "A"},
]

CONTROL_SCHEMA = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": list(CONTROL_TYPES)},
        "id": {"type": "string"},
        "label": {"type": "string"},
        "options": {"type": "array", "items": {"type": "string"}},
        "columns": {"type": "integer"},
        "color": {"type": "string"},
    },
    "required": ["type", "id", "label", "options", "columns", "color"],
    "additionalProperties": False,
}
GAME_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "players": {"type": "integer"},
        "controls": {
            "type": "object",
            "properties": {
                "player1": {"type": "array", "items": CONTROL_SCHEMA},
                "player2": {"type": "array", "items": CONTROL_SCHEMA},
            },
            "required": ["player1", "player2"],
            "additionalProperties": False,
        },
        "game_js": {"type": "string"},
    },
    "required": ["name", "players", "controls", "game_js"],
    "additionalProperties": False,
}

app = FastAPI(title="Grow-a-Game", version="0.3.0")
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
        for socket in list(self.games.get(game_id, set())):
            try:
                await socket.send_json(message)
            except Exception:
                dead.append(socket)
        for socket in dead:
            self.disconnect(game_id, socket)


sockets = GameSockets()


@app.get("/", include_in_schema=False)
async def home() -> FileResponse:
    return FileResponse(WEB_DIR / "app" / "index.html")


@app.get("/brand-logo.png", include_in_schema=False)
async def brand_logo() -> FileResponse:
    return FileResponse(BRAND_LOGO_PATH, media_type="image/png")


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
        "stage": "Listening to your idea…" if audio else "Writing the game…",
        "prompt": prompt,
        "transcript": None,
        "error": None,
    }
    background_tasks.add_task(build_game, game_id, prompt, audio_path)
    return {"game_id": game_id, "status": "generating"}


@app.get("/api/games/{game_id}/status")
async def game_status(game_id: str) -> dict[str, Any]:
    metadata = read_metadata(game_id)
    job = jobs.get(game_id)
    if not job and metadata:
        job = {"game_id": game_id, "status": "ready", "transcript": metadata.get("prompt")}
    if not job:
        raise HTTPException(404, "Unknown game ID.")

    response = dict(job)
    if metadata:
        response["version"] = metadata.get("version", 1)
        response["repairs"] = metadata.get("repairs", 0)
    if job["status"] in ("ready", "repairing"):
        response.update(
            {
                "game_url": f"/games/{game_id}/",
                "controller_url": f"/games/{game_id}/controller.json",
                "player1_url": f"/controller?game={game_id}&player=1",
                "player2_url": f"/controller?game={game_id}&player=2",
            }
        )
    return response


@app.post("/api/games/{game_id}/errors")
async def report_error(
    game_id: str, background_tasks: BackgroundTasks, report: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    metadata = read_metadata(game_id)
    if not metadata:
        raise HTTPException(404, "Unknown game ID.")
    job = jobs.setdefault(game_id, {"game_id": game_id, "status": "ready", "error": None})
    version = metadata.get("version", 1)
    if job["status"] == "repairing" or _int(report.get("version"), 0, 10**6, version) < version:
        return {"repairing": True}
    if not XAI_API_KEY or not (GAMES_DIR / game_id / "game.js").exists():
        return {"repairing": False, "reason": "Automatic repair needs XAI_API_KEY."}
    if metadata.get("repairs", 0) >= MAX_REPAIRS:
        return {"repairing": False, "reason": "Repair limit reached for this game."}
    job["status"] = "repairing"
    job["repair_error"] = None
    background_tasks.add_task(repair_game, game_id, report)
    return {"repairing": True}


@app.get("/api/asset")
async def asset(url: str) -> Response:
    """Fetch and cache an online image or sound so games can use it without CORS problems."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or _is_private(parsed.hostname):
        raise HTTPException(400, "Only public http(s) asset URLs are allowed.")
    key = hashlib.sha256(url.encode()).hexdigest()
    data_path = ASSET_CACHE_DIR / key
    type_path = ASSET_CACHE_DIR / f"{key}.type"
    headers = {"Cache-Control": "public, max-age=86400"}
    if data_path.exists() and type_path.exists():
        return FileResponse(data_path, media_type=type_path.read_text(), headers=headers)
    try:
        async with httpx.AsyncClient(
            timeout=15, follow_redirects=True, headers={"User-Agent": "grow-a-game/0.3"}
        ) as client:
            upstream = await client.get(url)
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Could not fetch asset: {exc}") from exc
    if upstream.status_code != 200:
        raise HTTPException(404, f"Asset returned HTTP {upstream.status_code}.")
    media_type = upstream.headers.get("content-type", "").split(";")[0].strip()
    if not media_type.startswith(("image/", "audio/")):
        raise HTTPException(415, "Asset is not an image or sound.")
    if len(upstream.content) > MAX_ASSET_BYTES:
        raise HTTPException(413, "Asset is too large.")
    data_path.write_bytes(upstream.content)
    type_path.write_text(media_type)
    return Response(upstream.content, media_type=media_type, headers=headers)


def _is_private(hostname: str) -> bool:
    if hostname.lower() in ("localhost", "localhost.localdomain"):
        return True
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return address.is_private or address.is_loopback or address.is_link_local


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
    job = jobs[game_id]
    try:
        transcript = supplied_prompt
        if audio_path:
            transcript = await transcribe(audio_path)
        if not transcript:
            raise ValueError("The recording did not contain a game prompt.")

        job["transcript"] = transcript
        job["stage"] = "Writing the game…"
        package = await generate_package(transcript) if XAI_API_KEY else demo_package()
        write_package(game_id, transcript, package)
        job["status"] = "ready"
    except Exception as exc:
        job["status"] = "failed"
        job["error"] = str(exc)
    finally:
        if audio_path:
            audio_path.unlink(missing_ok=True)


async def generate_package(request: str) -> dict[str, Any]:
    feedback = ""
    for _attempt in range(2):
        package = normalize_package(await ask_xai(request + feedback))
        problems = check_game_js(package["game_js"])
        if not problems:
            return package
        feedback = (
            "\n\nYour previous answer had these problems, fix them and return the full game:\n- "
            + "\n- ".join(problems)
        )
    raise RuntimeError("Generated game failed checks: " + "; ".join(problems))


def check_game_js(code: str) -> list[str]:
    problems = []
    if not re.search(r"""from\s+["']/static/kit\.js["']""", code):
        problems.append('game_js must import from "/static/kit.js".')
    if not re.search(r"\bstart\s*\(", code):
        problems.append("game_js must call start({...}).")
    if re.search(r"""\bimport\s+[^;]*from\s+["'](?!/static/kit\.js|three)""", code):
        problems.append('Only "/static/kit.js" and "three" may be imported.')
    if len(code) < 200:
        problems.append("game_js is too short to be a complete game.")
    return problems


async def repair_game(game_id: str, report: dict[str, Any]) -> None:
    job = jobs[game_id]
    game_dir = GAMES_DIR / game_id
    metadata = read_metadata(game_id) or {}
    try:
        code = (game_dir / "game.js").read_text(encoding="utf-8-sig")
        controls = json.loads((game_dir / "controller.json").read_text(encoding="utf-8-sig"))
        request = repair_request(metadata.get("prompt", ""), code, controls, report)
        package = normalize_package(await ask_xai(request), metadata.get("players", 1))
        problems = check_game_js(package["game_js"])
        if problems:
            raise RuntimeError("; ".join(problems))
        metadata["version"] = metadata.get("version", 1) + 1
        metadata["repairs"] = metadata.get("repairs", 0) + 1
        metadata["last_error"] = str(report.get("message", ""))[:500]
        write_files(game_id, package, metadata)
    except Exception as exc:
        metadata["repairs"] = metadata.get("repairs", 0) + 1
        (game_dir / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        job["repair_error"] = str(exc)
    finally:
        job["status"] = "ready"


def repair_request(prompt: str, code: str, controls: dict[str, Any], report: dict[str, Any]) -> str:
    message = str(report.get("message", "Unknown error"))[:1000]
    stack = str(report.get("stack", ""))[:2000]
    where = str(report.get("where", "running"))[:40]
    context = ""
    line = _int(report.get("line"), 0, 100000, 0)
    if line and "game.js" in str(report.get("file", "")) + stack:
        lines = code.splitlines()
        start_line = max(1, line - 4)
        context = "\n".join(
            f"{number:>4}| {lines[number - 1]}"
            for number in range(start_line, min(len(lines), line + 4) + 1)
        )
        context = f"\nCode around line {line}:\n{context}\n"
    return (
        f"This game was generated for the request: {prompt!r}\n"
        f"It crashed while {where} with this error:\n{message}\n{context}"
        f"Stack trace:\n{stack}\n\n"
        "Return the complete corrected game in the same JSON format. Fix the root cause, then "
        "re-read the whole program for similar mistakes (undefined variables, missing state, "
        "wrong kit API names). Keep the design and everything that already works.\n\n"
        f"Current controls:\n{json.dumps(controls)}\n\nCurrent game.js:\n{code}"
    )


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


def system_prompt() -> str:
    guide = (WEB_DIR / "game-generator-prompt.txt").read_text(encoding="utf-8")
    example = (WEB_DIR / "examples" / "connect4.js").read_text(encoding="utf-8")
    return f"{guide}\n\nEXAMPLE GAME (Connect Four, uses a choice control):\n{example}"


async def ask_xai(request: str) -> dict[str, Any]:
    payload = {
        "model": XAI_GAME_MODEL,
        "reasoning": {"effort": XAI_REASONING_EFFORT},
        "input": [
            {"role": "system", "content": system_prompt()},
            {"role": "user", "content": request},
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "generated_game",
                "strict": True,
                "schema": GAME_SCHEMA,
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


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _int(value: Any, low: int, high: int, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _text(value: Any, limit: int, default: str) -> str:
    text = re.sub(r"[<>]|\s+", " ", str(value or "")).strip()[:limit].strip()
    return text or default


def normalize_control(raw: Any, used_ids: set[str]) -> dict[str, Any]:
    raw = _dict(raw)
    kind = raw.get("type") if raw.get("type") in CONTROL_TYPES else "button"
    base = re.sub(r"[^a-z0-9_]", "", str(raw.get("id") or "").lower())[:20]
    base = base or DEFAULT_CONTROL_IDS[kind]
    control_id, suffix = base, 2
    while control_id in used_ids:
        control_id, suffix = f"{base}{suffix}", suffix + 1
    used_ids.add(control_id)
    control: dict[str, Any] = {
        "type": kind,
        "id": control_id,
        "label": _text(raw.get("label"), 18, DEFAULT_CONTROL_LABELS[kind]),
    }
    if kind == "choice":
        options = raw.get("options") if isinstance(raw.get("options"), list) else []
        options = [_text(option, 18, "") for option in options[:MAX_CHOICE_OPTIONS]]
        control["options"] = [option for option in options if option] or ["1", "2"]
        columns = _int(raw.get("columns"), 0, 7, 0)
        if columns:
            control["columns"] = columns
    color = str(raw.get("color") or "")
    if re.fullmatch(r"#[0-9a-fA-F]{3}([0-9a-fA-F]{3})?", color):
        control["color"] = color
    return control


def normalize_controls(raw: Any, players: int) -> dict[str, Any]:
    raw = _dict(raw)
    result: dict[str, Any] = {"players": players}
    for player in range(1, players + 1):
        items = raw.get(f"player{player}")
        items = items if isinstance(items, list) else []
        if not items and player == 2:
            items = raw.get("player1") if isinstance(raw.get("player1"), list) else []
        used: set[str] = set()
        controls = [normalize_control(item, used) for item in items[:MAX_CONTROLS]]
        result[f"player{player}"] = controls or [dict(control) for control in DEFAULT_CONTROLS]
    return result


def normalize_package(raw: Any, players: int | None = None) -> dict[str, Any]:
    raw = _dict(raw)
    players = players or _int(raw.get("players"), 1, 2, 1)
    return {
        "name": _text(raw.get("name"), 60, "Generated Game"),
        "players": players,
        "controls": normalize_controls(raw.get("controls"), players),
        "game_js": str(raw.get("game_js") or ""),
    }


def read_metadata(game_id: str) -> dict[str, Any] | None:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", game_id):
        return None
    path = GAMES_DIR / game_id / "metadata.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_package(game_id: str, prompt: str, package: dict[str, Any]) -> None:
    (GAMES_DIR / game_id).mkdir(parents=True, exist_ok=False)
    metadata = {
        "game_id": game_id,
        "name": package["name"],
        "players": package["players"],
        "orientation": "landscape",
        "prompt": prompt,
        "version": 1,
        "repairs": 0,
    }
    write_files(game_id, package, metadata)


def write_files(game_id: str, package: dict[str, Any], metadata: dict[str, Any]) -> None:
    game_dir = GAMES_DIR / game_id
    metadata["name"] = package["name"]
    files = {
        "game.js": package["game_js"],
        "controller.json": json.dumps(package["controls"], indent=2),
        "index.html": game_html(package["name"]),
        "metadata.json": json.dumps(metadata, indent=2),
    }
    for name, content in files.items():
        (game_dir / name).write_text(content, encoding="utf-8")


def game_html(name: str) -> str:
    safe_name = re.sub(r"[<>&\"]", "", name)
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
  <script type="module">import {{ boot }} from "/static/kit.js"; boot();</script>
</body>
</html>"""


def demo_package() -> dict[str, Any]:
    choice = {
        "type": "choice",
        "id": "column",
        "label": "Drop a piece",
        "options": [str(column) for column in range(1, 8)],
        "columns": 7,
    }
    return normalize_package(
        {
            "name": "Connect Four",
            "players": 2,
            "controls": {"player1": [choice], "player2": [choice]},
            "game_js": (WEB_DIR / "examples" / "connect4.js").read_text(encoding="utf-8"),
        }
    )
