import { useEffect, useRef, useState } from "react";
import type { Session } from "@supabase/supabase-js";
import { BotAvatar } from "bot-avatars";
import { ThinkingOrb } from "thinking-orbs";
import { getAudioContext, VoiceBeam } from "voice-glow";
import { AuthScreen } from "./AuthScreen";
import { supabase } from "./lib/supabase";
import { CrtBackground } from "./shaders/crt/CrtBackground";

type Screen = "home" | "auth" | "creator" | "loading";

type GameJob = {
  game_id: string;
  status: "generating" | "ready" | "failed" | "cancelled";
  game_url?: string;
  name?: string;
  error?: string;
  stage?: string | null;
};

const MIME_TYPES = [
  "audio/mp4",
  "audio/webm;codecs=opus",
  "audio/ogg;codecs=opus",
];

function wantsRecordPage() {
  return new URLSearchParams(window.location.search).get("screen") === "record";
}

function readGuest() {
  return sessionStorage.getItem("grow-a-game:guest") === "1";
}

function writeGuest(value: boolean) {
  if (value) sessionStorage.setItem("grow-a-game:guest", "1");
  else sessionStorage.removeItem("grow-a-game:guest");
}

export function App() {
  const [screen, setScreen] = useState<Screen>(wantsRecordPage() ? "creator" : "home");
  const [session, setSession] = useState<Session | null>(null);
  const [guest, setGuest] = useState(readGuest);
  const [authReady, setAuthReady] = useState(false);
  const [recording, setRecording] = useState(false);
  const [microphoneStream, setMicrophoneStream] = useState<MediaStream | null>(null);
  const [transcribing, setTranscribing] = useState(false);
  const [transcript, setTranscript] = useState("");
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState("");
  const [stage, setStage] = useState("Starting generation…");
  const recorderRef = useRef<MediaRecorder | null>(null);
  const timerRef = useRef<number | null>(null);
  const activeGameRef = useRef<string | null>(null);
  const pollAbortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!supabase) {
      setAuthReady(true);
      if (wantsRecordPage()) setScreen(readGuest() ? "creator" : "home");
      return;
    }

    void supabase.auth.getSession().then(({ data }) => {
      setSession(data.session);
      if (data.session) {
        writeGuest(false);
        setGuest(false);
      }
      if (wantsRecordPage()) setScreen(data.session || readGuest() ? "creator" : "auth");
      setAuthReady(true);
    });
    const { data } = supabase.auth.onAuthStateChange((_event, nextSession) => {
      setSession(nextSession);
      if (nextSession) {
        writeGuest(false);
        setGuest(false);
      }
      setScreen((current) => {
        if (nextSession && current === "auth") return "creator";
        if (!nextSession && current === "creator" && !readGuest()) return "auth";
        return current;
      });
    });
    return () => data.subscription.unsubscribe();
  }, []);

  useEffect(() => () => {
    if (timerRef.current) window.clearInterval(timerRef.current);
    recorderRef.current?.stream.getTracks().forEach((track) => track.stop());
  }, []);

  async function toggleRecording() {
    if (recorderRef.current?.state === "recording") {
      recorderRef.current.stop();
      return;
    }

    setError("");
    void getAudioContext()?.resume();
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
      setError("Microphone recording is not supported in this browser.");
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      setMicrophoneStream(stream);
      const mimeType = MIME_TYPES.find((type) => MediaRecorder.isTypeSupported(type));
      const chunks: Blob[] = [];
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
      recorderRef.current = recorder;

      recorder.addEventListener("dataavailable", (event) => {
        if (event.data.size) chunks.push(event.data);
      });
      recorder.addEventListener("stop", async () => {
        stream.getTracks().forEach((track) => track.stop());
        recorderRef.current = null;
        setMicrophoneStream(null);
        if (timerRef.current) window.clearInterval(timerRef.current);
        setRecording(false);
        try {
          const blob = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
          const wav = await recordingToWav(blob);
          await transcribeRecording(wav);
        } catch (reason) {
          setTranscribing(false);
          setError(reason instanceof Error ? reason.message : "The recording could not be prepared.");
        }
      });

      setTranscript("");
      setElapsed(0);
      setRecording(true);
      const startedAt = Date.now();
      timerRef.current = window.setInterval(
        () => setElapsed(Math.floor((Date.now() - startedAt) / 1000)),
        250,
      );
      recorder.start();
    } catch (reason) {
      setMicrophoneStream(null);
      const message =
        reason instanceof DOMException && reason.name === "NotAllowedError"
          ? "Allow microphone access, then press record again."
          : "The microphone could not be started.";
      setError(message);
    }
  }

  async function transcribeRecording(recordingBlob: Blob) {
    setTranscribing(true);
    const data = new FormData();
    data.append("audio", recordingBlob, "recording.wav");

    try {
      const response = await fetch("/api/transcribe", {
        method: "POST",
        body: data,
      });
      const result = (await response.json()) as { text?: string; detail?: string };
      if (!response.ok || !result.text) {
        throw new Error(result.detail || "The recording could not be transcribed.");
      }
      setTranscript(result.text);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Transcription failed.");
    } finally {
      setTranscribing(false);
    }
  }

  async function submitTranscript() {
    const prompt = transcript.trim();
    if (!prompt) {
      setError("Record your idea before submitting it.");
      return;
    }

    setError("");
    setStage("Sending your idea…");
    setScreen("loading");
    const data = new FormData();
    data.append("prompt", prompt);

    try {
      const response = await fetch("/api/games/generate", {
        method: "POST",
        body: data,
      });
      const job = (await response.json()) as GameJob & { detail?: string };
      if (!response.ok) throw new Error(job.detail || "Generation could not start.");
      activeGameRef.current = job.game_id;
      if (job.stage) setStage(job.stage);
      await pollJob(job.game_id);
    } catch (reason) {
      if (reason instanceof DOMException && reason.name === "AbortError") return;
      if (activeGameRef.current === null) return;
      setError(reason instanceof Error ? reason.message : "Generation failed.");
      setScreen("creator");
    }
  }

  async function pollJob(gameId: string) {
    const abort = new AbortController();
    pollAbortRef.current = abort;
    try {
      for (;;) {
        const response = await fetch(`/api/games/${gameId}/status`, { signal: abort.signal });
        const job = (await response.json()) as GameJob;
        if (job.stage) setStage(job.stage);
        if (abort.signal.aborted || activeGameRef.current !== gameId) return;
        if (job.status === "cancelled") return;
        if (job.status === "ready" && job.game_url) {
          rememberGame(session?.user.id, job);
          window.location.assign(job.game_url);
          return;
        }
        if (job.status === "failed") {
          throw new Error(job.error || "Grok could not build this game.");
        }
        await wait(1000, abort.signal);
      }
    } catch (reason) {
      if (reason instanceof DOMException && reason.name === "AbortError") return;
      throw reason;
    } finally {
      if (pollAbortRef.current === abort) pollAbortRef.current = null;
    }
  }

  function cancelGeneration() {
    const gameId = activeGameRef.current;
    activeGameRef.current = null;
    pollAbortRef.current?.abort();
    if (gameId) {
      void fetch(`/api/games/${gameId}/cancel`, { method: "POST" }).catch(() => {});
    }
    setScreen("creator");
  }

  return (
    <main className="experience">
      {screen === "home" && (
        <HomeScreen
          onStart={() => setScreen(authReady && (session || guest) ? "creator" : "auth")}
        />
      )}
      {screen === "auth" && (
        <AuthScreen
          onAuthenticated={(nextSession) => {
            writeGuest(false);
            setGuest(false);
            setSession(nextSession);
            setScreen("creator");
          }}
          onGuest={() => {
            writeGuest(true);
            setGuest(true);
            setScreen("creator");
          }}
          onBack={() => setScreen("home")}
        />
      )}
      {screen === "creator" && (session || guest) && (
        <CreatorScreen
          elapsed={elapsed}
          error={error}
          microphoneStream={microphoneStream}
          recording={recording}
          transcript={transcript}
          transcribing={transcribing}
          session={session}
          guest={guest}
          onRecord={toggleRecording}
          onSubmit={submitTranscript}
          onHome={() => {
            const url = new URL(window.location.href);
            url.searchParams.delete("screen");
            window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
            setScreen("home");
          }}
          onSignOut={async () => {
            writeGuest(false);
            setGuest(false);
            await supabase?.auth.signOut();
            setSession(null);
            setScreen("auth");
          }}
        />
      )}
      {screen === "loading" && (
        <LoadingScreen stage={stage} onCancel={cancelGeneration} />
      )}
    </main>
  );
}

function HomeScreen({ onStart }: { onStart: () => void }) {
  return (
    <section className="screen home-screen">
      <CrtBackground
        variant="nintendo"
        speed={1}
        motion={1}
        hue={0}
        saturation={1}
        brightness={1}
        opacity={1}
      />
      <div className="crt-shade" />
      <button
        className="crt-start-hit"
        type="button"
        aria-label="Background push start"
        onClick={onStart}
      />
      <div className="home-content">
        <img className="brand-logo" src="/brand-logo.png" alt="Grow a Game" />
      </div>
    </section>
  );
}

type CreatorScreenProps = {
  elapsed: number;
  error: string;
  microphoneStream: MediaStream | null;
  recording: boolean;
  transcript: string;
  transcribing: boolean;
  session: Session | null;
  guest: boolean;
  onRecord: () => void;
  onSubmit: () => void;
  onHome: () => void;
  onSignOut: () => void;
};

function CreatorScreen({
  elapsed,
  error,
  microphoneStream,
  recording,
  transcript,
  transcribing,
  session,
  guest,
  onRecord,
  onSubmit,
  onHome,
  onSignOut,
}: CreatorScreenProps) {
  const [showGames, setShowGames] = useState(false);
  const [games, setGames] = useState(() => (session ? readStoredGames(session.user.id) : []));

  useEffect(() => {
    if (!session) {
      setGames([]);
      return;
    }
    let cancelled = false;
    void Promise.all(
      games.map(async (game) => {
        if (game.name) return game;
        try {
          const response = await fetch(`/games/${game.gameId}/metadata.json`);
          if (!response.ok) return game;
          const metadata = (await response.json()) as { name?: string };
          return metadata.name ? { ...game, name: metadata.name } : game;
        } catch {
          return game;
        }
      }),
    ).then((next) => {
      if (cancelled || next.every((game, index) => game.name === games[index]?.name)) return;
      writeStoredGames(session.user.id, next);
      setGames(next);
    });
    return () => {
      cancelled = true;
    };
  }, [session?.user.id]);

  return (
    <section className="screen creator-screen">
      <header className="creator-nav">
        <div className="creator-nav-actions">
          <button type="button" onClick={onHome}>Home</button>
          {!guest && (
            <button type="button" onClick={() => setShowGames((visible) => !visible)}>
              Stored games
            </button>
          )}
        </div>
        <div>
          <BotAvatar
            type="droid"
            face="mouth"
            state={recording ? "working" : "default"}
            size={36}
            theme="dark"
            shading="plastic"
            seed={0.38}
          />
          <span>{guest ? "Guest" : session?.user.email}</span>
          <button type="button" onClick={onSignOut}>{guest ? "Sign in" : "Sign out"}</button>
        </div>
      </header>

      {showGames && !guest && (
        <aside className="games-panel">
          <div>
            <p className="creator-kicker">YOUR LIBRARY</p>
            <button type="button" onClick={() => setShowGames(false)} aria-label="Close stored games">
              ×
            </button>
          </div>
          {games.length ? (
            <ul>
              {games.map((game) => (
                <li key={game.gameId}>
                  <a href={game.url}>{game.name || "Untitled game"}</a>
                  <time>{new Date(game.createdAt).toLocaleDateString()}</time>
                </li>
              ))}
            </ul>
          ) : (
            <p className="games-empty">No stored games yet. Your first idea starts here.</p>
          )}
        </aside>
      )}

      <div className="creator-main">
        <h1>Let us build your story.</h1>
        <div className="voice-composer-glow">
        <VoiceBeam
          className="voice-composer-beam"
          type="default"
          stream={microphoneStream}
          processing={transcribing}
          active={recording || transcribing}
          idle={0.32}
          sensitivity={3.4}
          threshold={0.012}
          reach={1.45}
          spread={1.1}
          colorVariant="colorful"
          theme="dark"
          strength={0.95}
          borderRadius={32}
        >
          <div className="voice-composer">
            <div className={`transcript-copy${transcript ? " has-transcript" : ""}`}>
              {recording
                ? "Listening…"
                : transcribing
                  ? "Turning your voice into words…"
                  : transcript || "Your game idea will appear here."}
            </div>
            {error && <p className="creator-error">{error}</p>}
            <div className="composer-actions">
              <button
                className={`composer-record${recording ? " is-recording" : ""}`}
                type="button"
                onClick={onRecord}
                disabled={transcribing}
                aria-pressed={recording}
              >
                <span className="record-square" aria-hidden="true" />
                <span>
                  {recording ? "Stop" : transcript ? "Record again" : "Record"}
                </span>
                <time>{formatTime(elapsed)}</time>
              </button>
              <button
                className="composer-submit"
                type="button"
                onClick={onSubmit}
                disabled={!transcript || recording || transcribing}
                aria-label="Build this game"
              >
                ↑
              </button>
            </div>
          </div>
        </VoiceBeam>
        </div>
      </div>
    </section>
  );
}

function LoadingScreen({
  stage,
  onCancel,
}: {
  stage: string;
  onCancel: () => void;
}) {
  return (
    <section className="screen loading-screen" aria-label="Building your game" aria-live="polite">
      <div className="loading-orb">
        <ThinkingOrb state="solving" size={64} theme="light" />
      </div>
      <p className="loading-stage">{stage}</p>
      <button className="loading-cancel" type="button" onClick={onCancel}>
        Cancel
      </button>
    </section>
  );
}

const STT_SAMPLE_RATES = [8000, 16000, 22050, 24000, 44100, 48000];

async function recordingToWav(recording: Blob) {
  const context = new AudioContext();
  try {
    const decoded = await context.decodeAudioData(await recording.arrayBuffer());
    const sampleRate = STT_SAMPLE_RATES.includes(decoded.sampleRate) ? decoded.sampleRate : 16000;
    const audio = sampleRate === decoded.sampleRate ? decoded : await resample(decoded, sampleRate);
    return encodeWav(audio);
  } finally {
    await context.close();
  }
}

function resample(buffer: AudioBuffer, sampleRate: number) {
  const length = Math.max(1, Math.round(buffer.duration * sampleRate));
  const offline = new OfflineAudioContext(buffer.numberOfChannels, length, sampleRate);
  const source = offline.createBufferSource();
  source.buffer = buffer;
  source.connect(offline.destination);
  source.start();
  return offline.startRendering();
}

function encodeWav(buffer: AudioBuffer) {
  const channels = buffer.numberOfChannels;
  const length = buffer.length;
  const bytesPerSample = 2;
  const blockAlign = channels * bytesPerSample;
  const dataSize = length * blockAlign;
  const header = new ArrayBuffer(44);
  const view = new DataView(header);
  const write = (offset: number, text: string) => {
    for (let index = 0; index < text.length; index += 1) view.setUint8(offset + index, text.charCodeAt(index));
  };
  write(0, "RIFF");
  view.setUint32(4, 36 + dataSize, true);
  write(8, "WAVE");
  write(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, channels, true);
  view.setUint32(24, buffer.sampleRate, true);
  view.setUint32(28, buffer.sampleRate * blockAlign, true);
  view.setUint16(32, blockAlign, true);
  view.setUint16(34, 16, true);
  write(36, "data");
  view.setUint32(40, dataSize, true);

  const pcm = new Int16Array(length * channels);
  const channelData = Array.from({ length: channels }, (_, channel) => buffer.getChannelData(channel));
  for (let index = 0; index < length; index += 1) {
    for (let channel = 0; channel < channels; channel += 1) {
      const sample = Math.max(-1, Math.min(1, channelData[channel][index]));
      pcm[index * channels + channel] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
    }
  }
  return new Blob([header, pcm], { type: "audio/wav" });
}

function formatTime(totalSeconds: number) {
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

function wait(milliseconds: number, signal?: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException("Aborted", "AbortError"));
      return;
    }
    const timer = window.setTimeout(resolve, milliseconds);
    signal?.addEventListener(
      "abort",
      () => {
        window.clearTimeout(timer);
        reject(new DOMException("Aborted", "AbortError"));
      },
      { once: true },
    );
  });
}

type StoredGame = {
  gameId: string;
  url: string;
  name?: string;
  createdAt: string;
};

function storageKey(userId: string) {
  return `grow-a-game:${userId}:games`;
}

function readStoredGames(userId: string): StoredGame[] {
  try {
    return JSON.parse(localStorage.getItem(storageKey(userId)) || "[]") as StoredGame[];
  } catch {
    return [];
  }
}

function writeStoredGames(userId: string, games: StoredGame[]) {
  localStorage.setItem(storageKey(userId), JSON.stringify(games.slice(0, 24)));
}

function rememberGame(userId: string | undefined, job: GameJob) {
  if (!userId || !job.game_url) return;
  const games = readStoredGames(userId);
  if (games.some((game) => game.gameId === job.game_id)) return;
  games.unshift({
    gameId: job.game_id,
    url: job.game_url,
    name: job.name?.trim() || undefined,
    createdAt: new Date().toISOString(),
  });
  writeStoredGames(userId, games);
}
