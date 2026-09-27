from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
GAMES_DIR = ROOT / "games"
DEVICE_PORTS = (18080, 18081, 18082, 18083, 8081, 8000)
_last_sent: dict[str, float] = {}


def find_adb() -> Path | None:
    configured = os.getenv("ADB")
    if configured and Path(configured).exists():
        return Path(configured)
    on_path = shutil.which("adb")
    if on_path:
        return Path(on_path)
    names = ("adb.exe", "adb") if os.name == "nt" else ("adb",)
    roots = [
        Path(os.getenv("ANDROID_HOME", "")),
        Path(os.getenv("ANDROID_SDK_ROOT", "")),
        Path(os.getenv("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages",
        Path(os.getenv("LOCALAPPDATA", "")) / "Android" / "Sdk",
    ]
    for root in roots:
        if not root.exists():
            continue
        for name in names:
            exact = root / "platform-tools" / name
            if exact.exists():
                return exact
            matches = list(root.glob(f"Google.PlatformTools*/platform-tools/{name}"))
            if matches:
                return matches[0]
    return None


def host_port() -> int:
    parsed = urlparse(os.getenv("SELF_URL", f"http://127.0.0.1:{os.getenv('PORT', '8000')}"))
    if parsed.port:
        return parsed.port
    return 443 if parsed.scheme == "https" else 80


def run_adb(*args: str, serial: str | None = None, timeout: float = 8) -> subprocess.CompletedProcess[str]:
    adb = find_adb()
    if adb is None:
        raise FileNotFoundError("adb was not found. Install Android platform-tools.")
    command = [str(adb)]
    if serial:
        command.extend(["-s", serial])
    command.extend(args)
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)


def list_devices() -> list[str]:
    if find_adb() is None:
        return []
    try:
        result = run_adb("devices")
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []
    devices: list[str] = []
    for line in result.stdout.splitlines():
        match = re.match(r"^(\S+)\s+device(?:\s|$)", line.strip())
        if match:
            devices.append(match.group(1))
    return devices


def ensure_reverse(serial: str, laptop_port: int | None = None) -> int:
    laptop_port = laptop_port or host_port()
    listed = run_adb("reverse", "--list", serial=serial)
    existing = _parse_reverse(listed.stdout, laptop_port)
    if existing:
        return existing
    for port in DEVICE_PORTS:
        result = run_adb("reverse", f"tcp:{port}", f"tcp:{laptop_port}", serial=serial)
        if result.returncode == 0:
            return port
    detail = (listed.stderr or listed.stdout or result.stderr or result.stdout).strip()
    raise RuntimeError(detail or f"Could not forward a port from {serial} to the laptop.")


def controller_url(game_id: str, player: int, device_port: int) -> str:
    return f"http://127.0.0.1:{device_port}/controller?game={game_id}&player={player}"


def open_command(url: str) -> str:
    return (
        "am start -a android.intent.action.VIEW "
        "-c android.intent.category.BROWSABLE "
        f"-d {shlex.quote(url)}"
    )


def open_controller(serial: str, game_id: str, player: int, device_port: int) -> str:
    url = controller_url(game_id, player, device_port)
    result = run_adb("shell", open_command(url), serial=serial)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(detail or f"The phone {serial} did not open the controller.")
    return url


def send_controllers(game_id: str, players: int | None = None) -> dict[str, Any]:
    players = max(1, min(2, int(players if players is not None else _player_count(game_id))))
    now = time.monotonic()
    if now - _last_sent.get(game_id, 0) < 20:
        return {"game_id": game_id, "players": players, "sent": [], "skipped": [], "devices": list_devices()}

    devices = list_devices()
    sent: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for player, serial in enumerate(devices[:players], start=1):
        try:
            port = ensure_reverse(serial)
            url = open_controller(serial, game_id, player, port)
            sent.append({"serial": serial, "player": player, "url": url})
        except Exception as exc:
            skipped.append({"serial": serial, "player": player, "error": str(exc)})
    for player in range(len(devices) + 1, players + 1):
        skipped.append({"player": player, "error": "No phone connected for this player."})
    if sent:
        _last_sent[game_id] = now
    return {"game_id": game_id, "players": players, "sent": sent, "skipped": skipped, "devices": devices}


def reset_send_cache() -> None:
    _last_sent.clear()


def _player_count(game_id: str) -> int:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", game_id or ""):
        return 1
    controller = GAMES_DIR / game_id / "controller.json"
    if controller.exists():
        try:
            data = json.loads(controller.read_text(encoding="utf-8-sig"))
            return max(1, min(2, int(data.get("players") or 1)))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
    metadata = GAMES_DIR / game_id / "metadata.json"
    if metadata.exists():
        try:
            data = json.loads(metadata.read_text(encoding="utf-8-sig"))
            return max(1, min(2, int(data.get("players") or 1)))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
    return 1


def _parse_reverse(output: str, laptop_port: int) -> int | None:
    for match in re.finditer(r"tcp:(\d+)\s+tcp:(\d+)", output or ""):
        device_port, remote_port = int(match.group(1)), int(match.group(2))
        if remote_port == laptop_port:
            return device_port
    return None
