"use client";
/**
 * Split page — 5-step Bill splitting flow (new /bills API)
 * Steps: 1 Capture+People → 2 Items → 3 Asignar → 4 ¿Quién pagó? → 5 Resumen
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Account, Person, listAccounts, listPeople, getToken, resolveBackendUrl } from "@/lib/api";
import { Camera, Plus, Trash2, Pencil, Check, X, ChevronRight, ChevronLeft, Share2 } from "lucide-react";

// ─── Types ────────────────────────────────────────────────────────────────────

interface BillParticipant {
  id: number;
  person_id: number;
  name: string;
  color: string;
  is_me: boolean;
  paid_amount: number;
  owes_amount: number;
}

interface BillItemShare {
  participant_id: number;
  weight: number;
}

interface BillItem {
  id: number;
  name: string;
  qty: number;
  unit_price: number;
  line_total: number;
  shares: BillItemShare[];
}

interface Bill {
  id: number;
  merchant: string;
  date: string;
  total_amount: number;
  tip_amount: number;
  currency: string;
  image_url: string;
  status: "draft" | "assigned" | "finalized";
  transaction_id: number | null;
  public_token: string | null;
  participants: BillParticipant[];
  items: BillItem[];
}

// ─── API helpers ──────────────────────────────────────────────────────────────

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function billReq<T>(
  path: string,
  init: RequestInit = {},
  isForm = false,
): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {};
  if (!isForm) headers["Content-Type"] = "application/json";
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(`${API}${path}`, { ...init, headers });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`${res.status}: ${text}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

async function createBill(opts?: { merchant?: string; date?: string; currency?: string }): Promise<Bill> {
  return billReq("/bills", { method: "POST", body: JSON.stringify(opts ?? {}) });
}
async function ocrBill(id: number, file: File): Promise<Bill> {
  const form = new FormData();
  form.append("file", file);
  const token = getToken();
  const res = await fetch(`${API}/bills/${id}/ocr`, {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: form,
  });
  if (!res.ok) throw new Error(`OCR ${res.status}: ${await res.text()}`);
  return res.json();
}
async function addParticipant(billId: number, personId: number): Promise<Bill> {
  return billReq(`/bills/${billId}/participants`, { method: "POST", body: JSON.stringify({ person_id: personId }) });
}
async function removeParticipant(billId: number, pid: number): Promise<Bill> {
  return billReq(`/bills/${billId}/participants/${pid}`, { method: "DELETE" });
}
async function addItem(billId: number, item: { name: string; qty: number; unit_price: number }): Promise<Bill> {
  return billReq(`/bills/${billId}/items`, { method: "POST", body: JSON.stringify(item) });
}
async function patchItem(billId: number, iid: number, patch: { name?: string; qty?: number; unit_price?: number }): Promise<Bill> {
  return billReq(`/bills/${billId}/items/${iid}`, { method: "PATCH", body: JSON.stringify(patch) });
}
async function deleteItem(billId: number, iid: number): Promise<Bill> {
  return billReq(`/bills/${billId}/items/${iid}`, { method: "DELETE" });
}
async function postShares(billId: number, itemId: number, shares: { participant_id: number; weight: number }[]): Promise<Bill> {
  return billReq(`/bills/${billId}/shares`, { method: "POST", body: JSON.stringify({ item_id: itemId, shares }) });
}
async function assignEqual(billId: number): Promise<Bill> {
  return billReq(`/bills/${billId}/assign-equal`, { method: "POST" });
}
async function setPayers(billId: number, payers: { participant_id: number; paid_amount: number }[]): Promise<Bill> {
  return billReq(`/bills/${billId}/set-payers`, { method: "POST", body: JSON.stringify(payers) });
}
async function finalizeBill(billId: number, opts: { account_id?: number; category?: string }): Promise<Bill> {
  return billReq(`/bills/${billId}/finalize`, { method: "POST", body: JSON.stringify(opts) });
}
async function patchBill(billId: number, patch: { tip_amount?: number }): Promise<Bill> {
  return billReq(`/bills/${billId}`, { method: "PATCH", body: JSON.stringify(patch) });
}

// ─── Utilities ────────────────────────────────────────────────────────────────

function clp(n: number) {
  return "$" + Math.round(n).toLocaleString("es-CL");
}

function initials(name: string) {
  return name
    .split(" ")
    .map((w) => w[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();
}

// ─── Step indicator ───────────────────────────────────────────────────────────

function StepDots({ step }: { step: number }) {
  return (
    <div className="flex items-center justify-center gap-2 mb-4">
      {[1, 2, 3, 4, 5].map((s) => (
        <div
          key={s}
          className={`rounded-full transition-all ${
            s === step
              ? "w-6 h-2.5 bg-indigo-600"
              : s < step
              ? "w-2.5 h-2.5 bg-emerald-500"
              : "w-2.5 h-2.5 bg-slate-300"
          }`}
        />
      ))}
    </div>
  );
}

// ─── Error toast ──────────────────────────────────────────────────────────────

function Toast({ msg, onClose }: { msg: string; onClose: () => void }) {
  useEffect(() => {
    const t = setTimeout(onClose, 3000);
    return () => clearTimeout(t);
  }, [msg, onClose]);
  return (
    <div className="fixed bottom-24 left-1/2 -translate-x-1/2 z-50 bg-red-600 text-white text-sm px-4 py-2 rounded-xl shadow-lg max-w-xs text-center">
      {msg}
    </div>
  );
}

// ─── Avatar chip ──────────────────────────────────────────────────────────────

function Avatar({
  name,
  color,
  selected,
  onClick,
  locked,
}: {
  name: string;
  color: string;
  selected: boolean;
  onClick?: () => void;
  locked?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={locked ? undefined : onClick}
      className={`flex flex-col items-center gap-1 transition-opacity ${locked ? "opacity-60 cursor-default" : "cursor-pointer"}`}
    >
      <div
        className={`w-11 h-11 rounded-full flex items-center justify-center text-white font-bold text-sm ring-2 transition-all ${
          selected ? "ring-indigo-500 ring-offset-1" : "ring-transparent"
        }`}
        style={{ background: color }}
      >
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

  // Step 1 state
  const [selectedPeople, setSelectedPeople] = useState<Set<number>>(new Set());

  // Step 2 state
  const [editItemId, setEditItemId] = useState<number | null>(null);
  const [editDraft, setEditDraft] = useState({ name: "", qty: 1, unit_price: 0 });
  const [newItem, setNewItem] = useState<{ name: string; qty: number; unit_price: number } | null>(null);
  const [tipPct, setTipPct] = useState<number | null>(null);
  const [customTip, setCustomTip] = useState("");
  const [showCustomTip, setShowCustomTip] = useState(false);

  // Step 3 state
  const [shareItemId, setShareItemId] = useState<number | null>(null);
  const [shareSelected, setShareSelected] = useState<Set<number>>(new Set());

  // Step 4 state
  const [payerMode, setPayerMode] = useState<"me" | "other" | "split">("me");
  const [otherPayer, setOtherPayer] = useState<number | null>(null);
  const [multiAmounts, setMultiAmounts] = useState<Record<number, string>>({});
  const [selectedAccountId, setSelectedAccountId] = useState<number | null>(null);

  // Step 5 state
  const [finalized, setFinalized] = useState(false);
  const [myShare, setMyShare] = useState(0);

  function showError(msg: string) {
    setError(msg);
  }

  // Load people and accounts on mount
  useEffect(() => {
    listPeople().then((ps) => {
      setPeople(ps);
      const me = ps.find((p) => p.is_me);
      if (me) setSelectedPeople(new Set([me.id]));
    }).catch(() => {});
    listAccounts().then(setAccounts).catch(() => {});
  }, []);

  // ── File upload ──────────────────────────────────────────────

  async function handleFile(file: File) {
    setLoading(true);
    try {
      // 1. Create the bill
      const today = new Date().toISOString().split("T")[0];
      let b = await createBill({ date: today });

      // 2. Add selected participants
      const me = people.find((p) => p.is_me);
      const toAdd = Array.from(selectedPeople);
      if (me && !toAdd.includes(me.id)) toAdd.unshift(me.id);
      for (const pid of toAdd) {
        b = await addParticipant(b.id, pid);
      }

      // 3. OCR
      b = await ocrBill(b.id, file);
      setBill(b);
      setStep(2);
    } catch (e: unknown) {
      showError(e instanceof Error ? e.message : "Error al leer boleta");
    } finally {
      setLoading(false);
    }
  }

  // ── Step 2: tip ──────────────────────────────────────────────

  const subtotal = bill ? bill.items.reduce((s, i) => s + i.line_total, 0) : 0;

  async function applyTipPct(pct: number) {
    if (!bill) return;
    setTipPct(pct);
    setShowCustomTip(false);
    const tipAmt = Math.round(subtotal * pct);
    try {
      const b = await patchBill(bill.id, { tip_amount: tipAmt });
      setBill(b);
    } catch (e: unknown) {
      showError(e instanceof Error ? e.message : "Error al guardar propina");
    }
  }

  async function applyCustomTip() {
    if (!bill) return;
    const tipAmt = parseInt(customTip.replace(/\D/g, ""), 10) || 0;
    try {
      const b = await patchBill(bill.id, { tip_amount: tipAmt });
      setBill(b);
      setShowCustomTip(false);
      setTipPct(null);
    } catch (e: unknown) {
      showError(e instanceof Error ? e.message : "Error al guardar propina");
    }
  }

  // ── Step 2: items ────────────────────────────────────────────

  async function saveEditItem() {
    if (!bill || editItemId === null) return;
    try {
      const b = await patchItem(bill.id, editItemId, editDraft);
      setBill(b);
      setEditItemId(null);
    } catch (e: unknown) {
      showError(e instanceof Error ? e.message : "Error al guardar ítem");
    }
  }

  async function handleDeleteItem(iid: number) {
    if (!bill) return;
    try {
      const b = await deleteItem(bill.id, iid);
      setBill(b);
    } catch (e: unknown) {
      showError(e instanceof Error ? e.message : "Error al eliminar ítem");
    }
  }

  async function handleAddItem() {
    if (!bill || !newItem || !newItem.name.trim()) return;
    try {
      const b = await addItem(bill.id, newItem);
      setBill(b);
      setNewItem(null);
    } catch (e: unknown) {
      showError(e instanceof Error ? e.message : "Error al agregar ítem");
    }
  }

  // ── Step 3: shares ───────────────────────────────────────────

  function openShareSheet(itemId: number) {
    const item = bill?.items.find((i) => i.id === itemId);
    if (!item) return;
    const currentPids = new Set(item.shares.map((s) => s.participant_id));
    setShareSelected(currentPids.size > 0 ? currentPids : new Set(bill?.participants.map((p) => p.id)));
    setShareItemId(itemId);
  }

  async function confirmShares() {
    if (!bill || shareItemId === null || shareSelected.size === 0) return;
    const pids = Array.from(shareSelected);
    const w = 1 / pids.length;
    const shares = pids.map((pid) => ({ participant_id: pid, weight: w }));
    try {
      const b = await postShares(bill.id, shareItemId, shares);
      setBill(b);
      setShareItemId(null);
    } catch (e: unknown) {
      showError(e instanceof Error ? e.message : "Error al asignar");
    }
  }

  async function handleAssignEqual() {
    if (!bill) return;
    try {
      const b = await assignEqual(bill.id);
      setBill(b);
    } catch (e: unknown) {
      showError(e instanceof Error ? e.message : "Error al dividir igual");
    }
  }

  // ── Step 4: payers ───────────────────────────────────────────

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
      if (Math.abs(sum - total) > 1) { showError(`La suma ($${Math.round(sum).toLocaleString("es-CL")}) no coincide con el total`); return; }
      payers = bill.participants
        .filter((p) => (parseFloat(multiAmounts[p.id] || "0") || 0) > 0)
        .map((p) => ({ participant_id: p.id, paid_amount: parseFloat(multiAmounts[p.id] || "0") || 0 }));
    }

    try {
      const b = await setPayers(bill.id, payers);
      setBill(b);
      setStep(5);
    } catch (e: unknown) {
      showError(e instanceof Error ? e.message : "Error al guardar pagadores");
    }
  }

  // ── Step 5: finalize ─────────────────────────────────────────

  async function handleFinalize() {
    if (!bill) return;
    setLoading(true);
    try {
      const b = await finalizeBill(bill.id, {
        account_id: selectedAccountId ?? undefined,
        category: "Comida",
      });
      setBill(b);
      const me = b.participants.find((p) => p.is_me);
      setMyShare(me?.owes_amount ?? 0);
      setFinalized(true);
    } catch (e: unknown) {
      showError(e instanceof Error ? e.message : "Error al finalizar");
    } finally {
      setLoading(false);
    }
  }

  function buildWhatsApp() {
    if (!bill) return "";
    const lines = bill.participants.map((p) => `${p.name}: ${clp(p.owes_amount)}`);
    const merchant = bill.merchant || "la cuenta";
    const msg = `Cuenta en ${merchant} 🍽️\n${lines.join("\n")}\nTotal: ${clp(bill.total_amount)}`;
    return `https://wa.me/?text=${encodeURIComponent(msg)}`;
  }

  // ── Participant management in bill ───────────────────────────

  async function toggleParticipant(pid: number) {
    if (!bill) {
      // Pre-bill phase: just toggle local selection
      const me = people.find((p) => p.is_me);
      if (me && pid === me.id) return;
      setSelectedPeople((prev) => {
        const next = new Set(prev);
        if (next.has(pid)) next.delete(pid);
        else next.add(pid);
        return next;
      });
      return;
    }
    const existing = bill.participants.find((p) => p.person_id === pid);
    try {
      if (existing) {
        if (existing.is_me) return;
        const b = await removeParticipant(bill.id, existing.id);
        setBill(b);
      } else {
        const b = await addParticipant(bill.id, pid);
        setBill(b);
      }
    } catch (e: unknown) {
      showError(e instanceof Error ? e.message : "Error al actualizar participantes");
    }
  }

  // ── Render helpers ───────────────────────────────────────────

  const participantIds = bill
    ? new Set(bill.participants.map((p) => p.person_id))
    : selectedPeople;

  function unassignedTotal() {
    if (!bill) return 0;
    return bill.items
      .filter((i) => i.shares.length === 0)
      .reduce((s, i) => s + i.line_total, 0);
  }

  // ─── RENDER ──────────────────────────────────────────────────────────────────

  return (
    <div className="min-h-screen bg-slate-50 flex flex-col">
      {/* Header */}
      <div className="sticky top-0 z-10 bg-white border-b border-slate-200 px-4 pt-safe pt-4 pb-3">
        <div className="flex items-center gap-3 max-w-lg mx-auto">
          <button onClick={() => step > 1 ? setStep(step - 1) : router.back()} className="text-slate-400 hover:text-slate-700">
            <ChevronLeft size={22} />
          </button>
          <h1 className="font-bold text-slate-800 flex-1">
            {step === 1 && "Dividir cuenta"}
            {step === 2 && (bill?.merchant || "Ítems")}
            {step === 3 && "Asignar ítems"}
            {step === 4 && "¿Quién pagó?"}
            {step === 5 && "Resumen"}
          </h1>
          {bill?.image_url && (
            <a href={resolveBackendUrl(bill.image_url)} target="_blank" rel="noopener noreferrer" className="text-slate-400 hover:text-indigo-600">
              <Camera size={18} />
            </a>
          )}
        </div>
        <div className="max-w-lg mx-auto mt-2">
          <StepDots step={step} />
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto max-w-lg mx-auto w-full px-4 py-4 pb-28">

        {/* ── STEP 1: Capture + Participants ── */}
        {step === 1 && (
          <div className="space-y-6">
            {/* Upload area */}
            <div
              className="border-2 border-dashed border-indigo-300 rounded-2xl bg-white flex flex-col items-center justify-center py-12 gap-3 cursor-pointer hover:border-indigo-500 transition-colors"
              onClick={() => fileRef.current?.click()}
            >
              {loading ? (
                <>
                  <div className="w-10 h-10 border-4 border-indigo-300 border-t-indigo-600 rounded-full animate-spin" />
                  <p className="text-slate-500 text-sm">Leyendo boleta…</p>
                </>
              ) : (
                <>
                  <Camera size={36} className="text-indigo-400" />
                  <p className="font-semibold text-slate-700">Subir boleta</p>
                  <p className="text-slate-400 text-sm">Foto o imagen</p>
                </>
              )}
            </div>
            <input
              ref={fileRef}
              type="file"
              accept="image/*"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) handleFile(f);
              }}
            />

            {/* Participants */}
            <div>
              <p className="text-sm font-semibold text-slate-600 mb-3">¿Con quiénes?</p>
              <div className="flex flex-wrap gap-3">
                {people.map((p) => (
                  <Avatar
                    key={p.id}
                    name={p.name}
                    color={p.color}
                    selected={participantIds.has(p.id)}
                    locked={!!p.is_me}
                    onClick={() => toggleParticipant(p.id)}
                  />
                ))}
              </div>
            </div>

            {/* Skip OCR: continue without image */}
            <button
              className="w-full text-sm text-indigo-600 underline text-center py-1"
              onClick={async () => {
                setLoading(true);
                try {
                  const today = new Date().toISOString().split("T")[0];
                  let b = await createBill({ date: today });
                  const toAdd = Array.from(selectedPeople);
                  for (const pid of toAdd) b = await addParticipant(b.id, pid);
                  setBill(b);
                  setStep(2);
                } catch (e: unknown) {
                  showError(e instanceof Error ? e.message : "Error");
                } finally {
                  setLoading(false);
                }
              }}
            >
              Ingresar manualmente
            </button>
          </div>
        )}

        {/* ── STEP 2: Items ── */}
        {step === 2 && bill && (
          <div className="space-y-3">
            {bill.items.map((item) => (
              <div key={item.id} className="bg-white rounded-xl shadow-sm overflow-hidden">
                {editItemId === item.id ? (
                  <div className="p-3 space-y-2">
                    <input
                      className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm"
                      value={editDraft.name}
                      onChange={(e) => setEditDraft((d) => ({ ...d, name: e.target.value }))}
                      placeholder="Nombre"
                    />
                    <div className="flex gap-2">
                      <input
                        type="number"
                        className="flex-1 border border-slate-200 rounded-lg px-3 py-2 text-sm"
                        value={editDraft.qty}
                        min={1}
                        onChange={(e) => setEditDraft((d) => ({ ...d, qty: parseInt(e.target.value) || 1 }))}
                        placeholder="Cant."
                      />
                      <input
                        type="number"
                        className="flex-1 border border-slate-200 rounded-lg px-3 py-2 text-sm"
                        value={editDraft.unit_price}
                        min={0}
                        onChange={(e) => setEditDraft((d) => ({ ...d, unit_price: parseFloat(e.target.value) || 0 }))}
                        placeholder="Precio unit."
                      />
                    </div>
                    <div className="flex gap-2">
                      <button
                        className="flex-1 bg-indigo-600 text-white rounded-lg py-2 text-sm font-medium"
                        onClick={saveEditItem}
                      >
                        Guardar
                      </button>
                      <button
                        className="px-4 py-2 text-slate-500 text-sm"
                        onClick={() => setEditItemId(null)}
                      >
                        Cancelar
                      </button>
                    </div>
                  </div>
                ) : (
                  <div className="flex items-center px-4 py-3 gap-3">
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-slate-800 truncate">
                        {item.qty > 1 ? `${item.qty}× ` : ""}{item.name}
                      </p>
                      <p className="text-xs text-slate-400">{clp(item.unit_price)} c/u</p>
                    </div>
                    <span className="text-sm font-semibold text-slate-700 shrink-0">{clp(item.line_total)}</span>
                    <button
                      onClick={() => {
                        setEditItemId(item.id);
                        setEditDraft({ name: item.name, qty: item.qty, unit_price: item.unit_price });
                      }}
                      className="text-slate-400 hover:text-indigo-600 p-1"
                    >
                      <Pencil size={15} />
                    </button>
                    <button onClick={() => handleDeleteItem(item.id)} className="text-slate-400 hover:text-red-500 p-1">
                      <Trash2 size={15} />
                    </button>
                  </div>
                )}
              </div>
            ))}

            {/* New item form */}
            {newItem !== null ? (
              <div className="bg-white rounded-xl shadow-sm p-3 space-y-2">
                <input
                  className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm"
                  value={newItem.name}
                  autoFocus
                  onChange={(e) => setNewItem((n) => n && { ...n, name: e.target.value })}
                  placeholder="Nombre del ítem"
                />
                <div className="flex gap-2">
                  <input
                    type="number"
                    className="flex-1 border border-slate-200 rounded-lg px-3 py-2 text-sm"
                    value={newItem.qty}
                    min={1}
                    onChange={(e) => setNewItem((n) => n && { ...n, qty: parseInt(e.target.value) || 1 })}
                    placeholder="Cant."
                  />
                  <input
                    type="number"
                    className="flex-1 border border-slate-200 rounded-lg px-3 py-2 text-sm"
                    value={newItem.unit_price || ""}
                    min={0}
                    onChange={(e) => setNewItem((n) => n && { ...n, unit_price: parseFloat(e.target.value) || 0 })}
                    placeholder="Precio unit."
                  />
                </div>
                <div className="flex gap-2">
                  <button
                    className="flex-1 bg-indigo-600 text-white rounded-lg py-2 text-sm font-medium"
                    onClick={handleAddItem}
                  >
                    Agregar
                  </button>
                  <button className="px-4 py-2 text-slate-500 text-sm" onClick={() => setNewItem(null)}>
                    Cancelar
                  </button>
                </div>
              </div>
            ) : (
              <button
                className="flex items-center gap-2 text-indigo-600 text-sm font-medium py-2"
                onClick={() => setNewItem({ name: "", qty: 1, unit_price: 0 })}
              >
                <Plus size={16} /> Agregar ítem
              </button>
            )}

            {/* Tip */}
            <div className="bg-white rounded-xl shadow-sm p-4 space-y-3">
              <p className="text-sm font-semibold text-slate-700">Propina</p>
              <div className="flex gap-2">
                {[0, 0.1, 0.15].map((pct) => (
                  <button
                    key={pct}
                    onClick={() => applyTipPct(pct)}
                    className={`flex-1 py-2 rounded-lg text-sm font-medium border transition-colors ${
                      tipPct === pct && !showCustomTip
                        ? "bg-indigo-600 text-white border-indigo-600"
                        : "border-slate-200 text-slate-700"
                    }`}
                  >
                    {pct === 0 ? "Sin propina" : `${pct * 100}%`}
                  </button>
                ))}
                <button
                  onClick={() => { setShowCustomTip(true); setTipPct(null); }}
                  className={`flex-1 py-2 rounded-lg text-sm font-medium border transition-colors ${
                    showCustomTip ? "bg-indigo-600 text-white border-indigo-600" : "border-slate-200 text-slate-700"
                  }`}
                >
                  Otro
                </button>
              </div>
              {showCustomTip && (
                <div className="flex gap-2">
                  <input
                    type="number"
                    className="flex-1 border border-slate-200 rounded-lg px-3 py-2 text-sm"
                    value={customTip}
                    onChange={(e) => setCustomTip(e.target.value)}
                    placeholder="Monto propina"
                  />
                  <button onClick={applyCustomTip} className="bg-indigo-600 text-white px-4 rounded-lg text-sm">
                    <Check size={16} />
                  </button>
                </div>
              )}
            </div>

            {/* Summary bar inlined above sticky */}
            <div className="bg-white rounded-xl shadow-sm divide-y divide-slate-100 text-sm">
              <div className="flex justify-between px-4 py-2 text-slate-500">
                <span>Subtotal</span><span>{clp(subtotal)}</span>
              </div>
              <div className="flex justify-between px-4 py-2 text-slate-500">
                <span>Propina</span><span>{clp(bill.tip_amount)}</span>
              </div>
              <div className="flex justify-between px-4 py-3 font-bold text-slate-800">
                <span>Total</span><span>{clp(bill.total_amount)}</span>
              </div>
            </div>
          </div>
        )}

        {/* ── STEP 3: Assign ── */}
        {step === 3 && bill && (
          <div className="space-y-3">
            <button
              onClick={handleAssignEqual}
              className="w-full bg-slate-100 hover:bg-slate-200 text-slate-700 text-sm font-medium py-3 rounded-xl flex items-center justify-center gap-2"
            >
              <Check size={16} /> Dividir todo igual
            </button>

            {unassignedTotal() > 0 && (
              <div className="bg-amber-50 border border-amber-200 rounded-xl px-4 py-2 text-sm text-amber-700">
                Sin asignar: {clp(unassignedTotal())}
              </div>
            )}

            {bill.items.map((item) => {
              const assignedParticipants = bill.participants.filter((p) =>
                item.shares.some((s) => s.participant_id === p.id),
              );
              return (
                <div
                  key={item.id}
                  className="bg-white rounded-xl shadow-sm px-4 py-3 flex items-center gap-3 cursor-pointer hover:bg-slate-50"
                  onClick={() => openShareSheet(item.id)}
                >
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-slate-800 truncate">
                      {item.qty > 1 ? `${item.qty}× ` : ""}{item.name}
                    </p>
                    <p className="text-xs text-slate-400">{clp(item.line_total)}</p>
                  </div>
                  <div className="flex -space-x-1.5">
                    {assignedParticipants.length === 0 ? (
                      <span className="text-xs text-slate-400 italic">Sin asignar</span>
                    ) : (
                      assignedParticipants.map((p) => (
                        <div
                          key={p.id}
                          className="w-7 h-7 rounded-full flex items-center justify-center text-white text-[10px] font-bold ring-2 ring-white"
                          style={{ background: p.color }}
                          title={p.name}
                        >
                          {initials(p.name)}
                        </div>
                      ))
                    )}
                  </div>
                  <ChevronRight size={16} className="text-slate-300 shrink-0" />
                </div>
              );
            })}
          </div>
        )}

        {/* ── STEP 4: Who paid ── */}
        {step === 4 && bill && (
          <div className="space-y-4">
            {/* Me */}
            <button
              onClick={() => setPayerMode("me")}
              className={`w-full rounded-2xl p-4 text-left border-2 transition-colors ${
                payerMode === "me" ? "border-indigo-500 bg-indigo-50" : "border-slate-200 bg-white"
              }`}
            >
              <p className="font-semibold text-slate-800">Yo pagué</p>
              <p className="text-sm text-slate-500 mt-1">{clp(bill.total_amount)} de mi bolsillo</p>
              {payerMode === "me" && accounts.length > 0 && (
                <select
                  className="mt-3 w-full border border-slate-200 rounded-lg px-3 py-2 text-sm bg-white"
                  value={selectedAccountId ?? ""}
                  onChange={(e) => setSelectedAccountId(e.target.value ? parseInt(e.target.value) : null)}
                  onClick={(e) => e.stopPropagation()}
                >
                  <option value="">Sin cuenta específica</option>
                  {accounts.map((a) => (
                    <option key={a.id} value={a.id}>{a.name} ({a.bank})</option>
                  ))}
                </select>
              )}
            </button>

            {/* Other person */}
            <button
              onClick={() => setPayerMode("other")}
              className={`w-full rounded-2xl p-4 text-left border-2 transition-colors ${
                payerMode === "other" ? "border-indigo-500 bg-indigo-50" : "border-slate-200 bg-white"
              }`}
            >
              <p className="font-semibold text-slate-800">Pagó otra persona</p>
              {payerMode === "other" && (
                <div className="flex gap-3 mt-3 flex-wrap" onClick={(e) => e.stopPropagation()}>
                  {bill.participants
                    .filter((p) => !p.is_me)
                    .map((p) => (
                      <Avatar
                        key={p.id}
                        name={p.name}
                        color={p.color}
                        selected={otherPayer === p.id}
                        onClick={() => setOtherPayer(p.id)}
                      />
                    ))}
                </div>
              )}
            </button>

            {/* Split */}
            <button
              onClick={() => setPayerMode("split")}
              className={`w-full rounded-2xl p-4 text-left border-2 transition-colors ${
                payerMode === "split" ? "border-indigo-500 bg-indigo-50" : "border-slate-200 bg-white"
              }`}
            >
              <p className="font-semibold text-slate-800">Pagamos varios</p>
              {payerMode === "split" && (
                <div className="mt-3 space-y-2" onClick={(e) => e.stopPropagation()}>
                  {bill.participants.map((p) => (
                    <div key={p.id} className="flex items-center gap-3">
                      <div
                        className="w-8 h-8 rounded-full flex items-center justify-center text-white text-xs font-bold shrink-0"
                        style={{ background: p.color }}
                      >
                        {initials(p.name)}
                      </div>
                      <span className="flex-1 text-sm text-slate-700">{p.name}</span>
                      <input
                        type="number"
                        className="w-28 border border-slate-200 rounded-lg px-2 py-1.5 text-sm text-right"
                        placeholder="0"
                        value={multiAmounts[p.id] ?? ""}
                        onChange={(e) => setMultiAmounts((m) => ({ ...m, [p.id]: e.target.value }))}
                      />
                    </div>
                  ))}
                  <div className="flex justify-between text-xs text-slate-400 pt-1">
                    <span>Total: {clp(bill.total_amount)}</span>
                    <span>
                      Ingresado:{" "}
                      {clp(Object.values(multiAmounts).reduce((s, v) => s + (parseFloat(v) || 0), 0))}
                    </span>
                  </div>
                </div>
              )}
            </button>
          </div>
        )}

        {/* ── STEP 5: Summary ── */}
        {step === 5 && bill && (
          <div className="space-y-4">
            {/* My share */}
            <div className="bg-indigo-600 rounded-2xl px-5 py-6 text-white text-center">
              <p className="text-sm opacity-80 mb-1">Tu gasto personal</p>
              <p className="text-4xl font-extrabold">
                {clp(finalized ? myShare : (bill.participants.find((p) => p.is_me)?.owes_amount ?? 0))}
              </p>
              {finalized && <p className="text-sm mt-2 opacity-80">Guardado en Lucas ✓</p>}
            </div>

            {/* Debts */}
            <div className="bg-white rounded-2xl shadow-sm divide-y divide-slate-100">
              {bill.participants
                .filter((p) => !p.is_me)
                .map((p) => {
                  const me = bill.participants.find((q) => q.is_me);
                  if (!me) return null;
                  const diff = p.owes_amount - p.paid_amount;
                  if (Math.abs(diff) < 1) return null;
                  return (
                    <div key={p.id} className="flex items-center gap-3 px-4 py-3">
                      <div
                        className="w-9 h-9 rounded-full flex items-center justify-center text-white text-xs font-bold shrink-0"
                        style={{ background: p.color }}
                      >
                        {initials(p.name)}
                      </div>
                      <p className="flex-1 text-sm text-slate-700">
                        {diff > 0 ? (
                          <><span className="font-semibold">{p.name}</span> te debe {clp(diff)}</>
                        ) : (
                          <>Le debes {clp(-diff)} a <span className="font-semibold">{p.name}</span></>
                        )}
                      </p>
                    </div>
                  );
                })}
            </div>

            {/* Account selector for finalize */}
            {!finalized && (
              <div className="space-y-2">
                {accounts.length > 0 && (
                  <select
                    className="w-full border border-slate-200 rounded-xl px-3 py-2.5 text-sm bg-white"
                    value={selectedAccountId ?? ""}
                    onChange={(e) => setSelectedAccountId(e.target.value ? parseInt(e.target.value) : null)}
                  >
                    <option value="">Sin cuenta específica</option>
                    {accounts.map((a) => (
                      <option key={a.id} value={a.id}>{a.name} ({a.bank})</option>
                    ))}
                  </select>
                )}
              </div>
            )}

            {/* WhatsApp */}
            {finalized && (
              <a
                href={buildWhatsApp()}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center justify-center gap-2 w-full bg-emerald-500 hover:bg-emerald-600 text-white font-medium py-3 rounded-xl"
              >
                <Share2 size={18} /> Compartir por WhatsApp
              </a>
            )}

            {finalized && (
              <button
                onClick={() => router.push("/dashboard")}
                className="w-full text-sm text-slate-500 underline py-2"
              >
                Cerrar
              </button>
            )}
          </div>
        )}
      </div>

      {/* Error toast */}
      {error && <Toast msg={error} onClose={() => setError(null)} />}

      {/* Bottom sheet: share assignment */}
      {shareItemId !== null && bill && (
        <div className="fixed inset-0 z-40 flex items-end">
          <div className="absolute inset-0 bg-black/40" onClick={() => setShareItemId(null)} />
          <div className="relative bg-white rounded-t-3xl w-full max-w-lg mx-auto p-5 pb-safe pb-8 z-50">
            <h3 className="font-bold text-slate-800 mb-1">
              {bill.items.find((i) => i.id === shareItemId)?.name}
            </h3>
            <p className="text-sm text-slate-500 mb-4">Selecciona quiénes comparten este ítem</p>
            <div className="flex gap-4 flex-wrap mb-5">
              {bill.participants.map((p) => (
                <Avatar
                  key={p.id}
                  name={p.name}
                  color={p.color}
                  selected={shareSelected.has(p.id)}
                  onClick={() =>
                    setShareSelected((prev) => {
                      const next = new Set(prev);
                      if (next.has(p.id)) next.delete(p.id);
                      else next.add(p.id);
                      return next;
                    })
                  }
                />
              ))}
            </div>
            <button
              className="w-full bg-indigo-600 text-white font-medium py-3 rounded-xl disabled:opacity-40"
              disabled={shareSelected.size === 0}
              onClick={confirmShares}
            >
              Confirmar
            </button>
          </div>
        </div>
      )}

      {/* Sticky bottom action bar */}
      <div className="fixed bottom-0 left-0 right-0 z-30 bg-white border-t border-slate-200 px-4 py-3 pb-safe pb-5">
        <div className="max-w-lg mx-auto flex gap-3">
          {step > 1 && step < 5 && (
            <button
              onClick={() => setStep(step - 1)}
              className="px-4 py-3 rounded-xl border border-slate-200 text-slate-600 text-sm font-medium"
            >
              Atrás
            </button>
          )}

          {step === 1 && (
            <div className="flex-1 text-center text-xs text-slate-400 flex items-center justify-center">
              Sube una foto o toca &ldquo;Ingresar manualmente&rdquo;
            </div>
          )}

          {step === 2 && bill && (
            <button
              onClick={() => setStep(3)}
              className="flex-1 bg-indigo-600 hover:bg-indigo-700 text-white font-semibold py-3 rounded-xl flex items-center justify-center gap-2"
            >
              Continuar <ChevronRight size={18} />
            </button>
          )}

          {step === 3 && bill && (
            <button
              onClick={() => setStep(4)}
              className="flex-1 bg-indigo-600 hover:bg-indigo-700 text-white font-semibold py-3 rounded-xl flex items-center justify-center gap-2"
            >
              Continuar <ChevronRight size={18} />
            </button>
          )}

          {step === 4 && bill && (
            <button
              onClick={handleSetPayers}
              className="flex-1 bg-indigo-600 hover:bg-indigo-700 text-white font-semibold py-3 rounded-xl flex items-center justify-center gap-2"
            >
              Ver resumen <ChevronRight size={18} />
            </button>
          )}

          {step === 5 && bill && !finalized && (
            <button
              onClick={handleFinalize}
              disabled={loading}
              className="flex-1 bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 text-white font-semibold py-3 rounded-xl flex items-center justify-center gap-2"
            >
              {loading ? (
                <div className="w-5 h-5 border-2 border-white/40 border-t-white rounded-full animate-spin" />
              ) : (
                <>Guardar en Lucas <Check size={18} /></>
              )}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
