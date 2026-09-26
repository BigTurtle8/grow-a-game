const form = document.querySelector("#generator");
const audio = document.querySelector("#audio");
const fileName = document.querySelector("#file-name");
const statusPanel = document.querySelector("#status");
const result = document.querySelector("#result");
const button = document.querySelector("#generate");
const recordButton = document.querySelector("#record");
const recordLabel = document.querySelector("#record-label");
const recordTime = document.querySelector("#record-time");
const preview = document.querySelector("#recording-preview");
let recorder;
let recording;
let timer;
let startedAt;

audio.addEventListener("change", () => {
  fileName.textContent = audio.files[0]?.name || "WAV, MP3, M4A, or OGG";
  if (audio.files.length) clearRecording();
});

recordButton.addEventListener("click", async () => {
  if (recorder?.state === "recording") {
    recorder.stop();
    return;
  }
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
    showStatus("This browser cannot record audio. Use Chrome, Safari, or the file fallback.", true);
    return;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const mimeType = ["audio/mp4", "audio/webm;codecs=opus", "audio/ogg;codecs=opus"]
      .find((type) => MediaRecorder.isTypeSupported(type));
    const chunks = [];
    recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    recorder.addEventListener("dataavailable", (event) => {
      if (event.data.size) chunks.push(event.data);
    });
    recorder.addEventListener("stop", () => {
      window.clearInterval(timer);
      stream.getTracks().forEach((track) => track.stop());
      recording = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
      preview.src = URL.createObjectURL(recording);
      preview.classList.remove("hidden");
      recordButton.classList.remove("recording");
      recordLabel.textContent = "Record again";
      showStatus("Recording ready. Press Generate game.", false);
    });
    recorder.start();
    audio.value = "";
    recordButton.classList.add("recording");
    recordLabel.textContent = "Stop recording";
    startedAt = Date.now();
    updateTimer();
    timer = window.setInterval(updateTimer, 250);
  } catch (error) {
    showStatus(
      error.name === "NotAllowedError"
        ? "Microphone permission was denied. Allow microphone access and try again."
        : `Could not start the microphone: ${error.message}`,
      true,
    );
  }
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (recorder?.state === "recording") {
    showStatus("Stop the recording before generating.", true);
    return;
  }
  const data = new FormData(form);
  if (!data.get("prompt") && !audio.files.length && !recording) {
    showStatus("Add a game idea or record your voice.", true);
    return;
  }
  if (recording) {
    data.delete("audio");
    data.append("audio", recording, recordingName(recording.type));
  } else if (!audio.files.length) {
    data.delete("audio");
  }

  button.disabled = true;
  result.classList.add("hidden");
  showStatus("Planting your idea…");
  try {
    const response = await fetch("/api/games/generate", { method: "POST", body: data });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || "Could not start generation.");
    await poll(body.game_id);
  } catch (error) {
    showStatus(error.message, true);
    button.disabled = false;
  }
});

async function poll(gameId) {
  const response = await fetch(`/api/games/${gameId}/status`);
  const job = await response.json();
  if (job.status === "ready") {
    showResult(job);
    button.disabled = false;
    return;
  }
  if (job.status === "failed") {
    showStatus(job.error || "Generation failed.", true);
    button.disabled = false;
    return;
  }
  showStatus(job.stage || (job.transcript ? "Writing the game…" : "Listening to your idea…"));
  window.setTimeout(() => poll(gameId), 1000);
}

async function showResult(job) {
  statusPanel.classList.add("hidden");
  result.classList.remove("hidden");
  document.querySelector("#transcript").textContent = `“${job.transcript}”`;
  const quality = job.quality;
  const qualityLine = document.querySelector("#quality");
  if (quality && quality.checks_total) {
    const failed = (quality.known_issues || []).length;
    qualityLine.textContent = failed
      ? `Passed ${quality.checks_passed}/${quality.checks_total} automated checks after ${quality.rounds || 1} test rounds.`
      : `Passed all ${quality.checks_total} automated checks after researching how this game is usually played.`;
    qualityLine.classList.toggle("warn", Boolean(failed));
    qualityLine.classList.remove("hidden");
  } else {
    qualityLine.classList.add("hidden");
  }
  const design = document.querySelector("#design");
  if (job.design) {
    design.textContent = job.design.split("\n").slice(0, 8).join("\n");
    design.classList.remove("hidden");
  } else {
    design.classList.add("hidden");
  }
  document.querySelector("#game-link").href = job.game_url;
  document.querySelector("#p1-link").href = job.player1_url;
  document.querySelector("#p2-link").href = job.player2_url;
  try {
    const metadata = await fetch(`${job.game_url}metadata.json`).then((item) => item.json());
    document.querySelector("#game-title").textContent = metadata.name;
    document.querySelector("#p2-link").classList.toggle("hidden", metadata.players < 2);
  } catch {
    document.querySelector("#game-title").textContent = "Generated game";
  }
}

function showStatus(message, isError = false) {
  statusPanel.textContent = message;
  statusPanel.classList.remove("hidden");
  statusPanel.classList.toggle("error", isError);
}

function updateTimer() {
  const seconds = Math.floor((Date.now() - startedAt) / 1000);
  recordTime.textContent = `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}

function recordingName(mimeType) {
  if (mimeType.includes("mp4")) return "recording.m4a";
  if (mimeType.includes("ogg")) return "recording.ogg";
  return "recording.webm";
}

function clearRecording() {
  recording = undefined;
  preview.removeAttribute("src");
  preview.classList.add("hidden");
  recordLabel.textContent = "Record voice";
  recordTime.textContent = "00:00";
}

