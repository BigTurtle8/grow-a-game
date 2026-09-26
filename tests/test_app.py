from fastapi.testclient import TestClient

import app.main as main


app = main.app


def test_health() -> None:
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}


def test_prompt_generates_playable_package(monkeypatch) -> None:
    monkeypatch.setattr(main, "XAI_API_KEY", None)
    with TestClient(app) as client:
        response = client.post(
            "/api/games/generate",
            data={"prompt": "Make a two-player 3D fishing game"},
        )
        assert response.status_code == 202
        game_id = response.json()["game_id"]

        status = client.get(f"/api/games/{game_id}/status")
        assert status.status_code == 200
        assert status.json()["status"] == "ready"
        assert client.get(f"/games/{game_id}/").status_code == 200

        metadata = client.get(f"/games/{game_id}/metadata.json").json()
        controller = client.get(f"/games/{game_id}/controller.json").json()
        assert metadata["players"] == 2
        assert controller["players"] == 2
        assert controller["player1"][0]["type"] == "joystick"
        assert controller["player2"][1]["action"] == "action"


def test_controller_input_reaches_game_socket() -> None:
    with TestClient(app) as client:
        with client.websocket_connect("/ws/games/demo") as game:
            with client.websocket_connect("/ws/games/demo") as controller:
                event = {
                    "type": "input",
                    "player": 1,
                    "action": "move",
                    "value": {"x": 1, "y": 0},
                }
                controller.send_json(event)
                assert game.receive_json() == event

