import { useId, useState, type SubmitEvent } from "react";

import { describeError, type AuthOptions } from "../api/client";
import { signIn, startDemoSession } from "../api/endpoints";
import { Alert } from "../components/Common";
import { BrandMark, Icon, Wordmark, type IconName } from "../components/Icon";
import { isLocalDevelopment } from "../lib/session";

const HIGHLIGHTS: { icon: IconName; title: string; text: string }[] = [
  {
    icon: "shield",
    title: "Security first",
    text: "Injection flaws, weak cryptography, leaked secrets and vulnerable libraries.",
  },
  {
    icon: "bug",
    title: "Bugs and code quality",
    text: "Error-prone Java, JavaScript and TypeScript, with the exact line and a fix.",
  },
  {
    icon: "graph",
    title: "Architecture at a glance",
    text: "See how modules and files depend on each other and what a change affects.",
  },
];

export function LoginPage({
  options,
  onSignedIn,
}: {
  options: AuthOptions | null;
  onSignedIn: () => void;
}) {
  const tokenId = useId();
  const hintId = useId();
  const [token, setToken] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"token" | "demo" | null>(null);
  const demo = options?.demo_enabled === true;

  async function submit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy("token");
    setError(null);
    try {
      await signIn(token.trim());
      setToken("");
      onSignedIn();
    } catch (caught) {
      setError(describeError(caught));
    } finally {
      setBusy(null);
    }
  }

  async function tryDemo() {
    setBusy("demo");
    setError(null);
    try {
      await startDemoSession();
      onSignedIn();
    } catch (caught) {
      setError(describeError(caught));
      setBusy(null);
    }
  }

  return (
    <main className="login" aria-labelledby="login-title">
      <section className="login-hero" aria-label="About refactorX">
        <div className="row login-brand">
          <BrandMark />
          <Wordmark className="login-wordmark" />
        </div>
        <h1 id="login-title" className="login-headline">
          Ship code you can <span className="gradient-text">trust</span>.
        </h1>
        <p className="login-lead">
          refactorX reviews your source code for security flaws, bugs and risky dependencies — and
          shows exactly where each problem is and how to fix it.
        </p>
        <ul className="login-highlights">
          {HIGHLIGHTS.map((item) => (
            <li key={item.title}>
              <span className="feature-icon" aria-hidden="true">
                <Icon name={item.icon} size={18} />
              </span>
              <div>
                <strong>{item.title}</strong>
                <span>{item.text}</span>
              </div>
            </li>
          ))}
        </ul>
        <p className="small muted login-note">
          <Icon name="lock" size={13} /> Your code stays on this server. Nothing is sent to AI
          services.
        </p>
      </section>

      <section className="card login-card stack" aria-label="Sign in">
        {demo ? (
          <div className="stack">
            <h2 className="card-title">Try refactorX</h2>
            <p className="small secondary" style={{ margin: 0 }}>
              No sign-up needed. The demo opens a shared workspace where you can run the sample
              project or upload your own ZIP.
            </p>
            <button
              type="button"
              className="btn btn-primary btn-lg"
              disabled={busy !== null}
              onClick={() => {
                void tryDemo();
              }}
            >
              <Icon name="play" size={16} />
              {busy === "demo" ? "Opening the demo…" : "Try the demo"}
            </button>
            <div className="divider" role="separator">
              <span>or sign in with your access token</span>
            </div>
          </div>
        ) : (
          <h2 className="card-title">Sign in</h2>
        )}
        <form
          className="stack"
          onSubmit={(event) => {
            void submit(event);
          }}
        >
          <div className="field">
            <label htmlFor={tokenId}>Access token</label>
            <input
              id={tokenId}
              name="token"
              type="password"
              autoComplete="off"
              required
              value={token}
              aria-describedby={hintId}
              onChange={(event) => {
                setToken(event.target.value);
              }}
            />
            <p id={hintId} className="hint">
              {isLocalDevelopment(options) ? (
                <>
                  Print it with <code>uv run crp-dev token --show</code>.
                </>
              ) : (
                "The owner's access token from the deployment settings."
              )}{" "}
              It is exchanged for a secure session and never stored by the browser.
            </p>
          </div>
          {error ? <Alert tone="bad">{error}</Alert> : null}
          <button
            type="submit"
            className={demo ? "btn btn-ghost" : "btn btn-primary"}
            disabled={busy !== null || token.trim() === ""}
          >
            <Icon name="shield" size={16} />
            {busy === "token" ? "Signing in…" : "Sign in"}
          </button>
        </form>
      </section>
    </main>
  );
}
