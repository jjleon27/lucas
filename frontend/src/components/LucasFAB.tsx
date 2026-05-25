"use client";
/**
 * LucasFAB — floating "Talk to Lucas" button, visible on every page.
 *
 * Click the button → full-screen overlay opens.
 * Type or tap the mic → message sent to /chat/action.
 * Lucas replies in text; if he detects an intent (add expense, split bill,
 * go to upload…), an action card appears that the user can confirm in one tap.
 *
 * Intents handled:
 *   add_transaction  → pre-fills createTransaction, shows confirm card
 *   start_split      → navigates to /split with amount pre-filled
 *   navigate         → push to the URL Lucas suggests
 */
import {
  useEffect,
  useRef,
  useState,
  useCallback,
  FormEvent,
} from "react";
import { useRouter } from "next/navigation";
import {
  ActionOut,
  ChatMsg,
  Transaction,
  chatAction,
  createTransaction,
  getToken,
  listAccounts,
  Account,
  transcribeAudio,
} from "@/lib/api";
import { useT, formatMoney } from "@/lib/i18n";
import { Mic, X, SendHorizonal, Sparkles } from "lucide-react";

// ─── Types ────────────────────────────────────────────────────
interface ActionCard {
  type: "add_transaction" | "start_split" | "navigate";
  data: Record<string, unknown>;
  confirmed: boolean;
}

// ─── Helper: render Lucas's reply as markdown-lite ────────────
function Reply({ text }: { text: string }) {
  // Bold **x** and line breaks only — keep it fast
  const html = text
    .replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>")
    .replace(/\n/g, "<br/>");
  return (
    <div
      className="text-sm leading-relaxed"
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}

// ─── Transaction action card ──────────────────────────────────
function TransactionCard({
  data,
  accounts,
  onConfirm,
  onDismiss,
}: {
  data: Record<string, unknown>;
  accounts: Account[];
  onConfirm: (accountId: number | null) => void;
  onDismiss: () => void;
}) {
  const [accountId, setAccountId] = useState<number | null>(
    accounts[0]?.id ?? null,
  );
  const amount = Number(data.amount ?? 0);
  const currency = String(data.currency ?? "CLP");
  const merchant = String(data.merchant ?? "");
  const category = String(data.category ?? "");
  const isIncome = Boolean(data.is_income);

  return (
    <div className="rounded-2xl border-2 border-indigo-200 bg-indigo-50 p-4 space-y-3">
      <div className="flex items-start justify-between">
        <div>
          <p className="text-xs font-semibold text-indigo-500 uppercase tracking-wide mb-1">
            {isIncome ? "💰 Ingreso detectado" : "💸 Gasto detectado"}
          </p>
          <p className="text-xl font-bold text-indigo-900">
            {formatMoney(amount, currency)}
          </p>
          {merchant && (
            <p className="text-sm text-indigo-700 mt-0.5">{merchant}</p>
          )}
          {category && (
            <span className="inline-block mt-1 text-xs px-2 py-0.5 rounded-full bg-indigo-100 text-indigo-600">
              {category}
            </span>
          )}
        </div>
        <button onClick={onDismiss} className="text-indigo-300 hover:text-indigo-500">
          <X className="w-4 h-4" />
        </button>
      </div>

      {accounts.length > 0 && (
        <select
          className="input text-sm py-1.5"
          value={accountId ?? ""}
          onChange={(e) => setAccountId(e.target.value ? Number(e.target.value) : null)}
        >
          <option value="">— Sin cuenta —</option>
          {accounts.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name}
            </option>
          ))}
        </select>
      )}

      <button
        className="btn-primary w-full text-sm py-2"
        onClick={() => onConfirm(accountId)}
      >
        ✓ Guardar en Lucas
      </button>
    </div>
  );
}

// ─── Main FAB ──────────────────────────────────────────────────
export default function LucasFAB() {
  const router = useRouter();
  const { t } = useT();

  const [open, setOpen] = useState(false);
  const [input, setInput] = useState("");
  const [history, setHistory] = useState<ChatMsg[]>([]);
  const [thinking, setThinking] = useState(false);
  const [lastReply, setLastReply] = useState<ActionOut | null>(null);
  const [action, setAction] = useState<ActionCard | null>(null);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [listening, setListening] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);

  const inputRef = useRef<HTMLInputElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const recognitionRef = useRef<any>(null);
  const transcriptRef = useRef("");
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);

  // Load accounts once for the transaction confirm card
  useEffect(() => {
    if (open && getToken()) {
      listAccounts().then(setAccounts).catch(() => {});
    }
  }, [open]);

  // Focus input when overlay opens
  useEffect(() => {
    if (open) {
      setTimeout(() => inputRef.current?.focus(), 80);
    }
  }, [open]);

  // Scroll to bottom on new reply
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [lastReply, action]);

  // ── Voice ─────────────────────────────────────────────────
  async function toggleVoice() {
    const SR =
      (window as any).SpeechRecognition ||
      (window as any).webkitSpeechRecognition;
    if (!SR) {
      alert(t("chat.voiceUnsupported"));
      return;
    }
    if (listening) {
      recognitionRef.current?.stop();
      return;
    }
    transcriptRef.current = "";
    audioChunksRef.current = [];

    // ── MediaRecorder for Whisper ─────────────────────────────
    let mediaRecorder: MediaRecorder | null = null;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mimeType = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
        ? "audio/webm;codecs=opus"
        : MediaRecorder.isTypeSupported("audio/mp4") ? "audio/mp4" : "audio/webm";
      mediaRecorder = new MediaRecorder(stream, { mimeType });
      mediaRecorderRef.current = mediaRecorder;
      mediaRecorder.ondataavailable = (e) => {
        if (e.data.size > 0) audioChunksRef.current.push(e.data);
      };
      mediaRecorder.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        const blob = new Blob(audioChunksRef.current, { type: mimeType });
        const webText = transcriptRef.current.trim();
        try {
          const { transcript } = await transcribeAudio(blob);
          if (transcript) {
            setInput(transcript);
            if (inputRef.current) inputRef.current.value = transcript;
            handleSend(transcript);
            return;
          }
        } catch { /* fall through to web speech */ }
        if (webText) { setInput(webText); handleSend(webText); }
      };
      mediaRecorder.start();
    } catch { /* mic permission denied — Web Speech only */ }

    // ── Web Speech API for live preview ──────────────────────
    const rec = new SR();
    recognitionRef.current = rec;
    rec.lang = "es-CL";
    rec.interimResults = true;
    rec.continuous = false;

    const autoStop = setTimeout(() => rec.stop(), 7000);

    rec.onresult = (e: any) => {
      let final = "", interim = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        if (e.results[i].isFinal) final += e.results[i][0].transcript;
        else interim += e.results[i][0].transcript;
      }
      if (final) transcriptRef.current += final;
      const display = transcriptRef.current + (interim ? " " + interim : "");
      if (inputRef.current) inputRef.current.value = display;
      setInput(display);
    };

    rec.onerror = () => { clearTimeout(autoStop); setListening(false); };

    rec.onend = () => {
      clearTimeout(autoStop);
      setListening(false);
      // Stop MediaRecorder — its onstop will handle sending via Whisper
      if (mediaRecorderRef.current?.state === "recording") {
        mediaRecorderRef.current.stop();
      } else {
        // No MediaRecorder — send Web Speech result directly
        const text = transcriptRef.current.trim();
        if (text) { setInput(text); handleSend(text); }
      }
    };

    rec.start();
    setListening(true);
  }

  // ── Send ─────────────────────────────────────────────────
  const handleSend = useCallback(
    async (text?: string) => {
      const msg = (text ?? input).trim();
      if (!msg || thinking) return;
      if (!getToken()) return;

      setInput("");
      setAction(null);
      setSaved(null);
      setThinking(true);

      const newHistory: ChatMsg[] = [
        ...history,
        { role: "user", content: msg },
      ];
      setHistory(newHistory);

      try {
        const out = await chatAction(msg, history.slice(-6));
        setLastReply(out);
        setHistory((h) => [...h, { role: "assistant", content: out.reply }]);

        if (
          out.action_type &&
          out.action_type !== "null" &&
          out.action_data
        ) {
          setAction({
            type: out.action_type as ActionCard["type"],
            data: out.action_data,
            confirmed: false,
          });
        }
      } catch (e: any) {
        setLastReply({
          reply: "No pude conectarme. ¿Tienes internet?",
          action_type: null,
          action_data: null,
        });
      } finally {
        setThinking(false);
      }
    },
    [input, history, thinking],
  );

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    handleSend();
  }

  // ── Action: confirm add_transaction ──────────────────────
  async function confirmAddTransaction(accountId: number | null) {
    if (!action) return;
    const d = action.data;
    try {
      const tx = await createTransaction({
        amount: Number(d.amount ?? 0),
        currency: String(d.currency ?? "CLP"),
        category: String(d.category ?? "Uncategorized"),
        date: String(d.date ?? new Date().toISOString().slice(0, 10)),
        merchant: String(d.merchant ?? ""),
        notes: "",
        is_income: Boolean(d.is_income),
        account_id: accountId ?? undefined,
      } as any);
      setAction(null);
      setSaved(
        `✓ Guardado: ${formatMoney(tx.amount, tx.currency)} en ${tx.merchant || tx.category}`,
      );
    } catch (e: any) {
      alert("No se pudo guardar: " + e.message);
    }
  }

  // ── Action: navigate ─────────────────────────────────────
  function confirmNavigate(url: string) {
    setOpen(false);
    setAction(null);
    router.push(url);
  }

  // ── Action: start_split ──────────────────────────────────
  function confirmStartSplit(data: Record<string, unknown>) {
    setOpen(false);
    setAction(null);
    // Pass amount via query param for the split page to pre-fill
    const params = new URLSearchParams();
    if (data.amount) params.set("amount", String(data.amount));
    if (data.merchant) params.set("merchant", String(data.merchant));
    if (data.currency) params.set("currency", String(data.currency));
    router.push(`/split?${params.toString()}`);
  }

  // ─── Render ─────────────────────────────────────────────
  return (
    <>
      {/* ── FAB button ── */}
      <button
        onClick={() => setOpen(true)}
        aria-label={t("fab.label")}
        className={`
          fixed bottom-24 right-5 z-50
          w-14 h-14 rounded-full shadow-lg
          flex items-center justify-center
          bg-gradient-to-br from-indigo-500 to-violet-600
          text-white
          hover:scale-105 active:scale-95 transition-transform
          md:bottom-8 md:right-8
        `}
      >
        <Sparkles className="w-6 h-6" />
      </button>

      {/* ── Overlay ── */}
      {open && (
        <div
          className="fixed inset-0 z-50 flex flex-col bg-white/95 backdrop-blur-sm"
          style={{ paddingBottom: "env(safe-area-inset-bottom)" }}
        >
          {/* Header */}
          <div className="flex items-center justify-between px-5 py-4 border-b border-slate-100 shrink-0">
            <div className="flex items-center gap-2">
              <span className="w-8 h-8 rounded-full bg-gradient-to-br from-indigo-500 to-violet-600 flex items-center justify-center">
                <Sparkles className="w-4 h-4 text-white" />
              </span>
              <span className="font-semibold text-slate-800">Lucas</span>
            </div>
            <button
              onClick={() => setOpen(false)}
              className="text-slate-400 hover:text-slate-600 p-1"
            >
              <X className="w-5 h-5" />
            </button>
          </div>

          {/* Body */}
          <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
            {/* Welcome / hint */}
            {history.length === 0 && (
              <div className="text-center space-y-3 py-6">
                <div className="w-16 h-16 rounded-full bg-gradient-to-br from-indigo-100 to-violet-100 flex items-center justify-center mx-auto">
                  <Sparkles className="w-8 h-8 text-indigo-500" />
                </div>
                <p className="text-slate-600 text-sm max-w-xs mx-auto leading-relaxed">
                  {t("fab.hint")}
                </p>
                {/* Quick-action chips */}
                <div className="flex flex-wrap justify-center gap-2 mt-2">
                  {[
                    "¿Cuánto llevo gastado este mes?",
                    "Gasté $8.000 en almuerzo",
                    "Divide $20.000 entre 3 personas",
                    "Subir boleta",
                  ].map((q) => (
                    <button
                      key={q}
                      onClick={() => handleSend(q)}
                      className="text-xs px-3 py-1.5 rounded-full border border-indigo-200 bg-indigo-50 text-indigo-700 hover:bg-indigo-100 transition"
                    >
                      {q}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* Conversation */}
            {history.map((msg, i) => (
              <div
                key={i}
                className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
              >
                {msg.role === "assistant" && (
                  <span className="w-6 h-6 rounded-full bg-gradient-to-br from-indigo-500 to-violet-600 flex items-center justify-center shrink-0 mr-2 mt-0.5">
                    <Sparkles className="w-3 h-3 text-white" />
                  </span>
                )}
                <div
                  className={`max-w-[82%] px-4 py-2.5 rounded-2xl text-sm ${
                    msg.role === "user"
                      ? "bg-indigo-600 text-white rounded-br-sm"
                      : "bg-slate-100 text-slate-800 rounded-bl-sm"
                  }`}
                >
                  {msg.role === "assistant" ? (
                    <Reply text={msg.content} />
                  ) : (
                    msg.content
                  )}
                </div>
              </div>
            ))}

            {/* Thinking */}
            {thinking && (
              <div className="flex items-center gap-2 text-slate-400 text-sm">
                <span className="w-6 h-6 rounded-full bg-gradient-to-br from-indigo-500 to-violet-600 flex items-center justify-center shrink-0">
                  <Sparkles className="w-3 h-3 text-white" />
                </span>
                <span className="flex gap-1">
                  <span className="w-1.5 h-1.5 bg-slate-400 rounded-full animate-bounce" style={{ animationDelay: "0ms" }} />
                  <span className="w-1.5 h-1.5 bg-slate-400 rounded-full animate-bounce" style={{ animationDelay: "150ms" }} />
                  <span className="w-1.5 h-1.5 bg-slate-400 rounded-full animate-bounce" style={{ animationDelay: "300ms" }} />
                </span>
              </div>
            )}

            {/* Action card */}
            {action && !action.confirmed && (
              <div>
                {action.type === "add_transaction" && (
                  <TransactionCard
                    data={action.data}
                    accounts={accounts}
                    onConfirm={confirmAddTransaction}
                    onDismiss={() => setAction(null)}
                  />
                )}
                {action.type === "navigate" && (
                  <div className="rounded-2xl border-2 border-indigo-200 bg-indigo-50 p-4 flex items-center justify-between">
                    <p className="text-sm text-indigo-700 font-medium">
                      Ir a {String(action.data.url)}?
                    </p>
                    <div className="flex gap-2">
                      <button
                        className="btn-primary text-sm px-4 py-1.5"
                        onClick={() => confirmNavigate(String(action.data.url))}
                      >
                        Ir →
                      </button>
                      <button
                        className="btn-ghost text-sm px-3 py-1.5"
                        onClick={() => setAction(null)}
                      >
                        <X className="w-4 h-4" />
                      </button>
                    </div>
                  </div>
                )}
                {action.type === "start_split" && (
                  <div className="rounded-2xl border-2 border-indigo-200 bg-indigo-50 p-4 space-y-2">
                    <p className="text-sm font-semibold text-indigo-700">
                      💳 Dividir{" "}
                      {formatMoney(
                        Number(action.data.amount ?? 0),
                        String(action.data.currency ?? "CLP"),
                      )}
                      {action.data.merchant
                        ? ` en ${action.data.merchant}`
                        : ""}
                    </p>
                    <div className="flex gap-2">
                      <button
                        className="btn-primary flex-1 text-sm py-2"
                        onClick={() => confirmStartSplit(action.data)}
                      >
                        Ir a dividir →
                      </button>
                      <button
                        className="btn-ghost px-3 text-sm"
                        onClick={() => setAction(null)}
                      >
                        <X className="w-4 h-4" />
                      </button>
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* Saved confirmation */}
            {saved && (
              <div className="text-sm text-emerald-600 bg-emerald-50 rounded-2xl px-4 py-2.5 font-medium">
                {saved}
              </div>
            )}

            <div ref={bottomRef} />
          </div>

          {/* Input bar */}
          <form
            onSubmit={onSubmit}
            className="shrink-0 border-t border-slate-100 px-4 py-3 flex items-center gap-2 bg-white"
          >
            <button
              type="button"
              onClick={toggleVoice}
              title={listening ? "Toca para detener" : "Hablar"}
              className={`p-2.5 rounded-full transition-all ${
                listening
                  ? "bg-rose-500 text-white scale-110 shadow-lg ring-4 ring-rose-200"
                  : "bg-slate-100 text-slate-500 hover:bg-slate-200"
              }`}
            >
              {/* Always show Mic — red pulsing ring indicates recording. MicOff means "disabled", not "recording". */}
              <Mic className={`w-5 h-5 ${listening ? "animate-pulse" : ""}`} />
            </button>
            <input
              ref={inputRef}
              className="flex-1 bg-slate-50 rounded-full px-4 py-2.5 text-sm outline-none focus:ring-2 ring-indigo-300 transition"
              placeholder={listening ? t("fab.listening") : t("fab.placeholder")}
              value={input}
              onChange={(e) => !listening && setInput(e.target.value)}
            />
            <button
              type="submit"
              disabled={!input.trim() || thinking}
              className="p-2.5 rounded-full bg-indigo-600 text-white disabled:opacity-40 hover:bg-indigo-700 transition"
            >
              <SendHorizonal className="w-5 h-5" />
            </button>
          </form>
        </div>
      )}
    </>
  );
}
