import * as THREE from "three";

const root = document.querySelector("#game");
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x11120f);
scene.fog = new THREE.Fog(0x11120f, 14, 38);

const camera = new THREE.PerspectiveCamera(58, innerWidth / innerHeight, 0.1, 100);
camera.position.set(0, 11, 14);
camera.lookAt(0, 0, 0);

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setSize(innerWidth, innerHeight);
renderer.shadowMap.enabled = true;
root.append(renderer.domElement);

scene.add(new THREE.HemisphereLight(0xe8ffab, 0x202317, 2.5));
const sun = new THREE.DirectionalLight(0xffffff, 3);
sun.position.set(4, 10, 6);
sun.castShadow = true;
scene.add(sun);

const floor = new THREE.Mesh(
  new THREE.PlaneGeometry(26, 18, 20, 14),
  new THREE.MeshStandardMaterial({ color: 0x24271f, wireframe: true })
);
floor.rotation.x = -Math.PI / 2;
floor.receiveShadow = true;
scene.add(floor);

const playerGeometry = new THREE.IcosahedronGeometry(0.75, 0);
const players = {
  1: makePlayer(0xc9ff44, -3),
  2: makePlayer(0xff8a4c, 3),
};
const inputs = {
  1: { x: 0, y: 0 },
  2: { x: 0, y: 0 },
};

function makePlayer(color, x) {
  const mesh = new THREE.Mesh(
    playerGeometry,
    new THREE.MeshStandardMaterial({ color, roughness: 0.3 })
  );
  mesh.position.set(x, 0.8, 0);
  mesh.castShadow = true;
  scene.add(mesh);
  return mesh;
}

for (let index = 0; index < 24; index++) {
  const plant = new THREE.Mesh(
    new THREE.ConeGeometry(0.18, 0.7, 5),
    new THREE.MeshStandardMaterial({ color: index % 2 ? 0xc9ff44 : 0x7b9d33 })
  );
  plant.position.set((Math.random() - 0.5) * 22, 0.35, (Math.random() - 0.5) * 14);
  scene.add(plant);
}

const gameId = location.pathname.split("/").filter(Boolean).at(-1);
const protocol = location.protocol === "https:" ? "wss" : "ws";
const socket = new WebSocket(`${protocol}://${location.host}/ws/games/${gameId}`);
const connection = document.querySelector("#connection");
socket.addEventListener("open", () => (connection.textContent = "Controllers online"));
socket.addEventListener("close", () => (connection.textContent = "Controllers disconnected"));
socket.addEventListener("message", ({ data }) => {
  const message = JSON.parse(data);
  if (message.type !== "input" || !players[message.player]) return;
  if (message.action === "move") {
    inputs[message.player] = message.value;
  }
  if (message.action === "action" && message.value) {
    const target = players[message.player];
    const next = Math.min(target.scale.x + 0.12, 2.2);
    target.scale.setScalar(next);
  }
});

const clock = new THREE.Clock();
function animate() {
  const delta = Math.min(clock.getDelta(), 0.05);
  for (const [number, player] of Object.entries(players)) {
    const input = inputs[number];
    player.position.x = THREE.MathUtils.clamp(player.position.x + input.x * delta * 6, -11, 11);
    player.position.z = THREE.MathUtils.clamp(player.position.z - input.y * delta * 6, -7, 7);
    player.rotation.x += delta * (1 + Math.abs(input.y) * 4);
    player.rotation.z -= delta * input.x * 4;
    player.scale.multiplyScalar(1 - delta * 0.012);
    player.scale.clampScalar(0.65, 2.2);
  }
  renderer.render(scene, camera);
  requestAnimationFrame(animate);
}
animate();

addEventListener("resize", () => {
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
});

