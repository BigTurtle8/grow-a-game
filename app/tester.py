"""Headless browser test harness for generated games.

Each game is loaded with ?test=1 so the kit exposes window.__kit: time only advances when the
harness steps it, randomness is seeded, and errors are recorded. The harness runs a fixed
battery of robustness checks plus the scenario tests the AI wrote for its own game, and returns
plain-language problems that can be fed back to the AI for fixing.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / ".cache" / "ms-playwright"))

from playwright.async_api import Browser, Page, async_playwright  # noqa: E402

LOAD_TIMEOUT_MS = 15_000
ASSET_WAIT_SECONDS = 3
FUZZ_ITERATIONS = 180
IDLE_SECONDS = 8
SLOW_FRAME_MS = 16

_playwright = None
_browser: Browser | None = None
_browser_lock = asyncio.Lock()


@dataclass
class TestReport:
    problems: list[str] = field(default_factory=list)
    checks: list[dict[str, Any]] = field(default_factory=list)
    screenshot: bytes | None = None

    @property
    def passed(self) -> bool:
        return not self.problems

    def record(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append({"name": name, "ok": ok, "detail": detail})
        if not ok:
            self.problems.append(f"{name}: {detail}" if detail else name)

    def summary(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "checks_passed": sum(1 for check in self.checks if check["ok"]),
            "checks_total": len(self.checks),
            "checks": self.checks,
        }


async def _get_browser() -> Browser:
    global _playwright, _browser
    async with _browser_lock:
        if _browser is None or not _browser.is_connected():
            _playwright = _playwright or await async_playwright().start()
            try:
                _browser = await _playwright.chromium.launch(
                    args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"]
                )
            except Exception as exc:
                raise RuntimeError(
                    "Playwright Chromium is not installed. From the project folder run: "
                    "python -m playwright install chromium"
                ) from exc
        return _browser


async def close_browser() -> None:
    global _playwright, _browser
    if _browser is not None:
        await _browser.close()
    if _playwright is not None:
        await _playwright.stop()
    _browser = _playwright = None


def _format_error(error: dict[str, Any]) -> str:
    location = f" (game.js line {error['line']})" if error.get("line") else ""
    stack = str(error.get("stack", "")).strip().splitlines()[:4]
    where = error.get("where", "running")
    return f"{error.get('message', 'Unknown error')}{location} while {where}" + (
        "\n    " + "\n    ".join(stack) if stack else ""
    )


async def _errors_since(page: Page, start: int) -> list[str]:
    errors = await page.evaluate("(start) => window.__kit.errors.slice(start)", start)
    return [_format_error(error) for error in errors]


async def _fresh_round(page: Page) -> None:
    await page.evaluate("() => { window.__kit.restart(); window.__kit.step(0.1); }")


async def test_game(base_url: str, game_id: str, tests: list[dict[str, Any]] | None = None) -> TestReport:
    report = TestReport()
    browser = await _get_browser()
    context = await browser.new_context(viewport={"width": 1280, "height": 720})
    page = await context.new_page()
    page_errors: list[str] = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    page.on(
        "console",
        lambda message: page_errors.append(message.text)
        if message.type == "error" and "[kit]" not in message.text and "404" not in message.text
        else None,
    )
    try:
        await _run_battery(page, report, base_url, game_id, tests or [], page_errors)
    except Exception as exc:  # the harness itself must never crash the pipeline
        report.record("Test harness", False, f"Could not finish testing: {exc}")
    finally:
        await context.close()
    return report


async def _run_battery(
    page: Page,
    report: TestReport,
    base_url: str,
    game_id: str,
    tests: list[dict[str, Any]],
    page_errors: list[str],
) -> None:
    await page.goto(f"{base_url}/games/{game_id}/?test=1&seed=7", wait_until="load")
    try:
        await page.wait_for_function(
            "() => window.__kit && (window.__kit.ready || window.__kit.errors.length)",
            timeout=LOAD_TIMEOUT_MS,
        )
    except Exception:
        detail = "; ".join(page_errors[:3]) or "start() never finished (is game.js calling start?)"
        report.record("Game loads", False, detail)
        return
    errors = await _errors_since(page, 0)
    ready = await page.evaluate("() => window.__kit.ready")
    report.record("Game loads", ready and not errors, "; ".join(errors or page_errors[:3]))
    if not ready or errors:
        return

    # First frames render something visible.
    count = await page.evaluate("() => window.__kit.errors.length")
    await page.evaluate("() => window.__kit.step(0.5)")
    errors = await _errors_since(page, count)
    colors = await page.evaluate("() => window.__kit.distinctColors()")
    report.record("First frames run", not errors, "; ".join(errors))
    if errors:
        return
    if colors >= 0:
        report.record(
            "Screen shows the game",
            colors >= 4,
            f"the canvas is almost blank ({colors} colors); draw() must draw the whole game",
        )

    # Controller layouts are usable.
    layouts = await page.evaluate("() => window.__kit.layouts()")
    players = await page.evaluate("() => window.__kit.players()")
    for player in range(1, players + 1):
        controls = layouts.get(str(player)) or layouts.get(player) or []
        report.record(
            f"Player {player} has controls",
            bool(controls),
            "the phone controller is empty at the start of the game",
        )

    # Online images resolve.
    for _ in range(ASSET_WAIT_SECONDS * 4):
        assets = await page.evaluate("() => window.__kit.assets()")
        if all(asset["ready"] for asset in assets):
            break
        await asyncio.sleep(0.25)
    failed = sorted({asset["url"] for asset in assets if asset["failed"] or not asset["ready"]})
    report.record(
        "Images load",
        not failed,
        "these image URLs failed, replace them with verified sources or draw instead: "
        + ", ".join(failed[:8]),
    )

    # Idle: timers, computer turns, spawns, and clocks must run for a long time without errors.
    count = await page.evaluate("() => window.__kit.errors.length")
    await page.evaluate(f"() => window.__kit.step({IDLE_SECONDS}, 30)")
    errors = await _errors_since(page, count)
    report.record(f"Runs {IDLE_SECONDS}s with no input", not errors, "; ".join(errors[:2]))

    # Frame cost with drawing every frame.
    await _fresh_round(page)
    frame_ms = await page.evaluate(
        "() => { const t = performance.now(); window.__kit.step(1, 1); return (performance.now() - t) / 60; }"
    )
    report.record(
        "Runs smoothly",
        frame_ms < SLOW_FRAME_MS,
        f"update+draw takes {frame_ms:.1f}ms per frame; must be under {SLOW_FRAME_MS}ms",
    )

    # Scenario tests written for this game.
    for index, scenario in enumerate(tests[:20]):
        await _run_scenario(page, report, index, scenario)

    # Random play, including stale and invalid controller messages.
    await _fresh_round(page)
    idle_rate = await page.evaluate("() => window.__kit.idleChangeRate(30)")
    await _fresh_round(page)
    result = await page.evaluate(f"() => window.__kit.fuzz({FUZZ_ITERATIONS})")
    if result["errors"]:
        trail = json.dumps(result["trail"][-8:])
        report.record(
            "Random controller input",
            False,
            f"crashed after {result['run']} random inputs: {_format_error(result['errors'][0])}\n"
            f"    last controller messages: {trail}",
        )
    else:
        report.record("Random controller input", True, f"{result['run']} inputs")
        if idle_rate < 0.5:
            report.record(
                "Controls do something",
                result["changed"] > 0,
                "hundreds of controller inputs never changed g.state; check control ids and "
                "event.kind in onInput",
            )

    # Restarting repeatedly must always produce a working game.
    count = await page.evaluate("() => window.__kit.errors.length")
    await page.evaluate("() => { for (let i = 0; i < 3; i++) { window.__kit.restart(); window.__kit.step(0.3); } }")
    errors = await _errors_since(page, count)
    report.record("Restarts cleanly", not errors, "; ".join(errors[:2]))

    await _fresh_round(page)
    await page.evaluate("() => { window.__kit.fuzz(40); window.__kit.step(0.2); }")
    report.screenshot = await page.screenshot(type="png")
    if page_errors:
        report.record("No console errors", False, "; ".join(dict.fromkeys(page_errors))[:600])


async def _run_scenario(page: Page, report: TestReport, index: int, scenario: dict[str, Any]) -> None:
    name = str(scenario.get("name") or f"Scenario {index + 1}")[:80]
    steps = scenario.get("steps") if isinstance(scenario.get("steps"), list) else []
    await _fresh_round(page)
    count = await page.evaluate("() => window.__kit.errors.length")
    for number, step in enumerate(steps[:120], start=1):
        action = step.get("do")
        label = step.get("description") or action
        try:
            if action == "wait":
                await page.evaluate("(s) => window.__kit.step(s, 10)", max(0.0, min(float(step.get("seconds") or 0), 60)))
            elif action == "expect":
                expression = str(step.get("expression") or "true")
                ok = await page.evaluate("(e) => window.__kit.check(e)", expression)
                if not ok:
                    state = await page.evaluate("() => JSON.stringify(window.__kit.snapshot()).slice(0, 1500)")
                    text = await page.evaluate("() => window.__kit.text()")
                    report.record(
                        f'Scenario "{name}"',
                        False,
                        f"step {number} ({label}) expected `{expression}` to be true but it was false.\n"
                        f"    screen: {json.dumps(text)}\n    g.state: {state}",
                    )
                    return
            elif action == "restart":
                await _fresh_round(page)
            elif action == "tap":
                for kind in ("press", "release"):
                    await page.evaluate("(m) => window.__kit.send(m)", _scenario_message({**step, "do": kind}))
                    await page.evaluate("() => window.__kit.step(1 / 60)")
            else:
                await page.evaluate("(m) => window.__kit.send(m)", _scenario_message(step))
                await page.evaluate("() => window.__kit.step(1 / 60)")
        except Exception as exc:
            report.record(f'Scenario "{name}"', False, f"step {number} ({label}) threw: {exc}")
            return
        errors = await _errors_since(page, count)
        if errors:
            report.record(f'Scenario "{name}"', False, f"step {number} ({label}) crashed the game: {errors[0]}")
            return
    report.record(f'Scenario "{name}"', True, f"{len(steps)} steps")


async def _main(game_ids: list[str], base_url: str) -> None:
    for game_id in game_ids:
        tests_path = ROOT / "games" / game_id / "tests.json"
        tests = json.loads(tests_path.read_text(encoding="utf-8-sig")) if tests_path.exists() else []
        summary = (await test_game(base_url, game_id, tests)).summary()
        print(f"== {game_id}: {summary['checks_passed']}/{summary['checks_total']} checks passed")
        for check in summary["checks"]:
            detail = check["detail"].replace("\n", " | ")[:400]
            print(f"  {'OK  ' if check['ok'] else 'FAIL'} {check['name']} - {detail}")
    await close_browser()


if __name__ == "__main__":
    import sys

    asyncio.run(_main(sys.argv[1:], os.getenv("SELF_URL", "http://127.0.0.1:8000")))


def _scenario_message(step: dict[str, Any]) -> dict[str, Any]:
    action = step.get("do")
    message: dict[str, Any] = {"player": int(step.get("player") or 1), "control": str(step.get("control") or "")}
    if action == "select":
        index = int(step.get("index") or 0)
        message.update(kind="select", value=index, option=str(step.get("option") or ""))
    elif action == "move":
        message.update(kind="move", value={"x": float(step.get("x") or 0), "y": float(step.get("y") or 0)})
    elif action in ("press", "release"):
        message["kind"] = action
        message["value"] = action == "press"
        direction = step.get("direction")
        if direction:
            message["direction"] = direction
            message.pop("value")
    return message
