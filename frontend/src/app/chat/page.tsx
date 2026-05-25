"use client";
/**
 * Hablar con Lucas — escribe o habla. Voz usa Web Speech API.
 * Funciona en iOS Safari 14+, Chrome (desktop/Android) y Edge.
 */
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Send, Mic } from "lucide-react";
import { chat, ChatMsg, getToken } from "@/lib/api";
import { useT } from "@/lib/i18n";

export default function ChatPage() {
  const router = useRouter();
  const { t } = useT();
  const [messages, setMessages] = useState<ChatMsg[]>([
    {
      role: "assistant",
      content:
        "Hola, soy Lucas 👋 Pregúntame sobre tus finanzas — ej. «¿cuánto gasté en comida este mes?» o «¿voy bien con el presupuesto?»",
    },
  ]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [listening, setListening] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const recRef = useRef<any>(null);

  useEffect(() => {
    if (!getToken()) router.replace("/");
  }, [router]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function send() {
    const text = input.trim();
    if (!text || sending) return;
    const history = messages.filter((m) => m.role === "user" || m.role === "assistant");
    setMessages((m) => [...m, { role: "user", content: text }]);
    setInput("");
    setSending(true);
    try {
      const { reply } = await chat(text, history);
      setMessages((m) => [...m, { role: "assistant", content: reply }]);
    } catch (e: any) {
      setMessages((m) => [
        ...m,
        {
          role: "assistant",
          content: `Lo siento, no pude conectarme. ${e.message || "Inténtalo de nuevo."}`,
        },
      ]);
    } finally {
      setSending(false);
    }
  }

  function toggleVoice() {
    const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SR) {
      alert(t("chat.voiceUnsupported"));
      return;
    }
    if (listening) {
      recRef.current?.stop();
      setListening(false);
      return;
    }
    const rec = new SR();
    rec.continuous = false;
    rec.interimResults = false;
    rec.lang = "es-CL"; // Siempre español chileno
    rec.onresult = (e: any) => {
      const text = e.results[0][0].transcript;
      setInput((prev) => (prev ? `${prev} ${text}` : text));
    };
    rec.onend = () => setListening(false);
    rec.onerror = () => setListening(false);
    recRef.current = rec;
    rec.start();
    setListening(true);
  }

  return (
    <div className="max-w-3xl mx-auto flex flex-col h-[calc(100dvh-4rem)] pb-20 md:pb-0 md:h-[calc(100vh-6rem)]">
      <h1 className="text-3xl font-semibold tracking-tight mb-4">{t("chat.title")}</h1>

      <div className="card flex-1 overflow-y-auto space-y-3">
        {messages.map((m, i) => (
          <div
            key={i}
            className={`max-w-[85%] rounded-2xl px-4 py-2.5 text-sm whitespace-pre-wrap ${
              m.role === "user"
                ? "ml-auto bg-brand-600 text-white"
                : "mr-auto bg-slate-100 text-slate-900"
            }`}
          >
            {m.content}
          </div>
        ))}
        {sending && (
          <div className="mr-auto text-xs text-slate-400">{t("chat.thinking")}</div>
        )}
        <div ref={bottomRef} />
      </div>

      <div className="mt-4 flex gap-2">
        <button
          onClick={toggleVoice}
          title={listening ? "Detener" : "Hablar"}
          className={`btn-ghost transition-all ${
            listening
              ? "bg-rose-50 text-rose-600 ring-2 ring-rose-200 scale-105"
              : ""
          }`}
        >
          {/* Siempre Mic — el ring rojo indica grabación (MicOff = desactivado, no grabando) */}
          <Mic className={`w-4 h-4 ${listening ? "animate-pulse" : ""}`} />
        </button>
        <input
          className="input"
          placeholder={listening ? t("fab.listening") : t("chat.placeholder")}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
        />
        <button className="btn-primary" onClick={send} disabled={sending || !input.trim()}>
          <Send className="w-4 h-4" />
        </button>
      </div>
    </div>
  );
}
