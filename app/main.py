from __future__ import annotations

import base64
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

from app import art, tester

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
SELF_URL = os.getenv("SELF_URL", f"http://127.0.0.1:{os.getenv('PORT', '8000')}")

MAX_TEST_ROUNDS = int(os.getenv("MAX_TEST_ROUNDS", "2"))
REPAIR_TEST_ROUNDS = 1
MAX_REVIEWS = 0
MAX_REPAIRS = 3
MAX_TESTS = 20
MAX_TEST_STEPS = 120
TEST_ACTIONS = ("tap", "press", "release", "select", "move", "wait", "expect", "run", "restart")
DIRECTIONS = ("", "up", "down", "left", "right")
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
        "tests": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "steps": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "do": {"type": "string", "enum": list(TEST_ACTIONS)},
                                "player": {"type": "integer"},
                                "control": {"type": "string"},
                                "direction": {"type": "string", "enum": list(DIRECTIONS)},
                                "index": {"type": "integer"},
                                "x": {"type": "number"},
                                "y": {"type": "number"},
                                "seconds": {"type": "number"},
                                "expression": {"type": "string"},
                                "description": {"type": "string"},
                            },
                            "required": [
                                "do", "player", "control", "direction", "index", "x", "y",
                                "seconds", "expression", "description",
                            ],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["name", "steps"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["name", "players", "controls", "game_js", "tests"],
    "additionalProperties": False,
}
REVIEW_SCHEMA = {
    "type": "object",
    "properties": {"issues": {"type": "array", "items": {"type": "string"}}},
    "required": ["issues"],
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


@app.post("/api/transcribe")
async def transcribe_audio(audio: UploadFile = File(...)) -> dict[str, str]:
    recording_id = uuid.uuid4().hex[:10]
    extension = Path(audio.filename or "recording.wav").suffix or ".wav"
    audio_path = UPLOADS_DIR / f"transcript-{recording_id}{extension}"
    try:
        with audio_path.open("wb") as target:
            shutil.copyfileobj(audio.file, target)
        text = await transcribe(audio_path)
        if not text:
            raise HTTPException(422, "The recording did not contain speech.")
        return {"text": text}
    finally:
        audio_path.unlink(missing_ok=True)


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
        response["quality"] = metadata.get("quality")
        design = read_text(GAMES_DIR / game_id / "design.md")
        if design:
            response["design"] = design[:1200]
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

        if not XAI_API_KEY:
            write_package(game_id, transcript, demo_package(), design="")
        else:
            job["stage"] = "Looking up existing sprites…"
            choice = await find_existing_sprites(transcript)
            if choice["source"] == "kit":
                design = choice["brief"]
            else:
                job["stage"] = "Drawing original pixel art…"
                pieces = await plan_art(transcript)
                images = await art.render_pieces(XAI_API_KEY, pieces)
                pieces = [piece for piece in pieces if piece["id"] in images]
                design = art.art_brief(game_id, pieces) if pieces else ""
            job["stage"] = "Writing the game…"
            package = await generate_package(transcript, design)
            write_package(game_id, transcript, package, design=design)
            if choice["source"] != "kit" and images:
                art.save_art(GAMES_DIR / game_id, images)
            await refine(
                game_id, transcript, design, package, job, rounds=MAX_TEST_ROUNDS, review=False
            )
            if not _playable(read_report(game_id)):
                job["stage"] = "Rewriting the game so it actually starts…"
                failures = read_report(game_id).get("problems") or ["the first version was unplayable"]
                package = await generate_package(
                    transcript + "\n\nA previous version failed these tests:\n- "
                    + "\n- ".join(str(item)[:400] for item in failures[:6]),
                    design,
                )
                save_version(game_id, package)
                await refine(
                    game_id, transcript, design, package, job, rounds=1, review=False
                )
        job["status"] = "ready"
        job["stage"] = None
    except Exception as exc:
        job["status"] = "failed"
        job["error"] = str(exc)
    finally:
        if audio_path:
            audio_path.unlink(missing_ok=True)


async def find_existing_sprites(request: str) -> dict[str, Any]:
    """Use public sprites when a real set already fits. Otherwise draw new art."""
    if art.known_helpers(request):
        return art.resolve_sprite_choice("", request)
    catalog = read_text(WEB_DIR / "assets-catalog.txt")
    instructions = (
        "Decide whether this game should use existing public sprites or brand-new art. "
        "Use web_search once. Look for a sprite set people already use for this exact game: "
        "chess pieces, Pokémon, playing cards, or a standard emoji that IS the piece "
        "(dice, for example). Reply in exactly this shape:\n"
        "SOURCE: kit\n"
        "HELPERS: chessPiece pokemon card emoji\n"
        "BRIEF: which codes or Pokémon ids to draw\n"
        "List only helpers that fit, using those four names. "
        "If the request is an original or personal idea, or no catalog sprite set matches, reply:\n"
        "SOURCE: original\n"
        "HELPERS:\n"
        "BRIEF:\n"
        "Do not invent image URLs. Do not write rules. Checkers, Connect Four, and made-up "
        "stories are original, not chess pieces or emoji.\n\n"
        + catalog
    )
    try:
        answer = await ask_xai(
            instructions, f"Game request: {request}", tools=[{"type": "web_search"}]
        )
    except Exception:
        answer = ""
    return art.resolve_sprite_choice(answer, request)


async def research_game(request: str) -> str:
    """Use web search to learn how real versions of this game look, play, and are tested."""
    guide = read_text(WEB_DIR / "research-prompt.txt") + "\n\n" + read_text(WEB_DIR / "assets-catalog.txt")
    try:
        return await ask_xai(guide, f"Game request: {request}", tools=[{"type": "web_search"}])
    except Exception:
        return await ask_xai(
            guide, f"Game request: {request}\n(Web search is unavailable. Use the catalog and known rules.)"
        )


ART_SCHEMA = {
    "type": "object",
    "properties": {
        "pieces": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "kind": {"type": "string", "enum": ["background", "sprite"]},
                    "subject": {"type": "string"},
                },
                "required": ["id", "kind", "subject"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["pieces"],
    "additionalProperties": False,
}


async def plan_art(request: str) -> list[dict[str, str]]:
    instructions = (
        "List the original pictures a Nintendo DS style game needs for this idea. "
        "One background of the place, plus one to three sprites (characters or important objects). "
        "Describe the specific subject, not a famous copyrighted character. "
        "ids are short lowercase words. kind is background or sprite."
    )
    try:
        raw = await ask_xai(instructions, f"Game idea: {request}", schema=ART_SCHEMA)
    except Exception:
        raw = {}
    return art.normalize_pieces(raw, request)


async def generate_package(request: str, design: str = "") -> dict[str, Any]:
    design_block = f"\n\nDESIGN NOTES (follow these):\n{design}" if design else ""
    multiplayer = (
        "\n\nThe user asked for multiplayer. players must be 2. Give player 1 and player 2 "
        "each a full phone controller. onInput must use event.player so both humans play. "
        "Do not replace the second player with a computer."
        if wants_two_players(request)
        else ""
    )
    content = (
        f"Game request: {request}{design_block}{multiplayer}\n\n"
        "Build the complete game now, with 3 to 5 short scenario tests."
    )
    problems: list[str] = []
    for _attempt in range(2):
        feedback = (
            "\n\nYour previous answer had these problems, fix them:\n- " + "\n- ".join(problems)
            if problems
            else ""
        )
        package = normalize_package(
            await ask_xai(game_prompt(), content + feedback, schema=GAME_SCHEMA),
            2 if wants_two_players(request) else None,
        )
        problems = check_package(package, art_paths(design), required_helpers(design))
        if not problems:
            return package
    raise RuntimeError("Generated game failed checks: " + "; ".join(problems))


async def fix_package(
    request: str, design: str, package: dict[str, Any], problems: list[str]
) -> dict[str, Any]:
    content = (
        f"Game request: {request}\n\nDESIGN DOCUMENT:\n{design}\n\n"
        "Automated testing of your game found these problems:\n- "
        + "\n- ".join(problem[:1500] for problem in problems[:12])
        + "\n\nFix the root cause of every problem, then re-read the whole program for similar "
        "mistakes. Keep everything that already works. Keep the scenario tests and add a test "
        "for each bug you fixed. Only change an existing test if it contradicts the real rules "
        "of the game; never weaken or delete a test just to make it pass.\n\n"
        f"Current controls:\n{json.dumps(package['controls'])}\n\n"
        f"Current tests:\n{json.dumps(package['tests'])}\n\n"
        f"Current game.js:\n{package['game_js']}"
    )
    fixed = normalize_package(
        await ask_xai(game_prompt(), content, schema=GAME_SCHEMA), package["players"]
    )
    problems = check_package(fixed, art_paths(design), required_helpers(design))
    if problems:
        raise RuntimeError("; ".join(problems))
    return fixed


async def review_game(
    request: str, design: str, package: dict[str, Any], screenshot: bytes
) -> list[str]:
    """Compare a screenshot and the code against the researched checklist."""
    instructions = (
        "You are a strict QA lead reviewing a generated game before a live demo. Compare the "
        "screenshot and the code against the design document. Report only concrete, important "
        "problems: checklist features that are missing or clearly broken, rules implemented "
        "incorrectly, or screen elements that are unreadable, overlapping, or cut off. Do not "
        "report nitpicks or style preferences. Return an empty list if the game is good."
    )
    text = (
        f"Game request: {request}\n\nDESIGN DOCUMENT:\n{design}\n\n"
        f"Controls:\n{json.dumps(package['controls'])}\n\ngame.js:\n{package['game_js']}"
    )
    image = "data:image/png;base64," + base64.b64encode(screenshot).decode()
    content = [
        {"type": "input_text", "text": text},
        {"type": "input_image", "image_url": image, "detail": "high"},
    ]
    try:
        result = await ask_xai(instructions, content, schema=REVIEW_SCHEMA)
    except Exception:
        return []
    issues = result.get("issues") if isinstance(result, dict) else []
    return [f"Review: {issue}" for issue in issues[:8] if isinstance(issue, str) and issue.strip()]


async def refine(
    game_id: str,
    request: str,
    design: str,
    package: dict[str, Any],
    job: dict[str, Any],
    rounds: int,
    review: bool = True,
    problems: list[str] | None = None,
) -> dict[str, Any]:
    """Test, fix, and re-test until the game passes, keeping the best version seen."""
    best: tuple[int, dict[str, Any], dict[str, Any]] | None = None
    reviews = 0
    report_summary: dict[str, Any] = {}
    for round_number in range(1, rounds + 1):
        if problems:
            job["stage"] = f"Fixing {plural(len(problems), 'problem')} (round {round_number} of {rounds})…"
            try:
                package = await fix_package(request, design, package, problems)
            except Exception as exc:
                job["last_fix_error"] = str(exc)
                break
            save_version(game_id, package)

        job["stage"] = f"Testing the game (round {round_number} of {rounds})…"
        report = await tester.test_game(SELF_URL, game_id, package["tests"])
        report_summary = report.summary() | {"round": round_number, "problems": report.problems}
        if best is None or len(report.problems) <= best[0]:
            best = (len(report.problems), package, report_summary)
        problems = list(report.problems)

        if not problems and review and XAI_API_KEY and reviews < MAX_REVIEWS and report.screenshot:
            reviews += 1
            job["stage"] = "Checking the finished game against the research…"
            problems = await review_game(request, design, package, report.screenshot)
            report_summary["review"] = problems
        if not problems:
            break

    if best and best[1] is not package:
        package = best[1]
        save_version(game_id, package)
        report_summary = best[2]
    save_report(game_id, report_summary)
    return package


def plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


async def repair_game(game_id: str, report: dict[str, Any]) -> None:
    job = jobs[game_id]
    metadata = read_metadata(game_id) or {}
    try:
        package = load_package(game_id)
        design = read_text(GAMES_DIR / game_id / "design.md")
        problem = runtime_problem(package["game_js"], report)
        metadata["repairs"] = metadata.get("repairs", 0) + 1
        metadata["last_error"] = str(report.get("message", ""))[:500]
        write_metadata(game_id, metadata)
        await refine(
            game_id, metadata.get("prompt", ""), design, package, job,
            rounds=REPAIR_TEST_ROUNDS, review=False, problems=[problem],
        )
    except Exception as exc:
        job["repair_error"] = str(exc)
    finally:
        job["status"] = "ready"
        job["stage"] = None


def runtime_problem(code: str, report: dict[str, Any]) -> str:
    message = str(report.get("message", "Unknown error"))[:1000]
    stack = str(report.get("stack", ""))[:1500]
    where = str(report.get("where", "running"))[:60]
    context = ""
    line = _int(report.get("line"), 0, 100000, 0)
    if line and "game.js" in str(report.get("file", "")) + stack:
        lines = code.splitlines()
        first = max(1, line - 4)
        context = "\n".join(
            f"{number:>4}| {lines[number - 1]}"
            for number in range(first, min(len(lines), line + 4) + 1)
        )
        context = f"\n    Code around line {line}:\n{context}"
    return f"During live play the game crashed while {where}: {message}{context}\n    Stack: {stack}"


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


def game_prompt() -> str:
    guide = read_text(WEB_DIR / "game-generator-prompt.txt")
    catalog = read_text(WEB_DIR / "assets-catalog.txt")
    example = read_text(WEB_DIR / "examples" / "connect4.js")
    tests = read_text(WEB_DIR / "examples" / "connect4.tests.json")
    return (
        f"{guide}\n\n{catalog}\n\nEXAMPLE GAME (Connect Four, uses a choice control):\n{example}\n\n"
        f"EXAMPLE TESTS for that game:\n{tests}"
    )


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


def wants_two_players(request: str) -> bool:
    return bool(
        re.search(
            r"\b(multiplayer|two[- ]players?|2[- ]players?|co-?op|cooperative|pvp)\b",
            request,
            re.IGNORECASE,
        )
    )


def art_paths(design: str) -> list[str]:
    return re.findall(r"/games/[A-Za-z0-9_-]+/art/[a-z0-9]+\.png", design)


def required_helpers(design: str) -> list[str]:
    match = re.search(r"^HELPERS:\s*(.*)$", design, re.MULTILINE)
    if not match:
        return []
    return re.findall(r"g\.(chessPiece|pokemon|card|emoji)\b", match.group(1))


def check_package(
    package: dict[str, Any],
    paths: list[str] | None = None,
    helpers: list[str] | None = None,
) -> list[str]:
    problems = check_game_js(package["game_js"])
    if len(package.get("tests") or []) < 3:
        problems.append("tests must include at least 3 scenarios that prove the rules.")
    if package.get("players") == 2 and not re.search(r"event\.player|g\.players", package["game_js"]):
        problems.append(
            "A 2-player game must read event.player or g.players so both controllers do something."
        )
    missing = [path for path in paths or [] if path not in package["game_js"]]
    if missing:
        problems.append(
            "Draw the original pixel art with g.image. Missing: " + ", ".join(missing)
        )
    unused = [name for name in helpers or [] if f"g.{name}" not in package["game_js"]]
    if unused:
        problems.append(
            "Draw the existing sprites with their helpers. Missing: "
            + ", ".join(f"g.{name}" for name in unused)
        )
    return problems


def _playable(report: dict[str, Any]) -> bool:
    names = {check.get("name"): check.get("ok") for check in report.get("checks") or []}
    return bool(names.get("Game loads") and names.get("First frames run"))


def read_report(game_id: str) -> dict[str, Any]:
    raw = read_text(GAMES_DIR / game_id / "test-report.json")
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}


async def ask_xai(
    instructions: str,
    content: str | list[dict[str, Any]],
    schema: dict[str, Any] | None = None,
    tools: list[dict[str, Any]] | None = None,
) -> Any:
    """Call the xAI Responses API. Returns parsed JSON when a schema is given, else text."""
    payload: dict[str, Any] = {
        "model": XAI_GAME_MODEL,
        "input": [
            {"role": "system", "content": instructions},
            {"role": "user", "content": content},
        ],
    }
    if XAI_REASONING_EFFORT:
        payload["reasoning"] = {"effort": XAI_REASONING_EFFORT}
    if schema:
        payload["text"] = {
            "format": {"type": "json_schema", "name": "result", "strict": True, "schema": schema}
        }
    if tools:
        payload["tools"] = tools
    async with httpx.AsyncClient(timeout=600) as client:
        response = await client.post(
            "https://api.x.ai/v1/responses",
            headers={"Authorization": f"Bearer {XAI_API_KEY}", "Content-Type": "application/json"},
            json=payload,
        )
        if response.is_error and "reasoning" in response.text.lower() and "reasoning" in payload:
            payload.pop("reasoning")
            response = await client.post(
                "https://api.x.ai/v1/responses",
                headers={"Authorization": f"Bearer {XAI_API_KEY}", "Content-Type": "application/json"},
                json=payload,
            )
    if response.is_error:
        try:
            detail = response.json().get("error", response.text)
        except ValueError:
            detail = response.text
        raise RuntimeError(f"xAI request failed ({response.status_code}): {detail}")
    body = response.json()
    output_text = body.get("output_text") or "".join(
        item.get("text", "")
        for output in body.get("output", [])
        if output.get("type") == "message"
        for item in output.get("content", [])
        if item.get("type") in {"output_text", "text"}
    )
    return json.loads(output_text) if schema else output_text.strip()


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
        "tests": normalize_tests(raw.get("tests")),
    }


def normalize_tests(raw: Any) -> list[dict[str, Any]]:
    tests = []
    for item in (raw if isinstance(raw, list) else [])[:MAX_TESTS]:
        item = _dict(item)
        steps = []
        for step in (item.get("steps") if isinstance(item.get("steps"), list) else [])[:MAX_TEST_STEPS]:
            step = _dict(step)
            if step.get("do") not in TEST_ACTIONS:
                continue
            steps.append(
                {
                    "do": step["do"],
                    "player": _int(step.get("player"), 1, 2, 1),
                    "control": str(step.get("control") or "")[:40],
                    "direction": step.get("direction") if step.get("direction") in DIRECTIONS else "",
                    "index": _int(step.get("index"), -1, 99, 0),
                    "x": _float(step.get("x")),
                    "y": _float(step.get("y")),
                    "seconds": max(0.0, min(_float(step.get("seconds")), 60.0)),
                    "expression": str(step.get("expression") or "")[:2000],
                    "description": str(step.get("description") or "")[:200],
                }
            )
        if steps:
            tests.append({"name": _text(item.get("name"), 80, f"Test {len(tests) + 1}"), "steps": steps})
    return tests


def _float(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if number == number and abs(number) != float("inf") else 0.0


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig") if path.exists() else ""


def read_metadata(game_id: str) -> dict[str, Any] | None:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", game_id):
        return None
    path = GAMES_DIR / game_id / "metadata.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_metadata(game_id: str, metadata: dict[str, Any]) -> None:
    (GAMES_DIR / game_id / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")


def write_package(game_id: str, prompt: str, package: dict[str, Any], design: str = "") -> None:
    game_dir = GAMES_DIR / game_id
    game_dir.mkdir(parents=True, exist_ok=False)
    if design:
        (game_dir / "design.md").write_text(design, encoding="utf-8")
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


def save_version(game_id: str, package: dict[str, Any]) -> None:
    metadata = read_metadata(game_id) or {}
    metadata["version"] = metadata.get("version", 1) + 1
    write_files(game_id, package, metadata)


def save_report(game_id: str, summary: dict[str, Any]) -> None:
    (GAMES_DIR / game_id / "test-report.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    metadata = read_metadata(game_id) or {}
    metadata["quality"] = {
        "checks_passed": summary.get("checks_passed", 0),
        "checks_total": summary.get("checks_total", 0),
        "rounds": summary.get("round", 0),
        "known_issues": (summary.get("problems") or [])[:5],
    }
    write_metadata(game_id, metadata)


def load_package(game_id: str) -> dict[str, Any]:
    game_dir = GAMES_DIR / game_id
    metadata = read_metadata(game_id) or {}
    controls = json.loads(read_text(game_dir / "controller.json") or "{}")
    tests = json.loads(read_text(game_dir / "tests.json") or "[]")
    return normalize_package(
        {
            "name": metadata.get("name"),
            "players": metadata.get("players"),
            "controls": controls,
            "game_js": read_text(game_dir / "game.js"),
            "tests": tests,
        },
        metadata.get("players"),
    )


def write_files(game_id: str, package: dict[str, Any], metadata: dict[str, Any]) -> None:
    game_dir = GAMES_DIR / game_id
    metadata["name"] = package["name"]
    files = {
        "game.js": package["game_js"],
        "controller.json": json.dumps(package["controls"], indent=2),
        "tests.json": json.dumps(package.get("tests", []), indent=2),
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
            "game_js": read_text(WEB_DIR / "examples" / "connect4.js"),
            "tests": json.loads(read_text(WEB_DIR / "examples" / "connect4.tests.json")),
        }
    )
