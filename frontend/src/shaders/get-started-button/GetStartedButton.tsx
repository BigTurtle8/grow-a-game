import { useEffect, useRef, useState, type CSSProperties } from "react";

import getStartedButtonSource from "./sources/get-started-button.html?raw";

export type GetStartedButtonProps = {
  className?: string;
  label?: "SIGN UP" | "SIGN IN";
  style?: CSSProperties;
  transparent?: boolean;
};

export function GetStartedButton({
  className = "",
  label = "SIGN UP",
  style,
  transparent = false,
}: GetStartedButtonProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const [documentVisible, setDocumentVisible] = useState(() => (
    typeof document === "undefined" || !document.hidden
  ));
  const [hostVisible, setHostVisible] = useState(true);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const host = hostRef.current;
    if (!host || typeof IntersectionObserver === "undefined") return undefined;
    const observer = new IntersectionObserver(([entry]) => {
      setHostVisible(entry?.isIntersecting ?? true);
    }, { rootMargin: "80px" });
    observer.observe(host);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (typeof document === "undefined") return undefined;
    const update = () => setDocumentVisible(!document.hidden);
    document.addEventListener("visibilitychange", update);
    return () => document.removeEventListener("visibilitychange", update);
  }, []);

  const mounted = hostVisible && documentVisible;

  useEffect(() => {
    setReady(false);
  }, [mounted]);

  return (
    <div
      ref={hostRef}
      className={`threeui-background get-started-button${className ? ` ${className}` : ""}`}
      role="group"
      aria-label="Interactive liquid-chrome Get Started button"
      data-state={!mounted ? "paused" : ready ? "ready" : "loading"}
      style={{
        position: "relative",
        overflow: "hidden",
        background: transparent ? "transparent" : "#222225",
        pointerEvents: "auto",
        ...style,
      }}
    >
      {mounted ? (
        <iframe
          title={`${label} liquid-chrome button`}
          srcDoc={getStartedButtonSource
            .replace('aria-label="Sign up"', `aria-label="${label}"`)
            .replace(">SIGN UP</span>", `>${label}</span>`)
            .replace("--bg:#222225;", `--bg:${transparent ? "transparent" : "#222225"};`)}
          sandbox="allow-scripts"
          loading="eager"
          onLoad={() => setReady(true)}
          style={{
            position: "absolute",
            inset: 0,
            display: "block",
            width: "100%",
            height: "100%",
            border: 0,
            background: transparent ? "transparent" : "#222225",
            opacity: ready ? 1 : 0,
            pointerEvents: ready ? "auto" : "none",
            transition: "opacity 240ms ease-out",
          }}
        />
      ) : null}
    </div>
  );
}
