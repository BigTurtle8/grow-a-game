const params = new URLSearchParams(location.search);
const gameId = params.get("game");
const player = Number(params.get("player") || 1);
const controlsRoot = document.querySelector("#controls");
const status = document.querySelector("#controller-status");
let socket;

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
  connect();
  render(definition[`player${player}`] || []);
}

function connect() {
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(`${protocol}://${location.host}/ws/games/${gameId}`);
  socket.addEventListener("open", () => {
    status.textContent = "Connected";
    status.classList.add("online");
  });
  socket.addEventListener("close", () => {
    status.textContent = "Reconnecting…";
    status.classList.remove("online");
    window.setTimeout(connect, 1000);
  });
}

function send(action, value) {
  if (socket?.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ type: "input", player, action, value }));
  }
}

function render(controls) {
  controlsRoot.replaceChildren();
  for (const control of controls) {
    if (control.type === "joystick") addJoystick(control);
    else addButton(control);
  }
}

function addButton(control) {
  const button = document.createElement("button");
  button.className = "action-button";
  button.textContent = control.label || control.action;
  const down = (event) => {
    event.preventDefault();
    button.classList.add("pressed");
    send(control.action, true);
  };
  const up = (event) => {
    event.preventDefault();
    button.classList.remove("pressed");
    send(control.action, false);
  };
  button.addEventListener("pointerdown", down);
  button.addEventListener("pointerup", up);
  button.addEventListener("pointercancel", up);
  controlsRoot.append(button);
}

function addJoystick(control) {
  const pad = document.createElement("div");
  pad.className = "joystick";
  pad.innerHTML = `<div class="stick"></div><span>${control.label || "Move"}</span>`;
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
    send(control.action, { x: x / radius, y: -y / radius });
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
    send(control.action, { x: 0, y: 0 });
  };
  pad.addEventListener("pointerup", release);
  pad.addEventListener("pointercancel", release);
  controlsRoot.append(pad);
}

