// Grow-a-Game runtime kit. Generated games are ES modules that import { start } from here.
// The kit owns the loop, scaling, controllers, HUD, restarts, assets, and error recovery so
// generated code only has to describe the game itself.

const gameId = location.pathname.split("/").filter(Boolean).at(-1);
const AUTO_RESTART_SECONDS = 12;
const EMOJI_FONT = '"Apple Color Emoji","Segoe UI Emoji","Noto Color Emoji",sans-serif';
const KEYBOARD = {
  1: {
    directions: { KeyW: "up", KeyS: "down", KeyA: "left", KeyD: "right" },
    buttons: ["Space", "KeyE", "KeyQ", "KeyR", "KeyF", "KeyZ"],
    choicePrefix: "Digit",
  },
  2: {
    directions: { ArrowUp: "up", ArrowDown: "down", ArrowLeft: "left", ArrowRight: "right" },
    buttons: ["Enter", "ShiftRight", "Slash", "Period", "Comma", "Quote"],
    choicePrefix: "Numpad",
  },
};
const DIRECTION_VECTORS = {
  up: { x: 0, y: -1 },
  down: { x: 0, y: 1 },
  left: { x: -1, y: 0 },
  right: { x: 1, y: 0 },
};

let definition = null;
let g = null;
let THREE = null;
let canvas = null;
let context = null;
let renderer = null;
let playerCount = 1;
let version = 1;
let phase = "loading";
let overTime = 0;
let lastFrame = 0;
let socket = null;
let errorReported = false;
let restartAfterBug = false;
let defaultLayouts = {};
let layouts = {};
let tells = {};
let timers = [];
let view = { scale: 1, x: 0, y: 0 };
const held = new Map();
const axes = new Map();
const keyboardAxes = new Map();
const spriteCache = new Map();
const dom = {};

// ---------- boot ----------

export async function boot() {
  addEventListener("error", (event) => {
    if (event.target !== window) return;
    const file = event.filename || "";
    if (file && !file.includes("/games/") && !file.includes("/static/kit.js")) return;
    fail({
      message: event.message || String(event.error),
      stack: event.error?.stack ?? "",
      file,
      line: event.lineno,
      column: event.colno,
      where: phase === "loading" ? "loading" : "running",
    });
  });
  addEventListener("unhandledrejection", (event) => fail(errorInfo(event.reason, "running")));
  buildDom();

  const [metadata, controller] = await Promise.all([
    fetchJson("./metadata.json"),
    fetchJson("./controller.json"),
  ]);
  version = metadata?.version ?? 1;
  playerCount = Math.max(1, Math.min(2, Number(controller?.players ?? metadata?.players ?? 1)));
  for (let player = 1; player <= playerCount; player++) {
    defaultLayouts[player] = controller?.[`player${player}`] ?? [];
  }
  layouts = structuredClone(defaultLayouts);
  connect();
  addEventListener("keydown", (event) => onKey(event, true));
  addEventListener("keyup", (event) => onKey(event, false));

  const script = document.createElement("script");
  script.type = "module";
  script.src = `./game.js?v=${version}`;
  script.addEventListener("load", () => {
    setTimeout(() => {
      if (!definition) fail({ message: "game.js loaded but never called start({...}).", where: "loading" });
    }, 100);
  });
  script.addEventListener("error", () => fail({ message: "game.js could not be loaded.", where: "loading" }));
  document.body.append(script);
}

/** Entry point for generated games. */
export function start(game) {
  if (definition) return;
  definition = game ?? {};
  setup().catch((error) => fail(errorInfo(error, "starting")));
}

async function setup() {
  if (definition.mode === "3d") {
    THREE = await import("three");
    renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    dom.stage.append(renderer.domElement);
  } else {
    canvas = document.createElement("canvas");
    context = canvas.getContext("2d");
    dom.stage.append(canvas);
  }
  addEventListener("resize", resize);
  resize();
  restart();
  lastFrame = performance.now();
  requestAnimationFrame(frame);
}

function restart() {
  timers = [];
  held.clear();
  overTime = 0;
  phase = "playing";
  restartAfterBug = false;
  layouts = structuredClone(defaultLayouts);
  tells = {};
  hideOverlay();
  const keep = g?.keep ?? {};
  g = createApi(keep);
  dom.scores.replaceChildren();
  dom.status.textContent = "";
  resize();
  try {
    definition.init?.(g);
  } catch (error) {
    fail(errorInfo(error, "init"));
    return;
  }
  for (let player = 1; player <= playerCount; player++) sendLayout(player);
}

function frame(now) {
  requestAnimationFrame(frame);
  const dt = Math.min((now - lastFrame) / 1000, 0.05);
  lastFrame = now;
  if (phase === "broken") return;
  try {
    if (phase === "playing") {
      g.time += dt;
      runTimers(dt);
      if (phase === "playing") definition.update?.(g, dt);
    } else if (phase === "over") {
      overTime += dt;
      dom.overlayHint.textContent = restartHint();
      if (overTime >= AUTO_RESTART_SECONDS) {
        restart();
        return;
      }
    }
    render();
  } catch (error) {
    fail(errorInfo(error, phase === "playing" ? "update/draw" : phase));
  }
}

function render() {
  if (context) {
    const ratio = Math.min(devicePixelRatio, 2);
    context.setTransform(1, 0, 0, 1, 0, 0);
    context.fillStyle = "#000";
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.setTransform(view.scale * ratio, 0, 0, view.scale * ratio, view.x * ratio, view.y * ratio);
    context.beginPath();
    context.rect(0, 0, g.width, g.height);
    context.clip();
    context.fillStyle = definition.background ?? "#11120f";
    context.fillRect(0, 0, g.width, g.height);
    context.imageSmoothingEnabled = true;
    context.save();
    definition.draw?.(g, context);
    context.restore();
  } else if (renderer) {
    definition.draw?.(g);
    renderer.render(g.scene, g.camera);
  }
}

function resize() {
  const width = innerWidth;
  const height = innerHeight;
  if (canvas) {
    const ratio = Math.min(devicePixelRatio, 2);
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    const logicalWidth = definition?.width ?? 1280;
    const logicalHeight = definition?.height ?? 720;
    const scale = Math.min(width / logicalWidth, height / logicalHeight);
    view = {
      scale,
      x: (width - logicalWidth * scale) / 2,
      y: (height - logicalHeight * scale) / 2,
    };
  }
  if (renderer) {
    renderer.setSize(width, height);
    if (g?.camera?.isPerspectiveCamera) {
      g.camera.aspect = width / height;
      g.camera.updateProjectionMatrix();
    }
  }
}

// ---------- API passed to game callbacks ----------

function createApi(keep) {
  const api = {
    width: definition.width ?? 1280,
    height: definition.height ?? 720,
    players: playerCount,
    state: {},
    keep,
    time: 0,
    ctx: context,

    input: {
      held: (player, control, direction) =>
        Boolean(held.get(inputKey(player, control, direction))),
      axis: (player, control) => axes.get(inputKey(player, control)) ?? { x: 0, y: 0 },
    },

    setControls(player, controls) {
      layouts[player] = Array.isArray(controls) ? controls.filter(Boolean) : [];
      sendLayout(player);
    },
    tell(player, text) {
      tells[player] = String(text ?? "");
      send({ type: "status", player, text: tells[player] });
    },
    setStatus(text) {
      dom.status.textContent = String(text ?? "");
    },
    setScore(player, value, label) {
      let chip = dom.scores.querySelector(`[data-player="${player}"]`);
      if (!chip) {
        chip = document.createElement("div");
        chip.className = `kit-score p${player}`;
        chip.dataset.player = String(player);
        chip.innerHTML = "<b></b><span></span>";
        dom.scores.append(chip);
      }
      chip.querySelector("b").textContent = label ?? `P${player}`;
      chip.querySelector("span").textContent = String(value);
    },
    message(text, seconds = 1.6) {
      dom.toast.textContent = String(text);
      dom.toast.classList.remove("hidden");
      clearTimeout(dom.toastTimer);
      dom.toastTimer = setTimeout(() => dom.toast.classList.add("hidden"), seconds * 1000);
    },
    gameOver(title, subtitle = "") {
      if (phase !== "playing") return;
      phase = "over";
      overTime = 0;
      showOverlay(title, subtitle, restartHint());
      for (let player = 1; player <= playerCount; player++) {
        send({ type: "status", player, text: "Game over · press any button to play again" });
      }
    },
    after(seconds, callback) {
      const timer = { remaining: seconds, callback, repeat: 0 };
      timers.push(timer);
      return () => (timer.cancelled = true);
    },
    every(seconds, callback) {
      const timer = { remaining: seconds, callback, repeat: seconds };
      timers.push(timer);
      return () => (timer.cancelled = true);
    },

    image: loadSprite,
    emoji: (character, size) => loadSprite(twemojiUrl(character), character, size),
    pokemon(id, { back = false, shiny = false } = {}) {
      const path = `${back ? "back/" : ""}${shiny ? "shiny/" : ""}${Number(id)}`;
      return loadSprite(
        `https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/${path}.png`,
        "🐾",
      );
    },
    card(code) {
      const clean = String(code).toUpperCase().replace("10", "0");
      return loadSprite(`https://deckofcardsapi.com/static/img/${clean}.png`, clean);
    },
    cardBack: () => loadSprite("https://deckofcardsapi.com/static/img/back.png", "🂠"),

    drawSprite(sprite, x, y, width, height, options = {}) {
      const target = context;
      if (!target || !sprite) return;
      const w = width ?? sprite.width;
      const h = height ?? sprite.height;
      const left = options.center ? x - w / 2 : x;
      const top = options.center ? y - h / 2 : y;
      if (!sprite.ready) {
        target.fillStyle = "rgba(255,255,255,0.08)";
        target.fillRect(left, top, w, h);
        return;
      }
      target.save();
      if (options.alpha !== undefined) target.globalAlpha = options.alpha;
      target.imageSmoothingEnabled = options.smooth ?? !(w > sprite.width * 1.5);
      if (options.flip || options.rotate) {
        target.translate(left + w / 2, top + h / 2);
        if (options.rotate) target.rotate(options.rotate);
        if (options.flip) target.scale(-1, 1);
        target.drawImage(sprite.image, -w / 2, -h / 2, w, h);
      } else {
        target.drawImage(sprite.image, left, top, w, h);
      }
      target.restore();
    },
    text(value, x, y, options = {}) {
      const target = context;
      if (!target) return;
      const size = options.size ?? 28;
      target.save();
      target.font = `${options.weight ?? 600} ${size}px ${options.font ?? '"Space Grotesk", sans-serif'}`;
      target.textAlign = options.align ?? "center";
      target.textBaseline = options.baseline ?? "middle";
      if (options.stroke) {
        target.lineWidth = options.strokeWidth ?? 4;
        target.strokeStyle = options.stroke;
        target.strokeText(String(value), x, y, options.maxWidth);
      }
      target.fillStyle = options.color ?? "#ffffff";
      target.fillText(String(value), x, y, options.maxWidth);
      target.restore();
    },
    rect(x, y, width, height, color, options = {}) {
      const target = context;
      if (!target) return;
      target.beginPath();
      if (options.radius) target.roundRect(x, y, width, height, options.radius);
      else target.rect(x, y, width, height);
      paint(target, color, options);
    },
    circle(x, y, radius, color, options = {}) {
      const target = context;
      if (!target) return;
      target.beginPath();
      target.arc(x, y, Math.max(0, radius), 0, Math.PI * 2);
      paint(target, color, options);
    },
    line(x1, y1, x2, y2, color = "#fff", width = 2) {
      const target = context;
      if (!target) return;
      target.beginPath();
      target.moveTo(x1, y1);
      target.lineTo(x2, y2);
      target.strokeStyle = color;
      target.lineWidth = width;
      target.stroke();
    },

    rand: (min = 0, max = 1) => min + Math.random() * (max - min),
    randInt: (min, max) => Math.floor(min + Math.random() * (max - min + 1)),
    pick: (items) => items[Math.floor(Math.random() * items.length)],
    shuffle(items) {
      const copy = [...items];
      for (let index = copy.length - 1; index > 0; index--) {
        const other = Math.floor(Math.random() * (index + 1));
        [copy[index], copy[other]] = [copy[other], copy[index]];
      }
      return copy;
    },
    clamp: (value, min, max) => Math.max(min, Math.min(max, value)),
    beep,
  };

  if (THREE) {
    api.THREE = THREE;
    api.renderer = renderer;
    api.scene = new THREE.Scene();
    api.camera = new THREE.PerspectiveCamera(60, innerWidth / innerHeight, 0.1, 1000);
    api.camera.position.set(0, 10, 14);
    api.camera.lookAt(0, 0, 0);
    api.texture = (sprite) => textureFor(sprite);
  }
  return api;
}

function paint(target, color, options) {
  if (color) {
    target.fillStyle = color;
    target.fill();
  }
  if (options.stroke) {
    target.strokeStyle = options.stroke;
    target.lineWidth = options.lineWidth ?? 2;
    target.stroke();
  }
}

function runTimers(dt) {
  for (const timer of [...timers]) {
    if (timer.cancelled) continue;
    timer.remaining -= dt;
    if (timer.remaining > 0) continue;
    if (timer.repeat > 0) timer.remaining += timer.repeat;
    else timer.cancelled = true;
    timer.callback();
    if (phase !== "playing") break;
  }
  timers = timers.filter((timer) => !timer.cancelled);
}

// ---------- input ----------

function inputKey(player, control, direction) {
  return direction ? `${player}:${control}:${direction}` : `${player}:${control}`;
}

function dispatch(event) {
  const { player, control, kind, direction } = event;
  if (kind === "press" || kind === "release") {
    const down = kind === "press";
    held.set(inputKey(player, control, direction), down);
    if (!direction) held.set(inputKey(player, control), down);
    else if (down) held.set(inputKey(player, control), true);
    else {
      const anyHeld = Object.keys(DIRECTION_VECTORS).some((name) => held.get(inputKey(player, control, name)));
      held.set(inputKey(player, control), anyHeld);
    }
  } else if (kind === "move") {
    axes.set(inputKey(player, control), event.value);
  }

  const wantsRestart = kind === "press" || kind === "select";
  if (phase === "over") {
    if (wantsRestart && overTime > 0.8) restart();
    return;
  }
  if (phase === "broken") {
    if (wantsRestart && restartAfterBug && definition) restart();
    return;
  }
  if (phase !== "playing") return;
  try {
    definition.onInput?.(g, event);
  } catch (error) {
    fail(errorInfo(error, `handling ${kind} on ${control}`));
  }
}

function handleMessage(message) {
  if (!message || typeof message !== "object") return;
  const player = Number(message.player);
  if (!(player >= 1 && player <= playerCount)) return;
  if (message.type === "hello") {
    sendLayout(player);
    return;
  }
  if (message.type !== "input") return;
  const control = String(message.control ?? message.action ?? "");
  const value = message.value;
  let kind = message.kind;
  if (!kind) kind = typeof value === "boolean" ? (value ? "press" : "release") : "move";
  const event = { player, control, kind };
  if (kind === "move") {
    event.value = { x: toAxis(value?.x), y: toAxis(value?.y) };
  } else if (kind === "select") {
    event.value = Number(value) || 0;
    event.option = String(message.option ?? "");
  } else if (message.direction && DIRECTION_VECTORS[message.direction]) {
    event.direction = message.direction;
    event.value = { ...DIRECTION_VECTORS[message.direction] };
  }
  dispatch(event);
}

function onKey(event, down) {
  if (event.target instanceof HTMLInputElement) return;
  for (let player = 1; player <= playerCount; player++) {
    const keys = KEYBOARD[player];
    const layout = layouts[player] ?? [];
    const direction = keys.directions[event.code];
    if (direction) {
      event.preventDefault();
      if (event.repeat) return;
      const mover = layout.find((control) => control.type === "dpad" || control.type === "joystick");
      if (!mover) return;
      if (mover.type === "dpad") {
        dispatch({
          player, control: mover.id, kind: down ? "press" : "release", direction,
          value: { ...DIRECTION_VECTORS[direction] },
        });
      } else {
        const pressed = keyboardAxes.get(player) ?? new Set();
        if (down) pressed.add(direction);
        else pressed.delete(direction);
        keyboardAxes.set(player, pressed);
        let x = 0;
        let y = 0;
        for (const name of pressed) {
          x += DIRECTION_VECTORS[name].x;
          y += DIRECTION_VECTORS[name].y;
        }
        const length = Math.hypot(x, y) || 1;
        dispatch({ player, control: mover.id, kind: "move", value: { x: x / length, y: y / length } });
      }
      return;
    }
    const buttonIndex = keys.buttons.indexOf(event.code);
    if (buttonIndex >= 0) {
      event.preventDefault();
      if (event.repeat) return;
      const button = layout.filter((control) => control.type === "button")[buttonIndex];
      if (button) dispatch({ player, control: button.id, kind: down ? "press" : "release" });
      else if (down && phase === "over") dispatch({ player, control: "", kind: "press" });
      return;
    }
    if (down && event.code.startsWith(keys.choicePrefix)) {
      const index = Number(event.code.slice(keys.choicePrefix.length)) - 1;
      const choice = layout.find((control) => control.type === "choice");
      if (choice && index >= 0 && index < (choice.options?.length ?? 0)) {
        event.preventDefault();
        dispatch({ player, control: choice.id, kind: "select", value: index, option: choice.options[index] });
        return;
      }
    }
  }
}

// ---------- networking ----------

function connect() {
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(`${protocol}://${location.host}/ws/games/${gameId}`);
  socket.addEventListener("open", () => {
    dom.connection.textContent = "Controllers online";
    for (let player = 1; player <= playerCount; player++) sendLayout(player);
  });
  socket.addEventListener("close", () => {
    dom.connection.textContent = "Reconnecting…";
    setTimeout(connect, 1000);
  });
  socket.addEventListener("message", ({ data }) => {
    try {
      handleMessage(JSON.parse(data));
    } catch {
      // ignore malformed controller messages
    }
  });
}

function send(message) {
  if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(message));
}

function sendLayout(player) {
  send({ type: "layout", player, controls: layouts[player] ?? [] });
  if (tells[player]) send({ type: "status", player, text: tells[player] });
}

// ---------- assets ----------

function assetUrl(url) {
  const text = String(url ?? "");
  if (/^(data:|blob:|\/)/.test(text)) return text;
  return `/api/asset?url=${encodeURIComponent(text)}`;
}

function loadSprite(url, fallback = "❓", size = 128) {
  const key = `${url}|${fallback}`;
  if (spriteCache.has(key)) return spriteCache.get(key);
  const sprite = { url, fallback, image: null, ready: false, failed: false, width: size, height: size, textures: [] };
  spriteCache.set(key, sprite);
  const image = new Image();
  image.crossOrigin = "anonymous";
  image.addEventListener("load", () => useImage(sprite, image, image.naturalWidth, image.naturalHeight));
  image.addEventListener("error", () => {
    sprite.failed = true;
    useImage(sprite, fallbackCanvas(fallback, size), size, size);
  });
  image.src = assetUrl(url);
  return sprite;
}

function useImage(sprite, image, width, height) {
  sprite.image = image;
  sprite.width = width || 128;
  sprite.height = height || 128;
  sprite.ready = true;
  for (const texture of sprite.textures) {
    texture.image = image;
    texture.needsUpdate = true;
  }
}

function fallbackCanvas(text, size) {
  const surface = document.createElement("canvas");
  surface.width = surface.height = size;
  const draw = surface.getContext("2d");
  const label = String(text ?? "?");
  const short = [...label].length <= 2;
  draw.textAlign = "center";
  draw.textBaseline = "middle";
  if (short) {
    draw.font = `${size * 0.78}px ${EMOJI_FONT}`;
    draw.fillText(label, size / 2, size / 2 + size * 0.05);
  } else {
    draw.fillStyle = "#2a2d3a";
    draw.beginPath();
    draw.roundRect(4, 4, size - 8, size - 8, size * 0.12);
    draw.fill();
    draw.fillStyle = "#ffffff";
    draw.font = `600 ${Math.max(12, size / Math.max(3, label.length * 0.6))}px sans-serif`;
    draw.fillText(label, size / 2, size / 2, size - 12);
  }
  return surface;
}

function twemojiUrl(character) {
  let codes = [...String(character)].map((symbol) => symbol.codePointAt(0).toString(16));
  if (!codes.includes("200d")) codes = codes.filter((code) => code !== "fe0f");
  return `https://cdn.jsdelivr.net/gh/jdecked/twemoji@15.1.0/assets/svg/${codes.join("-")}.svg`;
}

function textureFor(sprite) {
  const texture = new THREE.Texture(sprite.image ?? fallbackCanvas("", 8));
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.needsUpdate = true;
  sprite.textures.push(texture);
  return texture;
}

let audio = null;
function beep(frequency = 440, duration = 0.1, type = "square", volume = 0.05) {
  try {
    audio ??= new AudioContext();
    if (audio.state !== "running") return;
    const oscillator = audio.createOscillator();
    const gain = audio.createGain();
    oscillator.type = type;
    oscillator.frequency.value = frequency;
    gain.gain.setValueAtTime(volume, audio.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.0001, audio.currentTime + duration);
    oscillator.connect(gain).connect(audio.destination);
    oscillator.start();
    oscillator.stop(audio.currentTime + duration);
  } catch {
    // sound is optional
  }
}
addEventListener("pointerdown", () => audio?.resume(), { once: true });

// ---------- HUD, overlay, errors ----------

function buildDom() {
  dom.stage = document.querySelector("#game");
  dom.connection = document.querySelector("#connection") ?? document.createElement("span");
  dom.scores = element("div", "kit-scores");
  dom.status = element("div", "kit-status");
  dom.toast = element("div", "kit-toast hidden");
  dom.overlay = element("div", "kit-overlay hidden");
  dom.overlayTitle = element("strong");
  dom.overlayText = element("p");
  dom.overlayHint = element("small");
  dom.overlay.append(dom.overlayTitle, dom.overlayText, dom.overlayHint);
  document.body.append(dom.scores, dom.status, dom.toast, dom.overlay);
}

function element(tag, className = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  return node;
}

function showOverlay(title, text, hint, isError = false) {
  dom.overlayTitle.textContent = title;
  dom.overlayText.textContent = text;
  dom.overlayHint.textContent = hint;
  dom.overlay.classList.toggle("error", isError);
  dom.overlay.classList.remove("hidden");
}

function hideOverlay() {
  dom.overlay.classList.add("hidden");
}

function restartHint() {
  const wait = Math.max(0, Math.ceil(AUTO_RESTART_SECONDS - overTime));
  return overTime > 0.8 ? `Press any button to play again · restarting in ${wait}` : "";
}

function errorInfo(error, where) {
  const info = { message: String(error?.message ?? error), stack: String(error?.stack ?? ""), where };
  const match = info.stack.match(/game\.js[^:]*:(\d+):(\d+)/);
  if (match) {
    info.file = "game.js";
    info.line = Number(match[1]);
    info.column = Number(match[2]);
  }
  return info;
}

function fail(info) {
  if (phase === "broken") return;
  phase = "broken";
  console.error("[kit] game error", info);
  showOverlay("Fixing a bug…", info.message, "The game hit an error. Asking the AI to repair it.", true);
  if (errorReported) {
    showManualRestart(info.message);
    return;
  }
  errorReported = true;
  fetch(`/api/games/${gameId}/errors`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...info, version }),
  })
    .then((response) => response.json())
    .then((result) => (result.repairing ? waitForRepair() : showManualRestart(info.message, result.reason)))
    .catch(() => showManualRestart(info.message, "Automatic repair is unavailable."));
}

async function waitForRepair() {
  for (;;) {
    await new Promise((resolve) => setTimeout(resolve, 2000));
    const status = await fetchJson(`/api/games/${gameId}/status`);
    if (!status) continue;
    if ((status.version ?? 1) > version) {
      location.reload();
      return;
    }
    if (status.status !== "repairing") {
      showManualRestart(dom.overlayText.textContent, status.repair_error ?? "The repair did not work.");
      return;
    }
  }
}

function showManualRestart(message, reason = "") {
  restartAfterBug = Boolean(definition);
  showOverlay(
    "This game hit a bug",
    message,
    `${reason ? `${reason} ` : ""}${restartAfterBug ? "Press any button to restart." : "Generate the game again."}`,
    true,
  );
}

async function fetchJson(url) {
  try {
    const response = await fetch(url, { cache: "no-store" });
    return response.ok ? await response.json() : null;
  } catch {
    return null;
  }
}

function toAxis(value) {
  const number = Number(value);
  return Number.isFinite(number) ? Math.max(-1, Math.min(1, number)) : 0;
}
