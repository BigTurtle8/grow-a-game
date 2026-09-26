# Grow-a-Game

A laptop-based prototype of the full voice-to-game console:

1. Enter a game idea or upload an audio recording.
2. FastAPI transcribes audio and generates a Three.js game.
3. Open the game display in one browser tab.
4. Open Player 1 and Player 2 controllers in two other tabs or on phones.
5. Controller input reaches the game in real time over WebSockets.

Without an xAI key, text prompts generate a local demo game so the complete controller flow
can be tested immediately. With a key, audio uses Grok speech-to-text and prompts use Grok to
generate a new game.

## Run it

Python 3.11 or newer is required.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

Open [http://localhost:8000](http://localhost:8000).

To use voice and AI generation:

```bash
cp .env.example .env
```

Then place your xAI API key in `.env` and restart the server. Never commit `.env`.

## Two-phone controller demo

Start the server so other devices on your Wi-Fi can reach it:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Find the laptop's local IP address, then open `http://YOUR_LAPTOP_IP:8000` on each phone. Generate
a two-player game and use the Player 1 and Player 2 links. All three pages must point to the same
laptop address; `localhost` on a phone refers to the phone itself.

## API

- `POST /api/games/generate` — multipart form with `prompt`, `audio`, or both
- `GET /api/games/{game_id}/status` — poll generation status and receive launch URLs
- `WS /ws/games/{game_id}` — game/controller input channel
- `/games/{game_id}/` — generated playable game
- `/games/{game_id}/controller.json` — dynamic controller definition
- `/docs` — interactive FastAPI documentation

Generated game packages are stored under `games/<game_id>/` and intentionally ignored by Git.
