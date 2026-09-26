# Grow-a-Game

A laptop-based prototype of the full voice-to-game console:

1. Enter a game idea or upload an audio recording.
2. FastAPI transcribes audio and Grok writes a game on top of the runtime kit.
3. Open the game display in one browser tab.
4. Open Player 1 and Player 2 controllers in two other tabs or on phones.
5. Controller input reaches the game in real time over WebSockets.

Without an xAI key, text prompts load the hand-written Connect Four demo so the complete
controller flow can be tested immediately. With a key, audio uses Grok speech-to-text and Grok
writes a new game for any idea.

## How games are built

Grok writes the whole game as JavaScript, but on top of a tested runtime, `web/kit.js`. The kit
owns everything that used to break: the game loop, scaling a 1280x720 canvas to any screen,
controller input, HUD, game over and restart, timers, and loading online sprites (Pokémon via
PokeAPI, playing cards, Twemoji, or any image URL) through the caching proxy at `/api/asset`.
Failed images fall back to an emoji so a bad URL never breaks a game. The instructions Grok
receives are in `web/game-generator-prompt.txt`, plus `web/examples/connect4.js` as a reference.

Each game designs its own phone controller from `dpad`, `joystick`, `button`, and `choice` (a grid
of labeled options such as Connect Four columns or battle moves), and can swap layouts mid-game
with `g.setControls`, e.g. a dpad for walking and a move list in battle.

When a game crashes, the kit reports the error (with line number) to
`POST /api/games/{game_id}/errors`; the server sends the code and error back to Grok, saves the
fix as a new version, and the game page reloads itself. Each game gets up to three repairs.

Keyboard controls on the game page are handy for testing without phones: Player 1 uses WASD,
Space/E/Q/R for buttons, and 1-9 for choices; Player 2 uses the arrows, Enter/Right Shift, and
the numpad.

## Run it

Python 3.11 or newer is required.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
npm install
npm run build
uvicorn app.main:app --reload
```

Open [http://localhost:8000](http://localhost:8000).

To use voice and AI generation:

```bash
cp .env.example .env
```

Then place your xAI API key in `.env` and restart the server. Never commit `.env`.

For account creation and sign-in, create a Supabase project and copy its Project URL and public
anon key from **Project Settings → API** into `VITE_SUPABASE_URL` and
`VITE_SUPABASE_ANON_KEY`. Re-run `npm run build` after changing either frontend setting.

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
- `POST /api/games/{game_id}/errors` — runtime error report that triggers an automatic repair
- `GET /api/asset?url=...` — cached proxy for online images and sounds
- `/games/{game_id}/controller.json` — the game's initial phone controller layout
- `/docs` — interactive FastAPI documentation

Generated game packages are stored under `games/<game_id>/` and intentionally ignored by Git.
