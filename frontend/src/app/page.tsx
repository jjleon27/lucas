"use client";
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { googleLogin, login, quickLogin, setToken, signup } from "@/lib/api";
import { LOCALES, useT } from "@/lib/i18n";

// Google Client ID is injected at build time via NEXT_PUBLIC_GOOGLE_CLIENT_ID.
// Leave blank in your .env.local and the button simply hides.
const GOOGLE_CLIENT_ID = process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID || "";

// Small helper so TypeScript stops complaining about window.google.
declare global {
  interface Window {
    google?: any;
  }
}

export default function Landing() {
  const router = useRouter();
  const { t, locale, setLocale } = useT();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [mode, setMode] = useState<"quick" | "password">("quick");
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);
  const googleBtnRef = useRef<HTMLDivElement>(null);

  // Load Google Identity Services once on mount.
  useEffect(() => {
    if (!GOOGLE_CLIENT_ID) return;
    const scriptId = "google-identity";
    if (document.getElementById(scriptId)) {
      renderGoogleButton();
      return;
    }
    const s = document.createElement("script");
    s.id = scriptId;
    s.src = "https://accounts.google.com/gsi/client";
    s.async = true;
    s.defer = true;
    s.onload = renderGoogleButton;
    document.body.appendChild(s);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function renderGoogleButton() {
    if (!window.google || !googleBtnRef.current) return;
    window.google.accounts.id.initialize({
      client_id: GOOGLE_CLIENT_ID,
      callback: async (resp: any) => {
        try {
          setLoading(true);
          const res = await googleLogin(resp.credential);
          setToken(res.access_token);
          router.push("/dashboard");
        } catch (e: any) {
          setErr(e.message || "Google sign-in failed");
        } finally {
          setLoading(false);
        }
      },
    });
    window.google.accounts.id.renderButton(googleBtnRef.current, {
      theme: "outline",
      size: "large",
      width: 320,
      text: "continue_with",
      shape: "pill",
      logo_alignment: "center",
    });
  }

  async function handleQuick(e: React.FormEvent) {
    e.preventDefault();
    setErr("");
    if (!email) return;
    setLoading(true);
    try {
      const res = await quickLogin(email);
      setToken(res.access_token);
      router.push("/dashboard");
    } catch (e: any) {
      setErr(e.message || "Could not sign in");
    } finally {
      setLoading(false);
    }
  }

  async function handlePassword(e: React.FormEvent, kind: "login" | "signup") {
    e.preventDefault();
    setErr("");
    setLoading(true);
    try {
      const res = kind === "login" ? await login(email, password) : await signup(email, password);
      setToken(res.access_token);
      router.push("/dashboard");
    } catch (e: any) {
      setErr(e.message || "Something went wrong");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-[90vh] flex items-center justify-center -m-4 md:-m-8">
      <div className="w-full max-w-md relative">
        {/* Language picker — always visible on the landing */}
        <div className="absolute top-0 right-0">
          <select
            value={locale}
            onChange={(e) => setLocale(e.target.value as any)}
            className="text-xs bg-transparent text-slate-500 outline-none"
          >
            {LOCALES.map((l) => (
              <option key={l.code} value={l.code}>
                {l.flag} {l.label}
              </option>
            ))}
          </select>
        </div>

        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-brand-600 text-white font-bold text-2xl">
            $
          </div>
          <h1 className="mt-4 text-3xl font-semibold tracking-tight">LUCAS</h1>
          <p className="text-slate-500 mt-2">
            {t("app.tagline")} <span className="text-slate-400">{t("app.subtitle")}</span>
          </p>
        </div>

        <div className="card space-y-5">
          {/* Google — only rendered if NEXT_PUBLIC_GOOGLE_CLIENT_ID is set */}
          {GOOGLE_CLIENT_ID && (
            <>
              <div ref={googleBtnRef} className="flex justify-center" />
              <div className="flex items-center gap-3 text-xs text-slate-400">
                <span className="h-px flex-1 bg-slate-200" />
                or
                <span className="h-px flex-1 bg-slate-200" />
              </div>
            </>
          )}

          {/* Disabled social buttons — make it clear they're coming */}
          <div className="grid grid-cols-3 gap-2">
            <button
              className="btn-ghost border border-slate-200 opacity-50 cursor-not-allowed"
              disabled
              title="Coming soon"
            >
              Facebook
            </button>
            <button
              className="btn-ghost border border-slate-200 opacity-50 cursor-not-allowed"
              disabled
              title="Coming soon"
            >
              Apple
            </button>
            <button
              className="btn-ghost border border-slate-200 opacity-50 cursor-not-allowed"
              disabled
              title="Coming soon"
            >
              TikTok
            </button>
          </div>

          <div className="flex items-center gap-3 text-xs text-slate-400">
            <span className="h-px flex-1 bg-slate-200" />
            {t("auth.continueEmail")}
            <span className="h-px flex-1 bg-slate-200" />
          </div>

          {/* Quick vs password tabs */}
          <div className="flex text-sm rounded-xl bg-slate-100 p-1">
            <button
              type="button"
              className={`flex-1 py-2 rounded-lg ${mode === "quick" ? "bg-white shadow-soft" : "text-slate-500"}`}
              onClick={() => setMode("quick")}
            >
              {t("auth.quickEntry")}
            </button>
            <button
              type="button"
              className={`flex-1 py-2 rounded-lg ${mode === "password" ? "bg-white shadow-soft" : "text-slate-500"}`}
              onClick={() => setMode("password")}
            >
              {t("auth.emailPassword")}
            </button>
          </div>

          {mode === "quick" ? (
            <form className="space-y-3" onSubmit={handleQuick}>
              <input
                className="input"
                type="email"
                placeholder="tu@email.com"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoFocus
              />
              <button className="btn-primary w-full" disabled={loading}>
                {loading ? "…" : t("auth.enterInstantly")}
              </button>
              <p className="text-xs text-slate-500 text-center">{t("auth.noPassword")}</p>
            </form>
          ) : (
            <div className="space-y-3">
              <input
                className="input"
                type="email"
                placeholder={t("auth.email")}
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
              />
              <div className="relative">
                <input
                  className="input pr-10"
                  type={showPassword ? "text" : "password"}
                  placeholder={t("auth.password")}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  minLength={8}
                  required
                />
                <button
                  type="button"
                  tabIndex={-1}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600 text-base leading-none"
                  onClick={() => setShowPassword((v) => !v)}
                >
                  {showPassword ? "🙈" : "👁️"}
                </button>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <button
                  className="btn-ghost border border-slate-200"
                  onClick={(e) => handlePassword(e, "login")}
                  disabled={loading}
                >
                  {t("auth.login")}
                </button>
                <button
                  className="btn-primary"
                  onClick={(e) => handlePassword(e, "signup")}
                  disabled={loading}
                >
                  {t("auth.signup")}
                </button>
              </div>
            </div>
          )}

          {err && <p className="text-sm text-rose-600 text-center">{err}</p>}
        </div>

        <p className="text-xs text-slate-400 text-center mt-6">{t("auth.terms")}</p>
      </div>
    </div>
  );
}
