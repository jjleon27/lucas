"use client";
/**
 * Split — 6-step bill-splitting flow
 * 1 Capture → 2 Items review → 3 Participants → 4 Assign → 5 Who paid → 6 Summary
 */
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Account, Person, listAccounts, listPeople, getToken, resolveBackendUrl } from "@/lib/api";
import { Camera, Plus, Trash2, Pencil, Check, ChevronRight, ChevronLeft, Share2, Users } from "lucide-react";

// ─── Types ────────────────────────────────────────────────────────────────────

interface BillParticipant { id: number; person_id: number; name: string; color: string; is_me: boolean; paid_amount: number; owes_amount: number; }
interface BillItemShare { participant_id: number; weight: number; units: number | null; }
interface BillItem { id: number; name: string; qty: number; unit_price: number; line_total: number; shares: BillItemShare[]; }
interface Bill {
  id: number; merchant: string; date: string; total_amount: number; tip_amount: number;
  currency: string; image_url: string; status: "draft" | "assigned" | "finalized";
  transaction_id: number | null; public_token: string | null;
  participants: BillParticipant[]; items: BillItem[];
}

// ─── API helpers ──────────────────────────────────────────────────────────────

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function billReq<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(`${API}${path}`, { ...init, headers });
  if (!res.ok) { const t = await res.text().catch(() => ""); throw new Error(`${res.status}: ${t}`); }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

const createBill = (opts?: { merchant?: string; date?: string }) =>
  billReq<Bill>("/bills", { method: "POST", body: JSON.stringify(opts ?? {}) });
const ocrBill = async (id: number, file: File): Promise<Bill> => {
  const form = new FormData(); form.append("file", file);
  const token = getToken();
  const res = await fetch(`${API}/bills/${id}/ocr`, { method: "POST", headers: token ? { Authorization: `Bearer ${token}` } : {}, body: form });
  if (!res.ok) throw new Error(`OCR ${res.status}: ${await res.text()}`);
  return res.json();
};
const addParticipant = (billId: number, personId: number) =>
  billReq<Bill>(`/bills/${billId}/participants`, { method: "POST", body: JSON.stringify({ person_id: personId }) });
const removeParticipant = (billId: number, pid: number) =>
  billReq<Bill>(`/bills/${billId}/participants/${pid}`, { method: "DELETE" });
const addItem = (billId: number, item: { name: string; qty: number; unit_price: number }) =>
  billReq<Bill>(`/bills/${billId}/items`, { method: "POST", body: JSON.stringify(item) });
const patchItem = (billId: number, iid: number, patch: { name?: string; qty?: number; unit_price?: number }) =>
  billReq<Bill>(`/bills/${billId}/items/${iid}`, { method: "PATCH", body: JSON.stringify(patch) });
const deleteItem = (billId: number, iid: number) =>
  billReq<Bill>(`/bills/${billId}/items/${iid}`, { method: "DELETE" });
const postShares = (billId: number, itemId: number, shares: { participant_id: number; weight: number; units?: number }[]) =>
  billReq<Bill>(`/bills/${billId}/shares`, { method: "POST", body: JSON.stringify({ item_id: itemId, shares }) });
const assignEqual = (billId: number) =>
  billReq<Bill>(`/bills/${billId}/assign-equal`, { method: "POST" });
const setPayers = (billId: number, payers: { participant_id: number; paid_amount: number }[]) =>
  billReq<Bill>(`/bills/${billId}/set-payers`, { method: "POST", body: JSON.stringify(payers) });
const finalizeBill = (billId: number, opts: { account_id?: number; category?: string }) =>
  billReq<Bill>(`/bills/${billId}/finalize`, { method: "POST", body: JSON.stringify(opts) });

// ─── Utilities ────────────────────────────────────────────────────────────────

const clp = (n: number) => "$" + Math.round(n).toLocaleString("es-CL");
const initials = (name: string) => name.split(" ").map((w) => w[0]).join("").slice(0, 2).toUpperCase();

function StepDots({ step }: { step: number }) {
  return (
    <div className="flex items-center justify-center gap-2 mb-4">
      {[1,2,3,4,5,6].map((s) => (
        <div key={s} className={`rounded-full transition-all ${s === step ? "w-6 h-2.5 bg-indigo-600" : s < step ? "w-2.5 h-2.5 bg-emerald-500" : "w-2.5 h-2.5 bg-slate-300"}`} />
      ))}
    </div>
  );
}

function Toast({ msg, onClose }: { msg: string; onClose: () => void }) {
  useEffect(() => { const t = setTimeout(onClose, 3000); return () => clearTimeout(t); }, [msg, onClose]);
  return <div className="fixed bottom-24 left-1/2 -translate-x-1/2 z-50 bg-red-600 text-white text-sm px-4 py-2 rounded-xl shadow-lg max-w-xs text-center">{msg}</div>;
}

function Avatar({ name, color, selected, onClick, locked }: { name: string; color: string; selected: boolean; onClick?: () => void; locked?: boolean; }) {
  return (
    <button type="button" onClick={locked ? undefined : onClick} className={`flex flex-col items-center gap-1 ${locked ? "opacity-60 cursor-default" : "cursor-pointer"}`}>
      <div className={`w-11 h-11 rounded-full flex items-center justify-center text-white font-bold text-sm ring-2 transition-all ${selected ? "ring-indigo-500 ring-offset-1" : "ring-transparent"}`} style={{ background: color }}>
        {initials(name)}
      </div>
      <span className="text-[10px] text-slate-600 max-w-[48px] truncate">{name.split(" ")[0]}</span>
    </button>
  );
}

// ─── Main page ────────────────────────────────────────────────────────────────

export default function SplitPage() {
  const router = useRouter();
  const fileRef = useRef<HTMLInputElement>(null);
  const [step, setStep] = useState(1);
  const [bill, setBill] = useState<Bill | null>(null);
  const [people, setPeople] = useState<Person[]>([]);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Step 2 — items
  const [editItemId, setEditItemId] = useState<number | null>(null);
  const [editDraft, setEditDraft] = useState({ name: "", qty: 1, unit_price: 0 });
  const [newItem, setNewItem] = useState<{ name: string; qty: number; unit_price: number } | null>(null);

  // Step 4 — assign: itemId → participantId → units
  const assigns = useRef<Map<number, Map<number, number>>>(new Map());
  const [, setAssignsV] = useState(0);
  const bump = () => setAssignsV((v) => v + 1);
  const [activeParticipantId, setActiveParticipantId] = useState<number | null>(null);
  const [shakeItemId, setShakeItemId] = useState<number | null>(null);
  const [bottomSheetItemId, setBottomSheetItemId] = useState<number | null>(null);
  const [bottomSheetDraft, setBottomSheetDraft] = useState<Record<number, number>>({});
  const [expandedItemId, setExpandedItemId] = useState<number | null>(null);

  // Step 5 — payers
  const [payerMode, setPayerMode] = useState<"me" | "other" | "split">("me");
  const [otherPayer, setOtherPayer] = useState<number | null>(null);
  const [multiAmounts, setMultiAmounts] = useState<Record<number, string>>({});
  const [selectedAccountId, setSelectedAccountId] = useState<number | null>(null);

  // Step 6 — summary
  const [finalized, setFinalized] = useState(false);
  const [myShare, setMyShare] = useState(0);

  const showError = (msg: string) => setError(msg);

  useEffect(() => {
    listPeople().then(setPeople).catch(() => {});
    listAccounts().then(setAccounts).catch(() => {});
  }, []);

  // ── Step 1 ────────────────────────────────────────────────────

  async function handleFile(file: File) {
    setLoading(true);
    try {
      const today = new Date().toISOString().split("T")[0];
      let b = await createBill({ date: today });
      b = await ocrBill(b.id, file);
      setBill(b); setStep(2);
    } catch (e: unknown) { showError(e instanceof Error ? e.message : "Error al leer boleta"); }
    finally { setLoading(false); }
  }

  async function handleManual() {
    setLoading(true);
    try {
      const b = await createBill({ date: new Date().toISOString().split("T")[0] });
      setBill(b); setStep(2);
    } catch (e: unknown) { showError(e instanceof Error ? e.message : "Error"); }
    finally { setLoading(false); }
  }

  // ── Step 2 ────────────────────────────────────────────────────

  const subtotal = bill ? bill.items.reduce((s, i) => s + i.line_total, 0) : 0;

  async function saveEditItem() {
    if (!bill || editItemId === null) return;
    try { const b = await patchItem(bill.id, editItemId, editDraft); setBill(b); setEditItemId(null); }
    catch (e: unknown) { showError(e instanceof Error ? e.message : "Error"); }
  }
  async function handleDeleteItem(iid: number) {
    if (!bill) return;
    try { setBill(await deleteItem(bill.id, iid)); }
    catch (e: unknown) { showError(e instanceof Error ? e.message : "Error"); }
  }
  async function handleAddItem() {
    if (!bill || !newItem?.name.trim()) return;
    try { setBill(await addItem(bill.id, newItem)); setNewItem(null); }
    catch (e: unknown) { showError(e instanceof Error ? e.message : "Error"); }
  }

  // ── Step 3 ────────────────────────────────────────────────────

  async function toggleParticipant(pid: number) {
    if (!bill) return;
    const existing = bill.participants.find((p) => p.person_id === pid);
    try {
      if (existing) { if (existing.is_me) return; setBill(await removeParticipant(bill.id, existing.id)); }
      else { setBill(await addParticipant(bill.id, pid)); }
    } catch (e: unknown) { showError(e instanceof Error ? e.message : "Error"); }
  }

  // ── Step 4 ────────────────────────────────────────────────────

  const getUnits = (itemId: number, pid: number) => assigns.current.get(itemId)?.get(pid) ?? 0;

  const unitsAssigned = (item: BillItem) => {
    const inner = assigns.current.get(item.id);
    if (!inner) return 0;
    let t = 0; for (const v of inner.values()) t += v; return t;
  };
  const unitsRemaining = (item: BillItem) => item.qty - unitsAssigned(item);

  const runningTotal = (pid: number) =>
    bill ? bill.items.reduce((s, item) => s + getUnits(item.id, pid) * item.unit_price, 0) : 0;

  function setUnits(itemId: number, pid: number, units: number) {
    const map = assigns.current;
    if (!map.has(itemId)) map.set(itemId, new Map());
    const inner = map.get(itemId)!;
    if (units <= 0) inner.delete(pid); else inner.set(pid, units);
    bump();
  }

  function seedAssigns(b: Bill) {
    const map = new Map<number, Map<number, number>>();
    for (const item of b.items) {
      const inner = new Map<number, number>();
      for (const s of item.shares) {
        const u = s.units != null ? s.units : Math.round(s.weight * item.qty);
        if (u > 0) inner.set(s.participant_id, u);
      }
      if (inner.size > 0) map.set(item.id, inner);
    }
    assigns.current = map; bump();
  }

  function goToAssign() {
    if (bill) { seedAssigns(bill); const me = bill.participants.find((p) => p.is_me); if (me) setActiveParticipantId(me.id); }
    setStep(4);
  }

  function tapItem(item: BillItem) {
    if (!activeParticipantId) { showError("Selecciona un participante primero"); return; }
    if (unitsRemaining(item) <= 0) {
      setShakeItemId(item.id); setTimeout(() => setShakeItemId(null), 500); return;
    }
    setUnits(item.id, activeParticipantId, getUnits(item.id, activeParticipantId) + 1);
  }

  function tapTodos(item: BillItem) {
    if (!bill) return;
    const n = bill.participants.length;
    if (n === 0) return;
    const base = Math.floor(item.qty / n), rem = item.qty % n;
    const inner = new Map<number, number>();
    bill.participants.forEach((p, i) => { const u = base + (i < rem ? 1 : 0); if (u > 0) inner.set(p.id, u); });
    assigns.current.set(item.id, inner); bump();
  }

  function openBottomSheet(item: BillItem) {
    if (!bill) return;
    const draft: Record<number, number> = {};
    bill.participants.forEach((p) => { draft[p.id] = getUnits(item.id, p.id); });
    setBottomSheetDraft(draft); setBottomSheetItemId(item.id);
  }

  function confirmBottomSheet() {
    if (bottomSheetItemId === null) return;
    const inner = new Map<number, number>();
    Object.entries(bottomSheetDraft).forEach(([pid, u]) => { if (u > 0) inner.set(Number(pid), u); });
    assigns.current.set(bottomSheetItemId, inner); bump(); setBottomSheetItemId(null);
  }

  async function handleAssignEqual() {
    if (!bill) return;
    try { const b = await assignEqual(bill.id); setBill(b); seedAssigns(b); }
    catch (e: unknown) { showError(e instanceof Error ? e.message : "Error"); }
  }

  async function goToWhoPaid() {
    if (!bill) return;
    let b = bill;
    for (const item of bill.items) {
      const inner = assigns.current.get(item.id);
      if (!inner || inner.size === 0) continue;
      const shares = Array.from(inner.entries()).map(([pid, units]) => ({ participant_id: pid, units, weight: units / item.qty }));
      try { b = await postShares(b.id, item.id, shares); }
      catch (e: unknown) { showError(e instanceof Error ? e.message : "Error al guardar"); return; }
    }
    setBill(b); setStep(5);
  }

  // ── Step 5 ────────────────────────────────────────────────────

  async function handleSetPayers() {
    if (!bill) return;
    const me = bill.participants.find((p) => p.is_me);
    let payers: { participant_id: number; paid_amount: number }[] = [];
    const total = bill.total_amount;
    if (payerMode === "me") {
      if (!me) return;
      payers = [{ participant_id: me.id, paid_amount: total }];
    } else if (payerMode === "other") {
      if (!otherPayer) { showError("Selecciona quién pagó"); return; }
      payers = [{ participant_id: otherPayer, paid_amount: total }];
    } else {
      const sum = Object.values(multiAmounts).reduce((s, v) => s + (parseFloat(v) || 0), 0);
      if (Math.abs(sum - total) > 1) { showError(`La suma (${clp(Math.round(sum))}) no coincide con el total`); return; }
      payers = bill.participants.filter((p) => (parseFloat(multiAmounts[p.id] || "0") || 0) > 0)
        .map((p) => ({ participant_id: p.id, paid_amount: parseFloat(multiAmounts[p.id] || "0") || 0 }));
    }
    try { setBill(await setPayers(bill.id, payers)); setStep(6); }
    catch (e: unknown) { showError(e instanceof Error ? e.message : "Error"); }
  }

  // ── Step 6 ────────────────────────────────────────────────────

  async function handleFinalize() {
    if (!bill) return; setLoading(true);
    try {
      const b = await finalizeBill(bill.id, { account_id: selectedAccountId ?? undefined, category: "Comida" });
      setBill(b); setMyShare(b.participants.find((p) => p.is_me)?.owes_amount ?? 0); setFinalized(true);
    } catch (e: unknown) { showError(e instanceof Error ? e.message : "Error"); }
    finally { setLoading(false); }
  }

  function buildWhatsApp() {
    if (!bill) return "";
    const msg = `Cuenta en ${bill.merchant || "la cuenta"}\n${bill.participants.map((p) => `${p.name}: ${clp(p.owes_amount)}`).join("\n")}\nTotal: ${clp(bill.total_amount)}`;
    return `https://wa.me/?text=${encodeURIComponent(msg)}`;
  }

  // ─── RENDER ──────────────────────────────────────────────────────────────────

  const stepLabels: Record<number, string> = { 1: "Dividir cuenta", 2: bill?.merchant || "Ítems", 3: "Participantes", 4: "Asignar ítems", 5: "¿Quién pagó?", 6: "Resumen" };

  return (
    <div className="min-h-screen bg-slate-50 flex flex-col">
      {/* Header */}
      <div className="sticky top-0 z-10 bg-white border-b border-slate-200 px-4 pt-safe pt-4 pb-3">
        <div className="flex items-center gap-3 max-w-lg mx-auto">
          <button onClick={() => step > 1 ? setStep(step - 1) : router.back()} className="text-slate-400 hover:text-slate-700">
            <ChevronLeft size={22} />
          </button>
          <h1 className="font-bold text-slate-800 flex-1">{stepLabels[step]}</h1>
          {bill?.image_url && (
            <a href={resolveBackendUrl(bill.image_url)} target="_blank" rel="noopener noreferrer" className="text-slate-400 hover:text-indigo-600">
              <Camera size={18} />
            </a>
          )}
        </div>
        <div className="max-w-lg mx-auto mt-2"><StepDots step={step} /></div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto max-w-lg mx-auto w-full px-4 py-4 pb-28">

        {/* ── STEP 1: Capture ── */}
        {step === 1 && (
          <div className="space-y-6">
            <div
              className="border-2 border-dashed border-indigo-300 rounded-2xl bg-white flex flex-col items-center justify-center py-16 gap-3 cursor-pointer hover:border-indigo-500 transition-colors"
              onClick={() => !loading && fileRef.current?.click()}
            >
              {loading ? (
                <><div className="w-10 h-10 border-4 border-indigo-300 border-t-indigo-600 rounded-full animate-spin" /><p className="text-slate-500 text-sm">Leyendo boleta…</p></>
              ) : (
                <><Camera size={40} className="text-indigo-400" /><p className="font-semibold text-slate-700 text-lg">Subir boleta</p><p className="text-slate-400 text-sm">Foto o imagen</p></>
              )}
            </div>
            <input ref={fileRef} type="file" accept="image/*" className="hidden" onChange={(e) => { const f = e.target.files?.[0]; if (f) handleFile(f); }} />
            <button disabled={loading} className="w-full text-sm text-indigo-600 underline text-center py-2 disabled:opacity-40" onClick={handleManual}>
              Ingresar manualmente
            </button>
          </div>
        )}

        {/* ── STEP 2: Items review ── */}
        {step === 2 && bill && (
          <div className="space-y-3">
            {(() => {
              const diff = Math.abs(subtotal - bill.total_amount);
              const match = diff < 2 || bill.total_amount === 0;
              return (
                <div className={`rounded-xl px-4 py-2.5 text-sm font-medium ${match ? "bg-emerald-50 text-emerald-700 border border-emerald-200" : "bg-amber-50 text-amber-700 border border-amber-200"}`}>
                  {bill.items.length} ítems · Total {clp(subtotal)}
                  {!match && <span className="ml-2 font-normal">(boleta: {clp(bill.total_amount)})</span>}
                </div>
              );
            })()}
            {bill.items.map((item) => (
              <div key={item.id} className="bg-white rounded-xl shadow-sm overflow-hidden">
                {editItemId === item.id ? (
                  <div className="p-3 space-y-2">
                    <input className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm" value={editDraft.name} onChange={(e) => setEditDraft((d) => ({ ...d, name: e.target.value }))} placeholder="Nombre" />
                    <div className="flex gap-2">
                      <input type="number" className="flex-1 border border-slate-200 rounded-lg px-3 py-2 text-sm" value={editDraft.qty} min={1} onChange={(e) => setEditDraft((d) => ({ ...d, qty: parseInt(e.target.value) || 1 }))} placeholder="Cant." />
                      <input type="number" className="flex-1 border border-slate-200 rounded-lg px-3 py-2 text-sm" value={editDraft.unit_price} min={0} onChange={(e) => setEditDraft((d) => ({ ...d, unit_price: parseFloat(e.target.value) || 0 }))} placeholder="Precio unit." />
                    </div>
                    <p className="text-xs text-slate-400 text-right">Total: {clp((editDraft.qty || 1) * (editDraft.unit_price || 0))}</p>
                    <div className="flex gap-2">
                      <button className="flex-1 bg-indigo-600 text-white rounded-lg py-2 text-sm font-medium" onClick={saveEditItem}>Guardar</button>
                      <button className="px-4 py-2 text-slate-500 text-sm" onClick={() => setEditItemId(null)}>Cancelar</button>
                    </div>
                  </div>
                ) : (
                  <div className="flex items-center px-4 py-3 gap-3">
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-slate-800 truncate">{item.qty > 1 ? `${item.qty}× ` : ""}{item.name}</p>
                      <p className="text-xs text-slate-400">{clp(item.unit_price)} c/u</p>
                    </div>
                    <span className="text-sm font-semibold text-slate-700 shrink-0">{clp(item.line_total)}</span>
                    <button onClick={() => { setEditItemId(item.id); setEditDraft({ name: item.name, qty: item.qty, unit_price: item.unit_price }); }} className="text-slate-400 hover:text-indigo-600 p-1"><Pencil size={15} /></button>
                    <button onClick={() => handleDeleteItem(item.id)} className="text-slate-400 hover:text-red-500 p-1"><Trash2 size={15} /></button>
                  </div>
                )}
              </div>
            ))}
            {newItem !== null ? (
              <div className="bg-white rounded-xl shadow-sm p-3 space-y-2">
                <input className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm" value={newItem.name} autoFocus onChange={(e) => setNewItem((n) => n && { ...n, name: e.target.value })} placeholder="Nombre del ítem" />
                <div className="flex gap-2">
                  <input type="number" className="flex-1 border border-slate-200 rounded-lg px-3 py-2 text-sm" value={newItem.qty} min={1} onChange={(e) => setNewItem((n) => n && { ...n, qty: parseInt(e.target.value) || 1 })} placeholder="Cant." />
                  <input type="number" className="flex-1 border border-slate-200 rounded-lg px-3 py-2 text-sm" value={newItem.unit_price || ""} min={0} onChange={(e) => setNewItem((n) => n && { ...n, unit_price: parseFloat(e.target.value) || 0 })} placeholder="Precio unit." />
                </div>
                <div className="flex gap-2">
                  <button className="flex-1 bg-indigo-600 text-white rounded-lg py-2 text-sm font-medium" onClick={handleAddItem}>Agregar</button>
                  <button className="px-4 py-2 text-slate-500 text-sm" onClick={() => setNewItem(null)}>Cancelar</button>
                </div>
              </div>
            ) : (
              <button className="flex items-center gap-2 text-indigo-600 text-sm font-medium py-2" onClick={() => setNewItem({ name: "", qty: 1, unit_price: 0 })}>
                <Plus size={16} /> Agregar ítem
              </button>
            )}
          </div>
        )}

        {/* ── STEP 3: Participants ── */}
        {step === 3 && bill && (
          <div className="space-y-5">
            <p className="text-sm text-slate-500">Toca para agregar o quitar personas de la cuenta.</p>
            <div className="flex flex-wrap gap-4">
              {people.map((p) => {
                const inBill = bill.participants.some((bp) => bp.person_id === p.id);
                return (
                  <div key={p.id} className="relative">
                    <Avatar name={p.name} color={p.color} selected={inBill} locked={!!p.is_me} onClick={() => toggleParticipant(p.id)} />
                    {p.is_me && <span className="absolute -top-1 -right-1 bg-indigo-600 text-white text-[8px] font-bold px-1 rounded-full">Yo</span>}
                  </div>
                );
              })}
            </div>
            <div className="bg-slate-100 rounded-xl px-4 py-3 text-sm text-slate-600">
              <Users size={14} className="inline mr-1.5 mb-0.5" />
              {bill.participants.length} participante{bill.participants.length !== 1 ? "s" : ""}:&nbsp;
              {bill.participants.map((p) => p.name.split(" ")[0]).join(", ")}
            </div>
          </div>
        )}

        {/* ── STEP 4: Assign ── */}
        {step === 4 && bill && (
          <div className="space-y-3">
            {/* Participant chip bar */}
            <div className="overflow-x-auto -mx-4 px-4">
              <div className="flex gap-2 pb-1" style={{ minWidth: "max-content" }}>
                {bill.participants.map((p) => {
                  const isActive = activeParticipantId === p.id;
                  const total = runningTotal(p.id);
                  return (
                    <button key={p.id} onClick={() => setActiveParticipantId(p.id)}
                      className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-sm font-medium border-2 transition-all ${isActive ? "border-indigo-600 bg-indigo-50 text-indigo-800" : "border-transparent bg-white text-slate-700 shadow-sm"}`}>
                      <div className="w-6 h-6 rounded-full flex items-center justify-center text-white text-[10px] font-bold" style={{ background: p.color }}>{initials(p.name)}</div>
                      <span>{p.name.split(" ")[0]}</span>
                      {total > 0 && <span className="text-xs opacity-70">{clp(total)}</span>}
                    </button>
                  );
                })}
              </div>
            </div>

            {/* Progress bar */}
            {(() => {
              const totalUnits = bill.items.reduce((s, i) => s + i.qty, 0);
              const assignedUnits = bill.items.reduce((s, i) => s + unitsAssigned(i), 0);
              const assignedAmt = bill.items.reduce((s, i) => s + unitsAssigned(i) * i.unit_price, 0);
              const pct = totalUnits > 0 ? Math.round((assignedUnits / totalUnits) * 100) : 0;
              return (
                <div className="space-y-1">
                  <div className="flex justify-between text-xs text-slate-500">
                    <span>{assignedUnits} de {totalUnits} unidades asignadas</span>
                    <span>{clp(assignedAmt)} / {clp(bill.items.reduce((s, i) => s + i.line_total, 0))}</span>
                  </div>
                  <div className="h-1.5 bg-slate-200 rounded-full overflow-hidden">
                    <div className="h-full bg-indigo-500 rounded-full transition-all" style={{ width: `${pct}%` }} />
                  </div>
                </div>
              );
            })()}

            {/* Item cards */}
            {bill.items.map((item) => {
              const remaining = unitsRemaining(item);
              const done = remaining <= 0;
              const expanded = expandedItemId === item.id;
              return (
                <div key={item.id} className={`bg-white rounded-xl shadow-sm overflow-hidden transition-all ${shakeItemId === item.id ? "ring-2 ring-red-400" : ""}`}>
                  <div className="px-4 py-3">
                    <div className="flex items-start gap-2">
                      {/* Avatar dots with unit counts */}
                      <div className="flex -space-x-1.5 mt-0.5 shrink-0">
                        {bill.participants.map((p) => {
                          const u = getUnits(item.id, p.id);
                          if (u === 0) return null;
                          return <div key={p.id} title={`${p.name.split(" ")[0]}: ${u}`} className="w-6 h-6 rounded-full flex items-center justify-center text-white text-[9px] font-bold ring-1 ring-white" style={{ background: p.color }}>{u}</div>;
                        })}
                        {remaining > 0 && <div className="w-6 h-6 rounded-full flex items-center justify-center bg-slate-200 text-slate-500 text-[9px] font-bold ring-1 ring-white" title={`${remaining} sin asignar`}>{remaining}</div>}
                      </div>
                      {/* Name — tap to add 1 unit to active participant */}
                      <button className="flex-1 text-left min-w-0" onClick={() => tapItem(item)} onContextMenu={(e) => { e.preventDefault(); openBottomSheet(item); }}>
                        <p className={`text-sm font-medium truncate ${done ? "text-emerald-700" : "text-slate-800"}`}>
                          {item.qty > 1 ? `${item.qty}× ` : ""}{item.name}
                          {done && <Check size={12} className="inline ml-1 text-emerald-500" />}
                        </p>
                        <p className="text-xs text-slate-400">{remaining > 0 ? `${remaining} sin asignar` : "Completado"} · {clp(item.line_total)}</p>
                      </button>
                      {/* Action buttons */}
                      <div className="flex gap-1 shrink-0">
                        <button onClick={() => tapTodos(item)} className="text-xs bg-slate-100 hover:bg-slate-200 text-slate-600 font-medium px-2 py-1 rounded-lg">Todos</button>
                        {item.qty > 1 && <button onClick={() => openBottomSheet(item)} className="text-xs bg-slate-100 hover:bg-slate-200 text-slate-600 font-medium px-2 py-1 rounded-lg">Repartir</button>}
                      </div>
                    </div>
                    {/* Expanded unit pills */}
                    {expanded && item.qty > 1 && (
                      <div className="flex flex-wrap gap-1.5 mt-2 pt-2 border-t border-slate-100">
                        {Array.from({ length: item.qty }, (_, unitIdx) => {
                          let owner: BillParticipant | null = null, cum = 0;
                          for (const p of bill.participants) { const u = getUnits(item.id, p.id); if (unitIdx < cum + u) { owner = p; break; } cum += u; }
                          return (
                            <button key={unitIdx} onClick={() => {
                              if (owner) { setUnits(item.id, owner.id, getUnits(item.id, owner.id) - 1); }
                              else if (activeParticipantId) { setUnits(item.id, activeParticipantId, getUnits(item.id, activeParticipantId) + 1); }
                            }} className="w-8 h-8 rounded-full flex items-center justify-center text-white text-[10px] font-bold shadow-sm" style={{ background: owner ? owner.color : "#CBD5E1" }}>
                              {unitIdx + 1}
                            </button>
                          );
                        })}
                      </div>
                    )}
                  </div>
                  {item.qty > 1 && (
                    <button className="w-full text-[10px] text-slate-400 pb-1.5 text-center" onClick={() => setExpandedItemId(expanded ? null : item.id)}>
                      {expanded ? "▲ menos" : "▼ ver unidades"}
                    </button>
                  )}
                </div>
              );
            })}

            {/* Global divide equal */}
            <button onClick={handleAssignEqual} className="w-full bg-slate-100 hover:bg-slate-200 text-slate-700 text-sm font-medium py-3 rounded-xl flex items-center justify-center gap-2">
              <Check size={16} /> Dividir todo por igual
            </button>
          </div>
        )}

        {/* ── STEP 5: Who paid ── */}
        {step === 5 && bill && (
          <div className="space-y-4">
            {/* Me */}
            <button onClick={() => setPayerMode("me")} className={`w-full rounded-2xl p-4 text-left border-2 transition-colors ${payerMode === "me" ? "border-indigo-500 bg-indigo-50" : "border-slate-200 bg-white"}`}>
              <p className="font-semibold text-slate-800">Yo pagué</p>
              <p className="text-sm text-slate-500 mt-1">{clp(bill.total_amount)} de mi bolsillo</p>
              {payerMode === "me" && accounts.length > 0 && (
                <select className="mt-3 w-full border border-slate-200 rounded-lg px-3 py-2 text-sm bg-white" value={selectedAccountId ?? ""} onChange={(e) => setSelectedAccountId(e.target.value ? parseInt(e.target.value) : null)} onClick={(e) => e.stopPropagation()}>
                  <option value="">Sin cuenta específica</option>
                  {accounts.map((a) => <option key={a.id} value={a.id}>{a.name} ({a.bank})</option>)}
                </select>
              )}
            </button>
            {/* Other */}
            <button onClick={() => setPayerMode("other")} className={`w-full rounded-2xl p-4 text-left border-2 transition-colors ${payerMode === "other" ? "border-indigo-500 bg-indigo-50" : "border-slate-200 bg-white"}`}>
              <p className="font-semibold text-slate-800">Pagó otra persona</p>
              {payerMode === "other" && (
                <div className="flex gap-3 mt-3 flex-wrap" onClick={(e) => e.stopPropagation()}>
                  {bill.participants.filter((p) => !p.is_me).map((p) => <Avatar key={p.id} name={p.name} color={p.color} selected={otherPayer === p.id} onClick={() => setOtherPayer(p.id)} />)}
                </div>
              )}
            </button>
            {/* Split */}
            <button onClick={() => setPayerMode("split")} className={`w-full rounded-2xl p-4 text-left border-2 transition-colors ${payerMode === "split" ? "border-indigo-500 bg-indigo-50" : "border-slate-200 bg-white"}`}>
              <p className="font-semibold text-slate-800">Pagamos varios</p>
              {payerMode === "split" && (
                <div className="mt-3 space-y-2" onClick={(e) => e.stopPropagation()}>
                  {bill.participants.map((p) => (
                    <div key={p.id} className="flex items-center gap-3">
                      <div className="w-8 h-8 rounded-full flex items-center justify-center text-white text-xs font-bold shrink-0" style={{ background: p.color }}>{initials(p.name)}</div>
                      <span className="flex-1 text-sm text-slate-700">{p.name}</span>
                      <input type="number" className="w-28 border border-slate-200 rounded-lg px-2 py-1.5 text-sm text-right" placeholder="0" value={multiAmounts[p.id] ?? ""} onChange={(e) => setMultiAmounts((m) => ({ ...m, [p.id]: e.target.value }))} />
                    </div>
                  ))}
                  <div className="flex justify-between text-xs text-slate-400 pt-1">
                    <span>Total: {clp(bill.total_amount)}</span>
                    <span>Ingresado: {clp(Object.values(multiAmounts).reduce((s, v) => s + (parseFloat(v) || 0), 0))}</span>
                  </div>
                </div>
              )}
            </button>
          </div>
        )}

        {/* ── STEP 6: Summary ── */}
        {step === 6 && bill && (
          <div className="space-y-4">
            <div className="bg-indigo-600 rounded-2xl px-5 py-6 text-white text-center">
              <p className="text-sm opacity-80 mb-1">Tu gasto personal</p>
              <p className="text-4xl font-extrabold">{clp(finalized ? myShare : (bill.participants.find((p) => p.is_me)?.owes_amount ?? 0))}</p>
              {finalized && <p className="text-sm mt-2 opacity-80">Guardado en Lucas ✓</p>}
            </div>
            <div className="bg-white rounded-2xl shadow-sm divide-y divide-slate-100">
              {bill.participants.filter((p) => !p.is_me).map((p) => {
                const diff = p.owes_amount - p.paid_amount;
                if (Math.abs(diff) < 1) return null;
                return (
                  <div key={p.id} className="flex items-center gap-3 px-4 py-3">
                    <div className="w-9 h-9 rounded-full flex items-center justify-center text-white text-xs font-bold shrink-0" style={{ background: p.color }}>{initials(p.name)}</div>
                    <p className="flex-1 text-sm text-slate-700">
                      {diff > 0 ? <><span className="font-semibold">{p.name}</span> te debe {clp(diff)}</> : <>Le debes {clp(-diff)} a <span className="font-semibold">{p.name}</span></>}
                    </p>
                  </div>
                );
              })}
            </div>
            {!finalized && accounts.length > 0 && (
              <select className="w-full border border-slate-200 rounded-xl px-3 py-2.5 text-sm bg-white" value={selectedAccountId ?? ""} onChange={(e) => setSelectedAccountId(e.target.value ? parseInt(e.target.value) : null)}>
                <option value="">Sin cuenta específica</option>
                {accounts.map((a) => <option key={a.id} value={a.id}>{a.name} ({a.bank})</option>)}
              </select>
            )}
            {finalized && (
              <a href={buildWhatsApp()} target="_blank" rel="noopener noreferrer" className="flex items-center justify-center gap-2 w-full bg-emerald-500 hover:bg-emerald-600 text-white font-medium py-3 rounded-xl">
                <Share2 size={18} /> Compartir por WhatsApp
              </a>
            )}
            {finalized && <button onClick={() => router.push("/dashboard")} className="w-full text-sm text-slate-500 underline py-2">Cerrar</button>}
          </div>
        )}
      </div>

      {/* Error toast */}
      {error && <Toast msg={error} onClose={() => setError(null)} />}

      {/* Bottom sheet: per-item unit stepper */}
      {bottomSheetItemId !== null && bill && (() => {
        const item = bill.items.find((i) => i.id === bottomSheetItemId);
        if (!item) return null;
        const total = Object.values(bottomSheetDraft).reduce((s, v) => s + v, 0);
        return (
          <div className="fixed inset-0 z-40 flex items-end">
            <div className="absolute inset-0 bg-black/40" onClick={() => setBottomSheetItemId(null)} />
            <div className="relative bg-white rounded-t-3xl w-full max-w-lg mx-auto p-5 pb-safe pb-8 z-50">
              <h3 className="font-bold text-slate-800 mb-1">{item.name}</h3>
              <p className="text-sm text-slate-500 mb-4">Total asignado: {total}/{item.qty} {total === item.qty && <span className="text-emerald-600 font-medium">✓</span>}</p>
              <div className="space-y-3 mb-5">
                {bill.participants.map((p) => (
                  <div key={p.id} className="flex items-center gap-3">
                    <div className="w-8 h-8 rounded-full flex items-center justify-center text-white text-xs font-bold shrink-0" style={{ background: p.color }}>{initials(p.name)}</div>
                    <span className="flex-1 text-sm text-slate-700">{p.name.split(" ")[0]}</span>
                    <div className="flex items-center gap-2">
                      <button onClick={() => setBottomSheetDraft((d) => ({ ...d, [p.id]: Math.max(0, (d[p.id] ?? 0) - 1) }))} className="w-8 h-8 rounded-full bg-slate-100 hover:bg-slate-200 flex items-center justify-center text-slate-600 font-bold">−</button>
                      <span className="w-6 text-center text-sm font-semibold">{bottomSheetDraft[p.id] ?? 0}</span>
                      <button onClick={() => setBottomSheetDraft((d) => ({ ...d, [p.id]: (d[p.id] ?? 0) + 1 }))} className="w-8 h-8 rounded-full bg-slate-100 hover:bg-slate-200 flex items-center justify-center text-slate-600 font-bold">+</button>
                    </div>
                  </div>
                ))}
              </div>
              <button className="w-full bg-indigo-600 text-white font-medium py-3 rounded-xl" onClick={confirmBottomSheet}>Confirmar</button>
            </div>
          </div>
        );
      })()}

      {/* Sticky bottom CTA */}
      <div className="fixed bottom-0 left-0 right-0 z-30 bg-white border-t border-slate-200 px-4 py-3 pb-safe pb-5">
        <div className="max-w-lg mx-auto flex gap-3">
          {step > 1 && step < 6 && (
            <button onClick={() => setStep(step - 1)} className="px-4 py-3 rounded-xl border border-slate-200 text-slate-600 text-sm font-medium">Atrás</button>
          )}
          {step === 1 && <div className="flex-1 text-center text-xs text-slate-400 flex items-center justify-center">Sube una foto o toca &ldquo;Ingresar manualmente&rdquo;</div>}
          {step === 2 && bill && <button onClick={() => setStep(3)} className="flex-1 bg-indigo-600 hover:bg-indigo-700 text-white font-semibold py-3 rounded-xl flex items-center justify-center gap-2">Participantes <ChevronRight size={18} /></button>}
          {step === 3 && bill && <button onClick={goToAssign} className="flex-1 bg-indigo-600 hover:bg-indigo-700 text-white font-semibold py-3 rounded-xl flex items-center justify-center gap-2">Asignar <ChevronRight size={18} /></button>}
          {step === 4 && bill && <button onClick={goToWhoPaid} className="flex-1 bg-indigo-600 hover:bg-indigo-700 text-white font-semibold py-3 rounded-xl flex items-center justify-center gap-2">¿Quién pagó? <ChevronRight size={18} /></button>}
          {step === 5 && bill && <button onClick={handleSetPayers} className="flex-1 bg-indigo-600 hover:bg-indigo-700 text-white font-semibold py-3 rounded-xl flex items-center justify-center gap-2">Ver resumen <ChevronRight size={18} /></button>}
          {step === 6 && bill && !finalized && (
            <button onClick={handleFinalize} disabled={loading} className="flex-1 bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 text-white font-semibold py-3 rounded-xl flex items-center justify-center gap-2">
              {loading ? <div className="w-5 h-5 border-2 border-white/40 border-t-white rounded-full animate-spin" /> : <>Guardar en Lucas <Check size={18} /></>}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
