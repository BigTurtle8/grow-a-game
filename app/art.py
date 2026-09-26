"""Art for a generated game.

Known games reuse public sprites (chess pieces, Pokémon, playing cards). Everything else
gets original Nintendo DS pixel art, saved under games/<id>/art and drawn with g.image.
Sprites are painted on magenta and then cut out so they can sit on the background.
"""

from __future__ import annotations

import base64
import io
import os
import re
from pathlib import Path
from typing import Any

import httpx

IMAGE_MODEL = os.getenv("XAI_IMAGE_MODEL", "grok-imagine-image-2.0")
MAX_PIECES = 4
HELPERS = ("chessPiece", "pokemon", "card", "emoji")

_CHESS = re.compile(r"\bchess\b", re.IGNORECASE)
_POKEMON = re.compile(r"pok[eé]mon", re.IGNORECASE)
_CARDS = re.compile(
    r"\b(poker|blackjack|solitaire|freecell|cribbage|rummy|canasta|hearts|spades|"
    r"euchre|crazy eights|go fish|playing cards?|deck of cards|card game)\b",
    re.IGNORECASE,
)

STYLE = (
    "16-bit Nintendo DS pixel art, like Pokemon HeartGold. Hard pixels, thick dark outlines, "
    "no gradients, no blur, no photorealism, no text, no letters, no watermark, no logo. "
    "Limited palette: grass #78c850, water #6890f0, cream #f8f0d0, night #203050, ink #1a1c2c."
)


def normalize_pieces(raw: Any, request: str) -> list[dict[str, str]]:
    items = raw.get("pieces") if isinstance(raw, dict) else None
    pieces: list[dict[str, str]] = []
    used: set[str] = set()
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        kind = "background" if item.get("kind") == "background" else "sprite"
        subject = re.sub(r"\s+", " ", str(item.get("subject") or "")).strip()[:240]
        if not subject:
            continue
        base = re.sub(r"[^a-z0-9]", "", str(item.get("id") or "").lower())[:16] or (
            "background" if kind == "background" else "sprite"
        )
        piece_id, suffix = base, 2
        while piece_id in used:
            piece_id = f"{base}{suffix}"
            suffix += 1
        used.add(piece_id)
        pieces.append({"id": piece_id, "kind": kind, "subject": subject})
        if len(pieces) >= MAX_PIECES:
            break
    if not any(piece["kind"] == "background" for piece in pieces):
        pieces.insert(
            0,
            {
                "id": "background",
                "kind": "background",
                "subject": f"the place where this game happens: {request[:180]}",
            },
        )
    if not any(piece["kind"] == "sprite" for piece in pieces):
        pieces.append(
            {
                "id": "hero",
                "kind": "sprite",
                "subject": f"the main character of this game: {request[:160]}",
            }
        )
    return pieces[:MAX_PIECES]


def known_helpers(request: str) -> list[str]:
    """Helpers we already know fit, without a web lookup."""
    found: list[str] = []
    if _CHESS.search(request):
        found.append("chessPiece")
    if _POKEMON.search(request):
        found.append("pokemon")
    if _CARDS.search(request):
        found.append("card")
    return found


def _helpers_in(text: str) -> list[str]:
    lowered = text.lower()
    return [name for name in HELPERS if name.lower() in lowered]


def resolve_sprite_choice(raw: Any, request: str) -> dict[str, Any]:
    """Turn a lookup answer into either kit sprites or original art.

    A request that names chess, Pokémon, or cards always uses those sprites, even
    when the lookup is empty or disagrees.
    """
    source = "original"
    helpers: list[str] = []
    note_lines: list[str] = []
    reading_brief = False
    for line in str(raw or "").splitlines():
        stripped = line.strip().strip("*").strip()
        label = stripped.upper()
        if label.startswith("SOURCE:"):
            source = "kit" if "KIT" in label else "original"
            reading_brief = False
        elif label.startswith("HELPERS:"):
            helpers = _helpers_in(stripped.split(":", 1)[1])
            reading_brief = False
        elif label.startswith("BRIEF:"):
            reading_brief = True
            rest = stripped.split(":", 1)[1].strip()
            if rest:
                note_lines.append(rest)
        elif reading_brief and stripped:
            note_lines.append(stripped)
    for name in known_helpers(request):
        if name not in helpers:
            helpers.append(name)
    if known_helpers(request):
        source = "kit"
    if source != "kit" or not helpers:
        return {"source": "original", "helpers": [], "brief": ""}
    return {
        "source": "kit",
        "helpers": helpers,
        "brief": kit_brief(helpers, " ".join(note_lines)[:500]),
    }


def kit_brief(helpers: list[str], note: str = "") -> str:
    lines = [
        "EXISTING SPRITES match this game. Draw those real pictures with the helpers below.",
        "Do not replace them with letters, circles, or newly drawn art.",
        "HELPERS: " + " ".join(f"g.{name}" for name in helpers),
    ]
    if "chessPiece" in helpers:
        lines.append(
            "Every piece is g.chessPiece. Codes: wK wQ wR wB wN wP bK bQ bR bB bN bP. "
            "Draw each at least 64px with smooth:false."
        )
    if "pokemon" in helpers:
        lines.append(
            "Every creature is g.pokemon(nationalDexId). Use the real id "
            "(Pikachu 25, Charmander 4, Bulbasaur 1, Squirtle 7, Eevee 133). "
            "The player's battler uses {back:true}. Draw them large with smooth:false."
        )
    if "card" in helpers:
        lines.append(
            'Every card is g.card("AS") with rank A,2-9,0,J,Q,K and suit S,H,D,C. '
            "Face-down cards use g.cardBack()."
        )
    if "emoji" in helpers:
        lines.append(
            "Objects that are a standard emoji use g.emoji with the real character, such as g.emoji(\"🎲\")."
        )
    if note:
        lines.append(note)
    lines.append(
        "The board, panels, and bottom text box stay Nintendo DS style. The pieces themselves are the existing sprites."
    )
    return "\n".join(lines)


def art_brief(game_id: str, pieces: list[dict[str, str]]) -> str:
    lines = [
        "ORIGINAL ART was drawn for this game. Use only these pictures, via g.image. "
        "Do not call g.emoji, g.pokemon, g.chessPiece, or g.card.",
        "Draw the background first, full screen. Draw sprites on top with smooth:false.",
    ]
    for piece in pieces:
        path = f"/games/{game_id}/art/{piece['id']}.png"
        role = "full-screen background, 1280x720" if piece["kind"] == "background" else "transparent sprite"
        lines.append(f"- {piece['id']} ({role}): g.image({path!r}) — {piece['subject']}")
    return "\n".join(lines)


def _prompt(piece: dict[str, str]) -> str:
    if piece["kind"] == "background":
        return (
            f"{STYLE} A full game background of {piece['subject']}. "
            "Wide scene, no characters in the foreground, no text."
        )
    return (
        f"{STYLE} One single centered sprite of {piece['subject']}. "
        "Full body or whole object, lots of empty space around it. "
        "Flat solid magenta background exactly #FF00FF, no shadow, no floor, no scenery."
    )


def knock_out_magenta(data: bytes) -> bytes:
    from PIL import Image

    image = Image.open(io.BytesIO(data)).convert("RGBA")
    pixels = [
        (red, green, blue, 0 if red > 190 and blue > 190 and green < 140 else alpha)
        for red, green, blue, alpha in image.getdata()
    ]
    image.putdata(pixels)
    output = io.BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


async def _one_image(client: httpx.AsyncClient, api_key: str, piece: dict[str, str]) -> tuple[str, bytes] | None:
    response = await client.post(
        "https://api.x.ai/v1/images/generations",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": IMAGE_MODEL,
            "prompt": _prompt(piece),
            "aspect_ratio": "16:9" if piece["kind"] == "background" else "1:1",
            "resolution": "1k",
            "quality": "low",
            "response_format": "b64_json",
            "n": 1,
        },
    )
    response.raise_for_status()
    item = response.json()["data"][0]
    if item.get("b64_json"):
        data = base64.b64decode(item["b64_json"])
    else:
        download = await client.get(item["url"])
        download.raise_for_status()
        data = download.content
    if piece["kind"] == "sprite":
        data = knock_out_magenta(data)
    return piece["id"], data


async def render_pieces(api_key: str, pieces: list[dict[str, str]]) -> dict[str, bytes]:
    images: dict[str, bytes] = {}
    async with httpx.AsyncClient(timeout=120) as client:
        tasks = [_one_image(client, api_key, piece) for piece in pieces]
        for result in await _gather(tasks):
            if isinstance(result, tuple):
                images[result[0]] = result[1]
    return images


async def _gather(tasks: list[Any]) -> list[Any]:
    import asyncio

    return await asyncio.gather(*tasks, return_exceptions=True)


def save_art(game_dir: Path, images: dict[str, bytes]) -> None:
    art_dir = game_dir / "art"
    art_dir.mkdir(exist_ok=True)
    for name, data in images.items():
        (art_dir / f"{name}.png").write_bytes(data)
