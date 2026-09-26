import json

from fastapi.testclient import TestClient

from app import art

import app.main as main


app = main.app


def test_health() -> None:
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}


def test_demo_prompt_generates_playable_package(monkeypatch) -> None:
    monkeypatch.setattr(main, "XAI_API_KEY", None)
    with TestClient(app) as client:
        response = client.post("/api/games/generate", data={"prompt": "connect four"})
        assert response.status_code == 202
        game_id = response.json()["game_id"]

        status = client.get(f"/api/games/{game_id}/status").json()
        assert status["status"] == "ready"
        assert status["version"] == 1
        page = client.get(f"/games/{game_id}/")
        assert page.status_code == 200
        assert "/static/kit.js" in page.text
        assert 'from "/static/kit.js"' in client.get(f"/games/{game_id}/game.js").text

        controller = client.get(f"/games/{game_id}/controller.json").json()
        assert controller["players"] == 2
        assert controller["player1"][0]["type"] == "choice"
        assert controller["player1"][0]["options"] == [str(n) for n in range(1, 8)]


def test_controls_are_normalized() -> None:
    controls = main.normalize_controls(
        {
            "player1": [
                {"type": "choice", "id": "Move!", "label": "<b>Attack</b>", "options": ["Tackle", 5, ""]},
                {"type": "laser", "id": "move", "color": "red"},
                {"type": "dpad", "id": "", "color": "#ff0000"},
            ],
            "player2": [],
        },
        2,
    )
    first, second, third = controls["player1"]
    assert first == {"type": "choice", "id": "move", "label": "b Attack /b", "options": ["Tackle", "5"]}
    assert second == {"type": "button", "id": "move2", "label": "A"}
    assert third == {"type": "dpad", "id": "move3", "label": "Move", "color": "#ff0000"}
    assert controls["player2"] == controls["player1"]

    assert main.normalize_controls({}, 1)["player1"] == main.DEFAULT_CONTROLS


def test_known_games_use_existing_sprites() -> None:
    assert art.known_helpers("a chess match") == ["chessPiece"]
    assert art.known_helpers("pokemon battle") == ["pokemon"]
    assert art.known_helpers("fishing in Ontario with my dad") == []

    chess = art.resolve_sprite_choice("SOURCE: original\nHELPERS:\nBRIEF:", "play chess")
    assert chess["source"] == "kit"
    assert "g.chessPiece" in chess["brief"]
    assert main.required_helpers(chess["brief"]) == ["chessPiece"]

    original = art.resolve_sprite_choice(
        "SOURCE: original\nHELPERS:\nBRIEF:",
        "reminisce about a fishing trip",
    )
    assert original == {"source": "original", "helpers": [], "brief": ""}

    looked_up = art.resolve_sprite_choice(
        "SOURCE: kit\nHELPERS: pokemon\nBRIEF: Pikachu is 25 and Charmander is 4.",
        "a creature battle in tall grass",
    )
    assert looked_up["source"] == "kit"
    assert looked_up["helpers"] == ["pokemon"]
    assert "Pikachu is 25" in looked_up["brief"]

    code = 'import { start } from "/static/kit.js";\n' + "start({ init(g) { g.chessPiece('wK'); } });\n" * 10
    package = {"game_js": code, "tests": [{}, {}, {}], "players": 1}
    assert any("g.pokemon" in problem for problem in main.check_package(package, helpers=["pokemon"]))
    assert main.check_package(package, helpers=["chessPiece"]) == []


def test_art_plan_always_has_a_background_and_a_sprite() -> None:
    pieces = art.normalize_pieces(
        {"pieces": [{"id": "Boat!", "kind": "sprite", "subject": "a wooden rowboat"}]},
        "fishing at dawn",
    )
    assert pieces[0]["kind"] == "background"
    assert any(piece["id"] == "boat" and piece["kind"] == "sprite" for piece in pieces)
    brief = art.art_brief("abc", pieces)
    assert "/games/abc/art/background.png" in brief
    assert "/games/abc/art/boat.png" in brief


def test_multiplayer_prompt_forces_two_controllers() -> None:
    assert main.wants_two_players("make me a multiplayer fishing game")
    assert main.wants_two_players("a 2-player race")
    assert not main.wants_two_players("fishing in Ontario with my dad")
    package = main.normalize_package(
        {
            "name": "Lake",
            "players": 1,
            "controls": {
                "player1": [{"type": "button", "id": "cast", "label": "Cast"}],
                "player2": [],
            },
            "game_js": "x" * 300,
            "tests": [],
        },
        2,
    )
    assert package["players"] == 2
    assert package["controls"]["players"] == 2
    assert package["controls"]["player1"][0]["id"] == "cast"
    assert package["controls"]["player2"][0]["id"] == "cast"


def test_generated_code_checks() -> None:
    good = 'import { start } from "/static/kit.js";\n' + "start({ init(g) {} });\n" * 20
    assert main.check_game_js(good) == []
    assert main.check_game_js('import x from "lodash";\nstart({});') != []
    assert any("kit.js" in problem for problem in main.check_game_js("start({});" * 40))
    package = main.normalize_package(
        {"name": "X", "players": 1, "controls": {}, "game_js": good, "tests": []}
    )
    assert any("tests" in problem for problem in main.check_package(package))


def test_research_and_catalog_files_exist() -> None:
    assert (main.WEB_DIR / "research-prompt.txt").exists()
    assert (main.WEB_DIR / "assets-catalog.txt").exists()
    assert (main.WEB_DIR / "examples" / "connect4.tests.json").exists()
    tests = main.normalize_tests(
        json.loads((main.WEB_DIR / "examples" / "connect4.tests.json").read_text(encoding="utf-8"))
    )
    assert len(tests) >= 3


def test_error_report_without_key_does_not_repair(monkeypatch) -> None:
    monkeypatch.setattr(main, "XAI_API_KEY", None)
    with TestClient(app) as client:
        game_id = client.post("/api/games/generate", data={"prompt": "x"}).json()["game_id"]
        result = client.post(
            f"/api/games/{game_id}/errors", json={"message": "boom", "version": 1}
        ).json()
        assert result["repairing"] is False


def test_asset_proxy_rejects_local_addresses() -> None:
    with TestClient(app) as client:
        for url in ("http://localhost:8000/health", "http://127.0.0.1/", "file:///etc/passwd"):
            assert client.get("/api/asset", params={"url": url}).status_code == 400


def test_controller_input_reaches_game_socket() -> None:
    with TestClient(app) as client:
        with client.websocket_connect("/ws/games/demo") as game:
            with client.websocket_connect("/ws/games/demo") as controller:
                event = {"type": "input", "player": 1, "control": "a", "kind": "press"}
                controller.send_json(event)
                assert game.receive_json() == event
