const params = new URLSearchParams(location.search);
const gameId = params.get("game");
const player = Number(params.get("player") || 1);
const controlsRoot = document.querySelector("#controls");
const status = document.querySelector("#controller-status");
const message = document.querySelector("#controller-message");
const DIRECTIONS = [
  { name: "up", label: "▲", x: 0, y: -1 },
  { name: "left", label: "◀", x: -1, y: 0 },
  { name: "down", label: "▼", x: 0, y: 1 },
  { name: "right", label: "▶", x: 1, y: 0 },
];
let socket;
let currentLayout = "";

if (!gameId) {
  status.textContent = "Missing game ID";
} else {
  setup();
}

async function setup() {
  const [metadata, definition] = await Promise.all([
    fetch(`/games/${gameId}/metadata.json`).then((response) => response.json()),
    fetch(`/games/${gameId}/controller.json`).then((response) => response.json()),
  ]);
  document.querySelector("#player-label").textContent = `Player ${player}`;
  document.querySelector("#game-name").textContent = metadata.name;
  document.body.dataset.player = String(player);
  render(definition[`player${player}`] || []);
  connect();
}

function connect() {
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(`${protocol}://${location.host}/ws/games/${gameId}`);
  socket.addEventListener("open", () => {
    status.textContent = "Connected";
    status.classList.add("online");
    socket.send(JSON.stringify({ type: "hello", player }));
  });
  socket.addEventListener("close", () => {
    status.textContent = "Reconnecting…";
    status.classList.remove("online");
    window.setTimeout(connect, 1000);
  });
  socket.addEventListener("message", ({ data }) => {
    let incoming;
    try {
      incoming = JSON.parse(data);
    } catch {
      return;
    }
    if (Number(incoming.player) !== player) return;
    if (incoming.type === "layout" && Array.isArray(incoming.controls)) render(incoming.controls);
    if (incoming.type === "status") {
      message.textContent = String(incoming.text ?? "");
      message.classList.toggle("hidden", !message.textContent);
    }
  });
}

function send(control, kind, extra = {}) {
  if (socket?.readyState !== WebSocket.OPEN) return;
  socket.send(JSON.stringify({ type: "input", player, control, action: control, kind, ...extra }));
  if (kind === "press" || kind === "select") navigator.vibrate?.(12);
}

function render(controls) {
  const signature = JSON.stringify(controls);
  if (signature === currentLayout) return;
  currentLayout = signature;
  controlsRoot.replaceChildren();
  for (const control of controls) {
    const id = String(control.id ?? control.action ?? "a");
    if (control.type === "joystick") addJoystick(id, control);
    else if (control.type === "dpad") addDpad(id, control);
    else if (control.type === "choice") addChoice(id, control);
    else addButton(id, control);
  }
}

function labelled(node, text) {
  const wrapper = document.createElement("div");
  wrapper.className = "control";
  const label = document.createElement("span");
  label.className = "control-label";
  label.textContent = text;
  wrapper.append(node, label);
  controlsRoot.append(wrapper);
}

function holdable(element, onDown, onUp) {
  element.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    try {
      element.setPointerCapture(event.pointerId);
    } catch {
      // capture is optional; the press still counts
    }
    element.classList.add("pressed");
    onDown();
  });
  const release = (event) => {
    event.preventDefault();
    if (!element.classList.contains("pressed")) return;
    element.classList.remove("pressed");
    onUp();
  };
  element.addEventListener("pointerup", release);
  element.addEventListener("pointercancel", release);
}

function addDpad(id, control) {
  const dpad = document.createElement("div");
  dpad.className = "dpad";
  for (const direction of DIRECTIONS) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `dpad-button ${direction.name}`;
    button.textContent = direction.label;
    button.setAttribute("aria-label", direction.name);
    const value = { x: direction.x, y: direction.y };
    holdable(
      button,
      () => send(id, "press", { direction: direction.name, value }),
      () => send(id, "release", { direction: direction.name, value }),
    );
    dpad.append(button);
  }
  labelled(dpad, control.label || "Move");
}

function addButton(id, control) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "action-button";
  button.textContent = control.label || id;
  if (control.color) button.style.setProperty("--button", control.color);
  holdable(
    button,
    () => send(id, "press", { value: true }),
    () => send(id, "release", { value: false }),
  );
  controlsRoot.append(button);
}

function addChoice(id, control) {
  const options = Array.isArray(control.options) ? control.options : [];
  const grid = document.createElement("div");
  grid.className = "choice-grid";
  const columns = control.columns || (options.length === 4 ? 2 : Math.min(options.length, 7));
  grid.style.setProperty("--columns", String(Math.max(1, columns)));
  options.forEach((option, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "choice-button";
    button.textContent = String(option);
    holdable(
      button,
      () => send(id, "select", { value: index, option: String(option) }),
      () => {},
    );
    grid.append(button);
  });
  labelled(grid, control.label || "Choose");
}

function addJoystick(id, control) {
  const pad = document.createElement("div");
  pad.className = "joystick";
  pad.innerHTML = '<div class="stick"></div>';
  const stick = pad.querySelector(".stick");
  const move = (event) => {
    event.preventDefault();
    const bounds = pad.getBoundingClientRect();
    const radius = bounds.width * 0.32;
    let x = event.clientX - (bounds.left + bounds.width / 2);
    let y = event.clientY - (bounds.top + bounds.height / 2);
    const length = Math.hypot(x, y);
    if (length > radius) {
      x = (x / length) * radius;
      y = (y / length) * radius;
    }
    stick.style.transform = `translate(${x}px, ${y}px)`;
    send(id, "move", { value: { x: x / radius, y: y / radius } });
  };
  pad.addEventListener("pointerdown", (event) => {
    pad.setPointerCapture(event.pointerId);
    move(event);
  });
  pad.addEventListener("pointermove", (event) => {
    if (pad.hasPointerCapture(event.pointerId)) move(event);
  });
  const release = (event) => {
    if (pad.hasPointerCapture(event.pointerId)) pad.releasePointerCapture(event.pointerId);
    stick.style.transform = "translate(0, 0)";
    send(id, "move", { value: { x: 0, y: 0 } });
  };
  pad.addEventListener("pointerup", release);
  pad.addEventListener("pointercancel", release);
  labelled(pad, control.label || "Move");
}
