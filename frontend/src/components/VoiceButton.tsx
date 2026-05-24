"use client";

/**
 * Floating mic button → voice-to-transaction.
 *
 * Flow:
 *  1. User taps the mic. We spin up the browser's SpeechRecognition
 *     (Web Speech API). No server call for transcription — it runs in the
 *     browser, so it's free and private.
 *  2. User says something like "gasté 8 lucas en el Jumbo con el débito".
 *  3. We POST the transcript to /voice/parse. The LLM returns a structured
 *     transaction.
 *  4. We render a confirmation card. User hits "Guardar" → we POST to
 *     /transactions and refresh the dashboard.
 *
 * Browser support: Chrome / Edge / Safari (with webkit prefix). Firefox
 * doesn't support SpeechRecognition — we hide the button if the API isn't
 * available.
 */
import { useEffect, useRef, useState } from "react";
import { Mic, Square, Loader2, Check, X } from "lucide-react";
import {
  parseVoice,
  createTransaction,
  listAccounts,
  type VoiceParsed,
  type Account,
} from "@/lib/api";

type State = "idle" | "listening" | "thinking" | "confirm" | "saving" | "done";

export default function VoiceButton({ onSaved }: { onSaved?: () => void }) {
  const [state, setState] = useState<State>("idle");
  const [transcript, setTranscript] = useState("");
  const [result, setResult] = useState<VoiceParsed | null>(null);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [pickedAccount, setPickedAccount] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const recognitionRef = useRef<any>(null);
  const supported = useRef<boolean>(false);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const SR =
      (window as any).SpeechRecognition ||
      (window as any).webkitSpeechRecognition;
    supported.current = !!SR;
  }, []);

  useEffect(() => {
    if (state === "confirm") {
      listAccounts().then((a) => {
        setAccounts(a);
        if (result?.suggested_account_id) {
          setPickedAccount(result.suggested_account_id);
        } else if (a.length > 0) {
          setPickedAccount(a[0].id);
        }
      });
    }
  }, [state, result]);

  function start() {
    if (!supported.current) {
      setError("Tu navegador no soporta dictado de voz. Probá Chrome o Safari.");
      return;
    }
    setError(null);
    setTranscript("");
    setResult(null);
    const SR =
      (window as any).SpeechRecognition ||
      (window as any).webkitSpeechRecognition;
    const rec = new SR();
    rec.lang = "es-CL";
    rec.continuous = false;
    rec.interimResults = true;

    let finalText = "";
    rec.onresult = (e: any) => {
      let interim = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const r = e.results[i];
        if (r.isFinal) finalText += r[0].transcript;
        else interim += r[0].transcript;
      }
      setTranscript((finalText + " " + interim).trim());
    };
    rec.onerror = (e: any) => {
      setError(e?.error || "No pude escuchar.");
      setState("idle");
    };
    rec.onend = () => {
      const text = (finalText || transcript || "").trim();
      if (!text) {
        setState("idle");
        return;
      }
      setTranscript(text);
      submit(text);
    };
    recognitionRef.current = rec;
    rec.start();
    setState("listening");
  }

  function stop() {
    try {
      recognitionRef.current?.stop();
    } catch {
      /* ignore */
    }
  }

  async function submit(text: string) {
    setState("thinking");
    try {
      const today = new Date().toISOString().slice(0, 10);
      const parsed = await parseVoice(text, today);
      setResult(parsed);
      if (parsed.action === "unclear") {
        // Show the clarification as an error so user can retry.
        setError(parsed.clarification || "No entendí. ¿Podés repetir?");
        setState("idle");
        return;
      }
      setState("confirm");
    } catch (e: any) {
      setError(e?.message || "Falló el parseo");
      setState("idle");
    }
  }

  async function save() {
    if (!result) return;
    setState("saving");
    try {
      await createTransaction({
        amount: Math.abs(result.amount),
        currency: result.currency,
        category: result.category,
        date: result.date,
        merchant: result.merchant,
        notes: `Voz: "${result.transcript}"`,
        is_income: result.is_income,
        account_id: pickedAccount,
      });
      setState("done");
      setTimeout(() => {
        setState("idle");
        setResult(null);
        setTranscript("");
        onSaved?.();
      }, 1200);
    } catch (e: any) {
      setError(e?.message || "No se pudo guardar");
      setState("confirm");
    }
  }

  function cancel() {
    setState("idle");
    setResult(null);
    setTranscript("");
    setError(null);
  }

  if (!supported.current && typeof window !== "undefined") {
    // Render the button, but show the error when clicked.
  }

  const fmt = (n: number, cur: string) =>
    cur === "CLP"
      ? `$${Math.round(n).toLocaleString("es-CL")}`
      : `${cur} ${n.toLocaleString("en-US", { maximumFractionDigits: 2 })}`;

  return (
    <>
      {/* Floating mic button */}
      <button
        onClick={state === "listening" ? stop : start}
        aria-label="Dictar un gasto"
        className={`fixed z-50 bottom-24 right-5 md:bottom-8 md:right-8 w-14 h-14 rounded-full shadow-lg flex items-center justify-center transition ${
          state === "listening"
            ? "bg-red-500 text-white animate-pulse"
            : "bg-brand-600 text-white hover:bg-brand-700"
        }`}
      >
        {state === "listening" ? <Square className="w-6 h-6" /> : <Mic className="w-6 h-6" />}
      </button>

      {/* Listening / thinking overlay */}
      {(state === "listening" || state === "thinking") && (
        <div className="fixed inset-0 bg-black/30 z-40 flex items-end md:items-center justify-center p-4">
          <div className="bg-white rounded-2xl p-5 w-full max-w-md shadow-xl">
            <div className="flex items-center gap-3 mb-3">
              {state === "listening" ? (
                <>
                  <span className="w-3 h-3 rounded-full bg-red-500 animate-pulse" />
                  <span className="text-sm text-slate-600">Escuchando…</span>
                </>
              ) : (
                <>
                  <Loader2 className="w-4 h-4 animate-spin text-brand-600" />
                  <span className="text-sm text-slate-600">Pensando…</span>
                </>
              )}
            </div>
            <p className="text-slate-800 min-h-[2rem]">
              {transcript || <span className="text-slate-400">Dí algo como “gasté 8 lucas en el Jumbo con el débito”</span>}
            </p>
            {state === "listening" && (
              <button
                onClick={stop}
                className="mt-4 px-3 py-1.5 text-sm rounded-lg bg-slate-100 hover:bg-slate-200"
              >
                Listo
              </button>
            )}
          </div>
        </div>
      )}

      {/* Confirmation card */}
      {state === "confirm" && result && (
        <div className="fixed inset-0 bg-black/40 z-40 flex items-end md:items-center justify-center p-4">
          <div className="bg-white rounded-2xl p-5 w-full max-w-md shadow-xl">
            <h3 className="text-lg font-semibold mb-1">
              {result.is_income ? "¿Confirmás este ingreso?" : "¿Confirmás este gasto?"}
            </h3>
            <p className="text-xs text-slate-500 mb-4">
              Dictaste: <span className="italic">“{result.transcript}”</span>
            </p>

            <div className="space-y-3">
              <Row label="Monto">
                <span className="text-xl font-semibold">
                  {fmt(result.amount, result.currency)}
                </span>
              </Row>
              <Row label="Comercio">
                <input
                  value={result.merchant}
                  onChange={(e) => setResult({ ...result, merchant: e.target.value })}
                  className="w-full border border-slate-200 rounded-lg px-2 py-1 text-sm"
                />
              </Row>
              <Row label="Categoría">
                <span>{result.category}</span>
              </Row>
              <Row label="Fecha">
                <input
                  type="date"
                  value={result.date}
                  onChange={(e) => setResult({ ...result, date: e.target.value })}
                  className="border border-slate-200 rounded-lg px-2 py-1 text-sm"
                />
              </Row>
              <Row label="Cuenta">
                <select
                  value={pickedAccount ?? ""}
                  onChange={(e) =>
                    setPickedAccount(e.target.value ? Number(e.target.value) : null)
                  }
                  className="border border-slate-200 rounded-lg px-2 py-1 text-sm"
                >
                  <option value="">(sin cuenta)</option>
                  {accounts.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.name}
                    </option>
                  ))}
                </select>
              </Row>
            </div>

            {result.confidence < 0.6 && (
              <p className="mt-3 text-xs text-amber-600">
                No estoy muy seguro de lo que escuché — revisá antes de guardar.
              </p>
            )}

            <div className="mt-5 flex gap-2 justify-end">
              <button
                onClick={cancel}
                className="px-3 py-2 text-sm rounded-lg bg-slate-100 hover:bg-slate-200 flex items-center gap-1"
              >
                <X className="w-4 h-4" /> Cancelar
              </button>
              <button
                onClick={save}
                disabled={!result.amount}
                className="px-3 py-2 text-sm rounded-lg bg-brand-600 text-white hover:bg-brand-700 flex items-center gap-1 disabled:opacity-50"
              >
                <Check className="w-4 h-4" /> Guardar
              </button>
            </div>
          </div>
        </div>
      )}

      {state === "done" && (
        <div className="fixed bottom-28 right-5 md:bottom-28 md:right-8 z-50 bg-green-600 text-white px-4 py-2 rounded-lg shadow">
          Guardado ✓
        </div>
      )}

      {error && state === "idle" && (
        <div className="fixed bottom-44 right-5 md:right-8 z-50 bg-red-50 border border-red-200 text-red-700 text-sm px-3 py-2 rounded-lg shadow max-w-xs">
          {error}
          <button
            onClick={() => setError(null)}
            className="ml-2 text-red-500 hover:text-red-700"
          >
            ×
          </button>
        </div>
      )}
    </>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="text-xs uppercase tracking-wide text-slate-500 w-24">{label}</span>
      <div className="flex-1 text-right">{children}</div>
    </div>
  );
}
