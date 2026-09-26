import { useEffect, useRef, useState } from "react";
import type { Session } from "@supabase/supabase-js";
import { BorderBeam } from "border-beam";
import { ThinkingOrb } from "thinking-orbs";
import { AuthScreen } from "./AuthScreen";
import { supabase } from "./lib/supabase";
import { CrtBackground } from "./shaders/crt/CrtBackground";
import { GetStartedButton } from "./shaders/get-started-button/GetStartedButton";

type Screen = "home" | "auth" | "creator" | "loading";

type GameJob = {
  game_id: string;
  status: "generating" | "ready" | "failed";
  game_url?: string;
  error?: string;
};

const MIME_TYPES = [
  "audio/mp4",
  "audio/webm;codecs=opus",
  "audio/ogg;codecs=opus",
];

export function App() {
  const [screen, setScreen] = useState<Screen>("home");
  const [session, setSession] = useState<Session | null>(null);
  const [authReady, setAuthReady] = useState(false);
  const [recording, setRecording] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState("");
  const recorderRef = useRef<MediaRecorder | null>(null);
  const timerRef = useRef<number | null>(null);

  useEffect(() => {
    if (!supabase) {
      setAuthReady(true);
      return;
    }

    void supabase.auth.getSession().then(({ data }) => {
      setSession(data.session);
      setAuthReady(true);
    });
    const { data } = supabase.auth.onAuthStateChange((_event, nextSession) => {
      setSession(nextSession);
      setScreen((current) => {
        if (nextSession && current === "auth") return "creator";
        if (!nextSession && current === "creator") return "auth";
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
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
      setError("Microphone recording is not supported in this browser.");
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mimeType = MIME_TYPES.find((type) => MediaRecorder.isTypeSupported(type));
      const chunks: Blob[] = [];
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
      recorderRef.current = recorder;

      recorder.addEventListener("dataavailable", (event) => {
        if (event.data.size) chunks.push(event.data);
      });
      recorder.addEventListener("stop", async () => {
        stream.getTracks().forEach((track) => track.stop());
        if (timerRef.current) window.clearInterval(timerRef.current);
        setRecording(false);
        const blob = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
        await generateFromRecording(blob);
      });

      setElapsed(0);
      setRecording(true);
      const startedAt = Date.now();
      timerRef.current = window.setInterval(
        () => setElapsed(Math.floor((Date.now() - startedAt) / 1000)),
        250,
      );
      recorder.start();
    } catch (reason) {
      const message =
        reason instanceof DOMException && reason.name === "NotAllowedError"
          ? "Allow microphone access, then press record again."
          : "The microphone could not be started.";
      setError(message);
    }
  }

  async function generateFromRecording(recordingBlob: Blob) {
    setScreen("loading");
    const data = new FormData();
    data.append("audio", recordingBlob, recordingName(recordingBlob.type));

    try {
      const response = await fetch("/api/games/generate", {
        method: "POST",
        body: data,
      });
      const job = (await response.json()) as GameJob & { detail?: string };
      if (!response.ok) throw new Error(job.detail || "Generation could not start.");
      await pollJob(job.game_id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Generation failed.");
      setScreen("creator");
    }
  }

  async function pollJob(gameId: string) {
    for (;;) {
      const response = await fetch(`/api/games/${gameId}/status`);
      const job = (await response.json()) as GameJob;
      if (job.status === "ready" && job.game_url) {
        rememberGame(session?.user.id, job);
        window.location.assign(job.game_url);
        return;
      }
      if (job.status === "failed") {
        throw new Error(job.error || "Grok could not build this game.");
      }
      await wait(1000);
    }
  }

  return (
    <main className="experience">
      {screen === "home" && (
        <HomeScreen
          onStart={() => setScreen(authReady && session ? "creator" : "auth")}
        />
      )}
      {screen === "auth" && (
        <AuthScreen
          onAuthenticated={(nextSession) => {
            setSession(nextSession);
            setScreen("creator");
          }}
          onBack={() => setScreen("home")}
        />
      )}
      {screen === "creator" && session && (
        <CreatorScreen
          elapsed={elapsed}
          error={error}
          recording={recording}
          session={session}
          onRecord={toggleRecording}
          onSignOut={async () => {
            await supabase?.auth.signOut();
            setSession(null);
            setScreen("auth");
          }}
        />
      )}
      {screen === "loading" && <LoadingScreen />}
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
      <div className="auth-preview" aria-label="Future account access">
        <GetStartedButton />
      </div>
      <div className="home-content">
        <img className="brand-logo" src="/brand-logo.png" alt="Grow a Game" />
      </div>
    </section>
  );
}

type CreatorScreenProps = {
  elapsed: number;
  error: string;
  recording: boolean;
  session: Session;
  onRecord: () => void;
  onSignOut: () => void;
};

function CreatorScreen({
  elapsed,
  error,
  recording,
  session,
  onRecord,
  onSignOut,
}: CreatorScreenProps) {
  const [showGames, setShowGames] = useState(false);
  const games = readStoredGames(session.user.id);

  return (
    <section className="screen creator-screen">
      <header className="creator-nav">
        <button type="button" onClick={() => setShowGames((visible) => !visible)}>
          Stored games
        </button>
        <div>
          <span>{session.user.email}</span>
          <button type="button" onClick={onSignOut}>Sign out</button>
        </div>
      </header>

      {showGames && (
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
                  <a href={game.url}>Generated game</a>
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
        <p className="creator-kicker">VOICE TO PLAYABLE WORLD</p>
        <h1>Let Grok build your story.</h1>
        <p className="creator-hint">Describe the game you want to play, then stop when you’re done.</p>
        {error && <p className="creator-error">{error}</p>}
        <BorderBeam
          className="voice-beam"
          size="md"
          colorVariant="colorful"
          strength={0.7}
          active
          theme="dark"
          borderRadius={24}
        >
          <button
            className={`creator-record${recording ? " is-recording" : ""}`}
            type="button"
            onClick={onRecord}
            aria-pressed={recording}
          >
            <span className="record-square" aria-hidden="true" />
            <span>{recording ? "Stop & build" : "Start recording"}</span>
            <time>{formatTime(elapsed)}</time>
          </button>
        </BorderBeam>
      </div>
    </section>
  );
}

function LoadingScreen() {
  return (
    <section className="screen loading-screen" aria-label="Building your game">
      <ThinkingOrb state="solving" size={64} theme="light" />
    </section>
  );
}

function recordingName(mimeType: string) {
  if (mimeType.includes("mp4")) return "recording.m4a";
  if (mimeType.includes("ogg")) return "recording.ogg";
  return "recording.webm";
}

function formatTime(totalSeconds: number) {
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

function wait(milliseconds: number) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

type StoredGame = {
  gameId: string;
  url: string;
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

function rememberGame(userId: string | undefined, job: GameJob) {
  if (!userId || !job.game_url) return;
  const games = readStoredGames(userId);
  if (games.some((game) => game.gameId === job.game_id)) return;
  games.unshift({
    gameId: job.game_id,
    url: job.game_url,
    createdAt: new Date().toISOString(),
  });
  localStorage.setItem(storageKey(userId), JSON.stringify(games.slice(0, 24)));
}
