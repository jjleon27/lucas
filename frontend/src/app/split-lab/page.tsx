"use client";

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { getToken } from "@/lib/api";
import { Camera, ChevronLeft, Zap } from "lucide-react";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// SPLIT (laboratorio) — sección AISLADA para probar, desde cero, la forma
// más rápida de leer una boleta y sacar sus ítems, sin tocar el flujo real
// de "Dividir cuenta" (/split) que ya funciona. Ver
// backend/app/routers/split_lab.py para el porqué (2026-09-18: se midió que
// el modelo ya respondía rápido — el problema era que la app esperaba la
// respuesta COMPLETA antes de mostrar algo. Acá se muestra cada ítem apenas
// el modelo termina de escribirlo, vía streaming/SSE).
//
// Ámbito chico a propósito: solo nombre/cantidad/valor, en vivo, con
// cronómetro real visible. Nada de participantes/reparto/pago — eso se
// reincorpora recién cuando esto esté validado.

interface LabItem {
  idx: number;
  quantity: number;
  name: string;
  line_total: number;
  t: number;
}

export default function SplitLabPage() {
  const router = useRouter();
  const fileRef = useRef<HTMLInputElement>(null);
  const [items, setItems] = useState<LabItem[]>([]);
  const [running, setRunning] = useState(false);
  const [firstItemT, setFirstItemT] = useState<number | null>(null);
  const [totalT, setTotalT] = useState<number | null>(null);
  const [nowT, setNowT] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [imgPreview, setImgPreview] = useState<string | null>(null);
  const startRef = useRef<number>(0);
  const tickRef = useRef<ReturnType<typeof setInterval> | null>(null);

  function stopTick() {
    if (tickRef.current) { clearInterval(tickRef.current); tickRef.current = null; }
  }

  // Fotos de celular sin comprimir suelen pesar 5-10MB — con datos móviles
  // eso solo de SUBIR puede tardar más que el modelo entero, y el
  // cronómetro de esta página (arranca al elegir la foto, no cuando el
  // servidor recién la recibe) lo cuenta igual, aunque no sea culpa del
  // modelo. El backend igual reescala a ~2048px de lado largo antes de
  // mandarla a OpenAI (`_prep_receipt_image`) — subirla ya más chica no
  // pierde nada que el propio pipeline no fuera a descartar de todas
  // formas, y ahorra justo el tramo de subida que más varía con la red.
  function compressForUpload(file: File): Promise<File> {
    return new Promise((resolve) => {
      const img = new Image();
      const url = URL.createObjectURL(file);
      img.onload = () => {
        URL.revokeObjectURL(url);
        const MAX_SIDE = 2000;
        const scale = Math.min(1, MAX_SIDE / Math.max(img.naturalWidth, img.naturalHeight));
        const w = Math.round(img.naturalWidth * scale);
        const h = Math.round(img.naturalHeight * scale);
        const canvas = document.createElement("canvas");
        canvas.width = w; canvas.height = h;
        const ctx = canvas.getContext("2d");
        if (!ctx) { resolve(file); return; }
        ctx.drawImage(img, 0, 0, w, h);
        canvas.toBlob((blob) => {
          resolve(blob ? new File([blob], file.name.replace(/\.\w+$/, "") + ".jpg", { type: "image/jpeg" }) : file);
        }, "image/jpeg", 0.85);
      };
      img.onerror = () => { URL.revokeObjectURL(url); resolve(file); };
      img.src = url;
    });
  }

  async function onPickFile(e: React.ChangeEvent<HTMLInputElement>) {
    const rawFile = e.target.files?.[0];
    e.target.value = "";
    if (!rawFile) return;
    setImgPreview((prev) => { if (prev) URL.revokeObjectURL(prev); return URL.createObjectURL(rawFile); });
    setItems([]); setFirstItemT(null); setTotalT(null); setError(null); setNowT(0);
    setRunning(true);
    startRef.current = performance.now();
    tickRef.current = setInterval(() => setNowT((performance.now() - startRef.current) / 1000), 100);

    try {
      const file = await compressForUpload(rawFile);
      const token = getToken();
      const form = new FormData();
      form.append("file", file);
      const res = await fetch(`${API}/split-lab/ocr-stream`, {
        method: "POST",
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        body: form,
      });
      if (!res.ok || !res.body) {
        setError(`Error del servidor (${res.status})`);
        setRunning(false); stopTick();
        return;
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      // Parseo manual de SSE (no se puede usar EventSource nativo: necesita
      // POST + header Authorization, que EventSource no soporta).
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const parts = buf.split("\n\n");
        buf = parts.pop() ?? "";
        for (const part of parts) {
          const lines = part.split("\n");
          let event = "message";
          let data = "";
          for (const ln of lines) {
            if (ln.startsWith("event:")) event = ln.slice(6).trim();
            else if (ln.startsWith("data:")) data = ln.slice(5).trim();
          }
          if (!data) continue;
          const payload = JSON.parse(data);
          if (event === "item") {
            setItems((prev) => [...prev, payload as LabItem]);
            setFirstItemT((prev) => prev ?? payload.t);
          } else if (event === "done") {
            setTotalT(payload.total_t);
          } else if (event === "error") {
            setError(payload.message);
          }
        }
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Error de red");
    } finally {
      setRunning(false); stopTick();
    }
  }

  return (
    <div className="min-h-screen bg-slate-50">
      <div className="sticky top-0 z-10 bg-white border-b border-slate-200 px-4 pt-safe pt-4 pb-3">
        <div className="flex items-center gap-3 max-w-lg mx-auto">
          <button onClick={() => router.back()} className="text-slate-400 hover:text-slate-700">
            <ChevronLeft size={22} />
          </button>
          <h1 className="font-bold text-slate-800 flex-1 flex items-center gap-2">
            <Zap size={18} className="text-amber-500" /> SPLIT (laboratorio)
          </h1>
        </div>
      </div>

      <div className="max-w-lg mx-auto px-4 py-6 space-y-4">
        <div className="bg-amber-50 border border-amber-200 rounded-xl px-4 py-3 text-xs text-amber-800">
          Sección de pruebas — solo lee nombre/cantidad/valor de cada ítem,
          en vivo, para medir qué tan rápido se puede mostrar algo útil.
          Nada de esto se guarda todavía ni afecta tus boletas reales.
        </div>

        {/* Cronómetro real, siempre visible mientras corre */}
        <div className="bg-white rounded-2xl shadow-sm p-5 text-center">
          <p className="text-5xl font-mono font-bold text-slate-800 tabular-nums">
            {(totalT ?? nowT).toFixed(2)}s
          </p>
          <p className="text-xs text-slate-400 mt-1">
            {running ? "leyendo…" : totalT !== null ? "terminado" : "esperando foto"}
          </p>
          {firstItemT !== null && (
            <p className="text-sm text-emerald-600 font-medium mt-2">
              primer ítem visible a los {firstItemT.toFixed(2)}s
            </p>
          )}
        </div>

        {error && (
          <div className="bg-red-50 border border-red-200 rounded-xl px-4 py-3 text-sm text-red-700">{error}</div>
        )}

        {imgPreview && (
          <img src={imgPreview} alt="" className="w-full rounded-xl border border-slate-200 max-h-48 object-cover" />
        )}

        <button
          disabled={running}
          onClick={() => fileRef.current?.click()}
          className="w-full border-2 border-dashed border-indigo-300 rounded-2xl bg-white flex flex-col items-center justify-center py-10 gap-2 hover:border-indigo-500 transition-colors disabled:opacity-50"
        >
          <Camera size={32} className="text-indigo-400" />
          <span className="font-semibold text-slate-700">{items.length > 0 ? "Probar con otra foto" : "Subir boleta"}</span>
        </button>
        <input ref={fileRef} type="file" accept="image/*" className="hidden" onChange={onPickFile} />

        {/* Lista de ítems — aparecen uno por uno a medida que llegan */}
        {items.length > 0 && (
          <div className="bg-white rounded-2xl shadow-sm divide-y divide-slate-100">
            {items.map((it) => (
              <div key={it.idx} className="flex items-center justify-between px-4 py-2.5 text-sm animate-[fadeIn_.2s_ease]">
                <span className="text-slate-700">{it.quantity > 1 ? `${it.quantity}× ` : ""}{it.name}</span>
                <span className="flex items-center gap-2">
                  <span className="text-[10px] text-slate-300 font-mono">{it.t.toFixed(1)}s</span>
                  <span className="font-medium text-slate-600">${Math.round(it.line_total).toLocaleString("es-CL")}</span>
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
