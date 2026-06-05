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

  // Step 4 — assign: itemId → Set of participantIds (equal split among selected)
  // overrides: itemId → Map<participantId, units> for unequal qty distribution
  const [selected, setSelected] = useState<Map<number, Set<number>>>(new Map());
  const [overrides, setOverrides] = useState<Map<number, Map<number, number>>>(new Map());
  const [bottomSheetItemId, setBottomSheetItemId] = useState<number | null>(null);
  const [bottomSheetDraft, setBottomSheetDraft] = useState<Record<number, number>>({});

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

  // Who is selected for a given item
  const getSelected = (itemId: number): Set<number> => selected.get(itemId) ?? new Set();

  // Running total per participant (from equal-split selected items + overrides)
  const runningTotal = (pid: number) => {
    if (!bill) return 0;
    return bill.items.reduce((s, item) => {
      const ov = overrides.get(item.id);
      if (ov) return s + (ov.get(pid) ?? 0) * item.unit_price;
      const sel = getSelected(item.id);
      if (!sel.has(pid)) return s;
      return s + item.line_total / sel.size;
    }, 0);
  };

  const itemAssigned = (item: BillItem) =>
    (overrides.get(item.id)?.size ?? 0) > 0 || getSelected(item.id).size > 0;

  function togglePerson(itemId: number, pid: number) {
    setSelected((prev) => {
      const next = new Map(prev);
      const cur = new Set(next.get(itemId) ?? []);
      if (cur.has(pid)) { cur.delete(pid); } else { cur.add(pid); }
      next.set(itemId, cur);
      return next;
    });
    // clear any override for this item since user is switching to equal mode
    setOverrides((prev) => { const n = new Map(prev); n.delete(itemId); return n; });
  }

  function selectAll(itemId: number) {
    if (!bill) return;
    setSelected((prev) => {
      const next = new Map(prev);
      next.set(itemId, new Set(bill.participants.map((p) => p.id)));
      return next;
    });
    setOverrides((prev) => { const n = new Map(prev); n.delete(itemId); return n; });
  }

  function seedSelected(b: Bill) {
    const sel = new Map<number, Set<number>>();
    const ov = new Map<number, Map<number, number>>();
    for (const item of b.items) {
      if (item.shares.length === 0) {
        // default: all participants
        sel.set(item.id, new Set(b.participants.map((p) => p.id)));
      } else {
        const hasUnequal = item.shares.some((s) => s.units != null);
        if (hasUnequal) {
          const inner = new Map<number, number>();
          item.shares.forEach((s) => { if ((s.units ?? 0) > 0) inner.set(s.participant_id, s.units!); });
          ov.set(item.id, inner);
        } else {
          sel.set(item.id, new Set(item.shares.map((s) => s.participant_id)));
        }
      }
    }
    setSelected(sel);
    setOverrides(ov);
  }

  function goToAssign() {
    if (bill) seedSelected(bill);
    setStep(4);
  }

  function openBottomSheet(item: BillItem) {
    if (!bill) return;
    const draft: Record<number, number> = {};
    const ov = overrides.get(item.id);
    const sel = getSelected(item.id);
    bill.participants.forEach((p) => {
      if (ov) draft[p.id] = ov.get(p.id) ?? 0;
      else draft[p.id] = sel.has(p.id) ? Math.round(item.qty / sel.size) : 0;
    });
    setBottomSheetDraft(draft);
    setBottomSheetItemId(item.id);
  }

  function confirmBottomSheet() {
    if (bottomSheetItemId === null || !bill) return;
    const inner = new Map<number, number>();
    Object.entries(bottomSheetDraft).forEach(([pid, u]) => { if (u > 0) inner.set(Number(pid), u); });
    setOverrides((prev) => { const n = new Map(prev); n.set(bottomSheetItemId, inner); return n; });
    // also update selected to match
    setSelected((prev) => {
      const n = new Map(prev);
      n.set(bottomSheetItemId, new Set(inner.keys()));
      return n;
    });
    setBottomSheetItemId(null);
  }

  async function handleAssignEqual() {
    if (!bill) return;
    try {
      const b = await assignEqual(bill.id);
      setBill(b);
      seedSelected(b);
    } catch (e: unknown) { showError(e instanceof Error ? e.message : "Error"); }
  }

  async function goToWhoPaid() {
    if (!bill) return;
    let b = bill;
    for (const item of bill.items) {
      const ov = overrides.get(item.id);
      const sel = getSelected(item.id);
      if (ov && ov.size > 0) {
        const shares = Array.from(ov.entries()).map(([pid, units]) => ({ participant_id: pid, units, weight: units / item.qty }));
        try { b = await postShares(b.id, item.id, shares); }
        catch (e: unknown) { showError(e instanceof Error ? e.message : "Error"); return; }
      } else if (sel.size > 0) {
        const w = 1 / sel.size;
        const shares = Array.from(sel).map((pid) => ({ participant_id: pid, weight: w }));
        try { b = await postShares(b.id, item.id, shares); }
        catch (e: unknown) { showError(e instanceof Error ? e.message : "Error"); return; }
      }
    }
    setBill(b);
    setStep(5);
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
            {/* Inline CTA — always visible without relying on fixed positioning */}
            <button onClick={() => setStep(3)} className="w-full bg-indigo-600 hover:bg-indigo-700 text-white font-semibold py-3.5 rounded-xl flex items-center justify-center gap-2 mt-2">
              Participantes <ChevronRight size={18} />
            </button>
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
          <div className="space-y-2.5">
            {/* Running totals per participant */}
            <div className="flex gap-2 overflow-x-auto pb-1 -mx-1 px-1">
              {bill.participants.map((p) => {
                const total = runningTotal(p.id);
                return (
                  <div key={p.id} className="flex-shrink-0 flex flex-col items-center gap-1 bg-white rounded-xl px-3 py-2 shadow-sm min-w-[64px]">
                    <div className="w-8 h-8 rounded-full flex items-center justify-center text-white text-xs font-bold" style={{ background: p.color }}>{initials(p.name)}</div>
                    <span className="text-[10px] text-slate-500 font-medium truncate max-w-[56px]">{p.name.split(" ")[0]}</span>
                    <span className="text-xs font-semibold text-indigo-700">{clp(Math.round(total))}</span>
                  </div>
                );
              })}
            </div>

            {/* "Dividir todo igual" shortcut */}
            <button onClick={handleAssignEqual} className="w-full bg-slate-100 hover:bg-slate-200 text-slate-600 text-sm font-medium py-2 rounded-xl flex items-center justify-center gap-2">
              <Check size={14} /> Dividir todo por igual
            </button>

            {/* Item cards — per-item chip selection */}
            {bill.items.map((item) => {
              const sel = getSelected(item.id);
              const ov = overrides.get(item.id);
              const assigned = itemAssigned(item);
              return (
                <div key={item.id} className={`bg-white rounded-xl shadow-sm px-4 py-3 transition-all ${assigned ? "border border-emerald-200" : "border border-slate-100"}`}>
                  {/* Item header */}
                  <div className="flex items-center justify-between mb-2.5">
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-semibold text-slate-800 truncate">
                        {item.qty > 1 ? <span className="text-indigo-500 mr-1">{item.qty}×</span> : null}{item.name}
                      </p>
                      <p className="text-xs text-slate-400 mt-0.5">
                        {item.qty > 1 ? `${clp(item.unit_price)} c/u · ` : ""}{clp(item.line_total)}
                        {ov && <span className="ml-1 text-amber-600">(personalizado)</span>}
                      </p>
                    </div>
                    {assigned && <Check size={16} className="text-emerald-500 shrink-0 ml-2" />}
                  </div>
                  {/* Participant chips — tap to toggle */}
                  <div className="flex flex-wrap gap-2">
                    {bill.participants.map((p) => {
                      const isOn = ov ? (ov.get(p.id) ?? 0) > 0 : sel.has(p.id);
                      const unitsLabel = ov ? (ov.get(p.id) ?? 0) : null;
                      return (
                        <button
                          key={p.id}
                          onClick={() => togglePerson(item.id, p.id)}
                          className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-full text-xs font-medium transition-all border-2 ${
                            isOn
                              ? "text-white border-transparent shadow-sm"
                              : "bg-white text-slate-400 border-slate-200"
                          }`}
                          style={isOn ? { background: p.color, borderColor: p.color } : {}}
                        >
                          <span>{p.name.split(" ")[0]}</span>
                          {unitsLabel != null && unitsLabel > 0 && <span className="bg-white/30 rounded-full px-1">{unitsLabel}</span>}
                        </button>
                      );
                    })}
                    {/* Todos shortcut */}
                    <button
                      onClick={() => selectAll(item.id)}
                      className="px-2.5 py-1.5 rounded-full text-xs font-medium bg-slate-100 text-slate-500 hover:bg-slate-200 border-2 border-transparent"
                    >Todos</button>
                    {/* Repartir for qty>1 unequal distribution */}
                    {item.qty > 1 && (
                      <button
                        onClick={() => openBottomSheet(item)}
                        className="px-2.5 py-1.5 rounded-full text-xs font-medium bg-slate-100 text-slate-500 hover:bg-slate-200 border-2 border-transparent"
                      >÷ Repartir</button>
                    )}
                  </div>
                </div>
              );
            })}
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
