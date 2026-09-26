from fastapi.testclient import TestClient

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


def test_generated_code_checks() -> None:
    good = 'import { start } from "/static/kit.js";\n' + "start({ init(g) {} });\n" * 20
    assert main.check_game_js(good) == []
    assert main.check_game_js('import x from "lodash";\nstart({});') != []
    assert any("kit.js" in problem for problem in main.check_game_js("start({});" * 40))


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
