"use client";
/**
 * Split — 5-step bill-splitting flow
 * 1 Capture → 2 Revisar + Asignar → 3 Participantes → 4 ¿Quién pagó? → 5 Resumen
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Account, Person, listAccounts, listPeople, createPerson, getToken, resolveBackendUrl } from "@/lib/api";
import { Camera, Plus, Trash2, Pencil, Check, ChevronRight, ChevronLeft, Share2, Users, Hand, Eraser, Sparkles, X } from "lucide-react";

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
const CIRCLED = ["①","②","③","④","⑤","⑥","⑦","⑧","⑨","⑩","⑪","⑫","⑬","⑭","⑮","⑯","⑰","⑱","⑲","⑳"];
const circled = (n: number) => CIRCLED[n - 1] ?? `(${n})`;
const ITEM_COLORS = ["#fef3c7","#dbeafe","#dcfce7","#fce7f3","#ede9fe","#ffedd5","#f0fdf4","#fdf4ff"];

function StepDots({ step }: { step: number }) {
  return (
    <div className="flex items-center justify-center gap-2 mb-4">
      {[1,2,3,4,5].map((s) => (
        <div key={s} className={`rounded-full transition-all ${s === step ? "w-6 h-2.5 bg-indigo-600" : s < step ? "w-2.5 h-2.5 bg-emerald-500" : "w-2.5 h-2.5 bg-slate-300"}`} />
      ))}
    </div>
  );
}

function Toast({ msg, onClose, kind = "error" }: { msg: string; onClose: () => void; kind?: "error" | "success" }) {
  useEffect(() => { const t = setTimeout(onClose, 3000); return () => clearTimeout(t); }, [msg, onClose]);
  const bg = kind === "success" ? "bg-emerald-600" : "bg-red-600";
  return <div className={`fixed top-4 left-1/2 -translate-x-1/2 z-50 ${bg} text-white text-sm px-4 py-2 rounded-xl shadow-lg max-w-xs text-center`}>{msg}</div>;
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
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);
  const [autoAssignBanner, setAutoAssignBanner] = useState(false);
  const [celebrate, setCelebrate] = useState(false);

  // Step 2 — items
  const [editItemId, setEditItemId] = useState<number | null>(null);
  const [editDraft, setEditDraft] = useState({ name: "", qty: 1, unit_price: 0 });
  const [newItem, setNewItem] = useState<{ name: string; qty: number; unit_price: number } | null>(null);

  // Step 3 — new person inline form
  const [newPersonForm, setNewPersonForm] = useState(false);
  const [newPersonName, setNewPersonName] = useState("");
  const [newPersonColor, setNewPersonColor] = useState("#6366f1");

  // Step 2 — assignments: per-item array of length item.qty
  //   each slot is participantId or null (unassigned)
  //   For qty=1 items, the array has length 1; toggling a participant chip
  //   adds/removes them across all slots (equal-split semantics).
  //   For qty>1 items, individual slots can be cycled.
  const [assignments, setAssignments] = useState<Map<number, (number | null)[]>>(new Map());

  // Mini-selector inline for qty>1 chip taps
  const [chipChoice, setChipChoice] = useState<{ itemId: number; pid: number } | null>(null);

  // Mini-selector inline for "÷ split into N" button (qty=1 items only)
  const [splitChoice, setSplitChoice] = useState<{ itemId: number; n: string } | null>(null);

  // Step 2 — image panel (split-screen viewer + drawing canvas)
  const [leftW, setLeftW] = useState(0.30); // fraction 0-1 for left image panel width
  const [imgScale, setImgScale] = useState(1);
  const [imgPan, setImgPan] = useState({ x: 0, y: 0 });
  const [drawMode, setDrawMode] = useState(false); // false = pan, true = draw
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const imgContainerRef = useRef<HTMLDivElement>(null);
  const splitContainerRef = useRef<HTMLDivElement>(null);
  const imgTransformRef = useRef({ scale: 1, x: 0, y: 0 });
  const pinchRef = useRef<{ dist: number; scale: number } | null>(null);
  const panStartRef = useRef<{ x: number; y: number; px: number; py: number } | null>(null);
  const drawingRef = useRef(false);
  const vDivRef = useRef<{ startX: number; startW: number } | null>(null);

  // Step 4 — payers
  const [payerMode, setPayerMode] = useState<"me" | "other" | "split">("me");
  const [otherPayer, setOtherPayer] = useState<number | null>(null);
  const [multiAmounts, setMultiAmounts] = useState<Record<number, string>>({});
  const [selectedAccountId, setSelectedAccountId] = useState<number | null>(null);

  // Step 5 — summary
  const [finalized, setFinalized] = useState(false);
  const [myShare, setMyShare] = useState(0);

  const showError = useCallback((msg: string) => setError(msg), []);
  const showSuccess = useCallback((msg: string) => setSuccessMsg(msg), []);

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
      // If participants already loaded (e.g. "Yo"), auto-assign + smart distribute
      if (b.participants.length > 0) {
        try {
          const assigned = await assignEqual(b.id);
          b = assigned;
        } catch { /* non-fatal */ }
        seedFromBill(b);
        const smart = smartDistribute(b);
        if (smart.size > 0) {
          setAssignments((prev) => {
            const merged = new Map(prev);
            smart.forEach((slots, itemId) => merged.set(itemId, slots));
            return merged;
          });
        }
        setAutoAssignBanner(true);
      }
      setBill(b);
      setStep(2);
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

  // ── Step 2 (items + assign) ────────────────────────────────────

  const subtotal = bill ? bill.items.reduce((s, i) => s + i.line_total, 0) : 0;

  async function saveEditItem() {
    if (!bill || editItemId === null) return;
    try {
      const b = await patchItem(bill.id, editItemId, editDraft);
      setBill(b);
      setEditItemId(null);
      // Re-seed assignments and re-apply smart distribution after item edit
      seedFromBill(b);
      const smart = smartDistribute(b);
      if (smart.size > 0) {
        setAssignments((prev) => {
          const merged = new Map(prev);
          smart.forEach((slots, itemId) => merged.set(itemId, slots));
          return merged;
        });
      }
    }
    catch (e: unknown) { showError(e instanceof Error ? e.message : "Error"); }
  }
  async function handleDeleteItem(iid: number) {
    if (!bill) return;
    try { setBill(await deleteItem(bill.id, iid)); }
    catch (e: unknown) { showError(e instanceof Error ? e.message : "Error"); }
  }
  async function handleAddItem() {
    if (!bill || !newItem?.name.trim()) return;
    try {
      const b = await addItem(bill.id, newItem);
      setBill(b);
      setNewItem(null);
      // Re-apply smart distribution so new item gets distributed
      seedFromBill(b);
      const smart = smartDistribute(b);
      if (smart.size > 0) {
        setAssignments((prev) => {
          const merged = new Map(prev);
          smart.forEach((slots, itemId) => merged.set(itemId, slots));
          return merged;
        });
      }
    }
    catch (e: unknown) { showError(e instanceof Error ? e.message : "Error"); }
  }

  // ── Step 3 ────────────────────────────────────────────────────

  async function toggleParticipant(pid: number) {
    if (!bill) return;
    const existing = bill.participants.find((p) => p.person_id === pid);
    try {
      let updated: Bill;
      if (existing) {
        if (existing.is_me) return;
        updated = await removeParticipant(bill.id, existing.id);
      } else {
        updated = await addParticipant(bill.id, pid);
      }
      setBill(updated);
      // Re-seed + re-apply smart distribution whenever participants change
      seedFromBill(updated);
      const smart = smartDistribute(updated);
      if (smart.size > 0) {
        setAssignments((prev) => {
          const merged = new Map(prev);
          smart.forEach((slots, itemId) => merged.set(itemId, slots));
          return merged;
        });
      }
    } catch (e: unknown) {
      showError(e instanceof Error ? e.message : "Error al actualizar participantes");
    }
  }

  async function handleCreatePerson() {
    if (!newPersonName.trim() || !bill) return;
    try {
      const person = await createPerson(newPersonName.trim(), newPersonColor);
      setPeople((prev) => [...prev, person]);
      const updated = await addParticipant(bill.id, person.id);
      setBill(updated);
      // Re-seed + re-apply smart distribution after adding new person
      seedFromBill(updated);
      const smart = smartDistribute(updated);
      if (smart.size > 0) {
        setAssignments((prev) => {
          const merged = new Map(prev);
          smart.forEach((slots, itemId) => merged.set(itemId, slots));
          return merged;
        });
      }
      setNewPersonName("");
      setNewPersonColor("#6366f1");
      setNewPersonForm(false);
    } catch (e: unknown) { showError(e instanceof Error ? e.message : "Error al crear persona"); }
  }

  // ── Assignments helpers ───────────────────────────────────────

  function seedFromBill(b: Bill) {
    const next = new Map<number, (number | null)[]>();
    const allPids = b.participants.map((p) => p.id);
    for (const item of b.items) {
      const slots: (number | null)[] = new Array(Math.max(item.qty, 1)).fill(null);
      if (item.shares.length === 0) {
        // not yet assigned → leave empty
      } else {
        const hasUnits = item.shares.some((s) => s.units != null && s.units > 0);
        if (hasUnits && item.qty > 1) {
          let idx = 0;
          for (const s of item.shares) {
            const u = Math.round(s.units ?? 0);
            for (let i = 0; i < u && idx < slots.length; i++) {
              slots[idx++] = s.participant_id;
            }
          }
        } else {
          const pids = item.shares.map((s) => s.participant_id).filter((p) => allPids.includes(p));
          if (item.qty === 1) {
            slots[0] = pids.length === 1 ? pids[0] : -1;
            (slots as unknown as { sharers: number[] }).sharers = pids;
          } else {
            for (let i = 0; i < slots.length; i++) slots[i] = pids[i % pids.length] ?? null;
          }
        }
      }
      next.set(item.id, slots);
    }
    setAssignments(next);
  }

  const slotsOf = (itemId: number, qty: number): (number | null)[] => {
    const cur = assignments.get(itemId);
    if (cur && cur.length === qty) return cur;
    return new Array(Math.max(qty, 1)).fill(null);
  };

  function cloneSlots(slots: (number | null)[]): (number | null)[] {
    const copy = [...slots];
    const sharers = (slots as unknown as { sharers?: number[] }).sharers;
    if (sharers) (copy as unknown as { sharers: number[] }).sharers = sharers.slice();
    return copy;
  }

  function isPersonOn(item: BillItem, pid: number): boolean {
    const slots = slotsOf(item.id, item.qty);
    if (item.qty === 1) {
      const s = slots[0];
      if (s === pid) return true;
      if (s === -1) {
        const sharers = (slots as unknown as { sharers?: number[] }).sharers;
        return !!sharers?.includes(pid);
      }
      return false;
    }
    return slots.includes(pid);
  }

  function unitsFor(item: BillItem, pid: number): number {
    const slots = slotsOf(item.id, item.qty);
    if (item.qty === 1) return isPersonOn(item, pid) ? 1 : 0;
    return slots.reduce((n: number, v) => n + (v === pid ? 1 : 0), 0);
  }

  function itemCostFor(item: BillItem, pid: number): number {
    if (!item.qty || item.qty <= 0 || item.unit_price <= 0) return 0;
    const slots = slotsOf(item.id, item.qty);
    if (item.qty === 1) {
      const s = slots[0];
      if (s === pid) return item.line_total;
      if (s === -1) {
        const sharers = (slots as unknown as { sharers?: number[] }).sharers ?? [];
        if (!sharers.includes(pid) || sharers.length === 0) return 0;
        return item.line_total / sharers.length;
      }
      return 0;
    }
    const assigned = slots.filter((v) => v !== null && v !== -1) as number[];
    if (assigned.length === 0) return 0;
    const myUnits = assigned.reduce((n, v) => n + (v === pid ? 1 : 0), 0);
    return myUnits * item.unit_price;
  }

  const runningTotals = useMemo<Map<number, number>>(() => {
    const m = new Map<number, number>();
    if (!bill) return m;
    for (const p of bill.participants) {
      let s = 0;
      for (const item of bill.items) s += itemCostFor(item, p.id);
      m.set(p.id, s);
    }
    return m;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bill, assignments]);
  const runningTotal = (pid: number) => runningTotals.get(pid) ?? 0;

  function itemFullyAssigned(item: BillItem): boolean {
    const slots = slotsOf(item.id, item.qty);
    if (item.qty === 1) {
      const s = slots[0];
      return s !== null;
    }
    return slots.every((v) => v !== null);
  }

  function itemPartiallyAssigned(item: BillItem): boolean {
    const slots = slotsOf(item.id, item.qty);
    return slots.some((v) => v !== null);
  }

  function toggleChip(item: BillItem, pid: number) {
    if (!bill) return;
    if (item.qty > 1) {
      const currentUnits = unitsFor(item, pid);
      if (currentUnits > 0) {
        setAssignments((prev) => {
          const next = new Map(prev);
          const cur = next.get(item.id) ?? new Array(item.qty).fill(null);
          const slots = cloneSlots(cur);
          for (let i = 0; i < slots.length; i++) if (slots[i] === pid) slots[i] = null;
          next.set(item.id, slots);
          return next;
        });
        return;
      }
      setChipChoice({ itemId: item.id, pid });
      return;
    }
    setAssignments((prev) => {
      const next = new Map(prev);
      const cur = next.get(item.id) ?? [null];
      const slots = cloneSlots(cur);
      const sharers = ((slots as unknown as { sharers?: number[] }).sharers ?? []).slice();
      let nextSharers: number[] = [];
      const first = slots[0];
      const currentSet = new Set<number>(
        first === -1 ? sharers : first != null ? [first] : []
      );
      if (currentSet.has(pid)) currentSet.delete(pid); else currentSet.add(pid);
      nextSharers = Array.from(currentSet);
      if (nextSharers.length === 0) {
        slots[0] = null;
        delete (slots as unknown as { sharers?: number[] }).sharers;
      } else if (nextSharers.length === 1) {
        slots[0] = nextSharers[0];
        delete (slots as unknown as { sharers?: number[] }).sharers;
      } else {
        slots[0] = -1;
        (slots as unknown as { sharers: number[] }).sharers = nextSharers;
      }
      next.set(item.id, slots);
      return next;
    });
  }

  function applyChipChoice(mode: "all" | "half") {
    if (!chipChoice || !bill) return;
    const item = bill.items.find((i) => i.id === chipChoice.itemId);
    if (!item) { setChipChoice(null); return; }
    setAssignments((prev) => {
      const next = new Map(prev);
      const cur = next.get(item.id) ?? new Array(item.qty).fill(null);
      const slots = cloneSlots(cur);
      const target = mode === "all" ? item.qty : Math.max(1, Math.floor(item.qty / 2));
      let placed = 0;
      for (let i = 0; i < slots.length && placed < target; i++) {
        if (slots[i] === null) { slots[i] = chipChoice.pid; placed++; }
      }
      for (let i = 0; i < slots.length && placed < target; i++) {
        if (slots[i] !== chipChoice.pid) { slots[i] = chipChoice.pid; placed++; }
      }
      next.set(item.id, slots);
      return next;
    });
    setChipChoice(null);
  }

  async function applySplit(item: BillItem) {
    if (!bill || !splitChoice) return;
    const n = parseInt(splitChoice.n, 10);
    if (!Number.isFinite(n) || n < 2 || n > 50) {
      showError("Elige un número entre 2 y 50");
      return;
    }
    if (item.line_total <= 0) {
      showError("El ítem no tiene precio");
      return;
    }
    const unit_price = Math.round(item.line_total / n);
    setBusy(true);
    try {
      // Borra el ítem original y crea N ítems separados — uno por unidad.
      // El usuario quiere ver "Completo $4.000" repetido N veces, no qty=N.
      await deleteItem(bill.id, item.id);
      const results = await Promise.all(
        Array.from({ length: n }, () =>
          addItem(bill.id, { name: item.name, qty: 1, unit_price })
        )
      );
      const b = results[results.length - 1];
      setBill(b);
      seedFromBill(b);
      const smart = smartDistribute(b);
      if (smart.size > 0) {
        setAssignments((prev) => {
          const merged = new Map(prev);
          smart.forEach((slots, itemId) => merged.set(itemId, slots));
          return merged;
        });
      }
      setSplitChoice(null);
      showSuccess(`Dividido en ${n} × ${clp(unit_price)}`);
    } catch (e: unknown) {
      showError(e instanceof Error ? e.message : "Error");
    } finally {
      setBusy(false);
    }
  }

  function cycleSlot(itemId: number, slotIdx: number) {
    if (!bill) return;
    setAssignments((prev) => {
      const next = new Map(prev);
      const item = bill.items.find((i) => i.id === itemId);
      if (!item) return prev;
      const curArr = next.get(itemId) ?? new Array(item.qty).fill(null);
      const slots = cloneSlots(curArr);
      const sortedPids = [...bill.participants].sort((a, b) => a.id - b.id).map((p) => p.id);
      const order: (number | null)[] = [...sortedPids, null];
      const curVal = slots[slotIdx];
      const idx = order.findIndex((v) => v === curVal);
      const nxt = order[(idx + 1) % order.length];
      slots[slotIdx] = nxt;
      if (item.qty === 1 && curVal === -1) {
        delete (slots as unknown as { sharers?: number[] }).sharers;
      }
      next.set(itemId, slots);
      return next;
    });
  }

  function selectAll(item: BillItem) {
    if (!bill) return;
    setAssignments((prev) => {
      const next = new Map(prev);
      const pids = bill.participants.map((p) => p.id);
      if (pids.length === 0) { next.set(item.id, new Array(item.qty).fill(null)); return next; }
      if (item.qty === 1) {
        const slots: (number | null)[] = [pids.length === 1 ? pids[0] : -1];
        if (pids.length > 1) (slots as unknown as { sharers: number[] }).sharers = pids.slice();
        next.set(item.id, slots);
      } else {
        const slots: (number | null)[] = new Array(item.qty).fill(null);
        for (let i = 0; i < item.qty; i++) slots[i] = pids[i % pids.length];
        next.set(item.id, slots);
      }
      return next;
    });
  }

  function smartDistribute(b: Bill): Map<number, (number | null)[]> {
    const pids = b.participants.map((p) => p.id);
    const N = pids.length;
    const next = new Map<number, (number | null)[]>();
    for (const item of b.items) {
      if (item.qty <= 1 || N === 0) continue;
      const slots: (number | null)[] = new Array(item.qty).fill(null);
      const base = Math.floor(item.qty / N);
      const rem = item.qty % N;
      let idx = 0;
      for (let i = 0; i < N; i++) {
        const units = base + (i < rem ? 1 : 0);
        for (let u = 0; u < units; u++) {
          if (idx < slots.length) slots[idx++] = pids[i];
        }
      }
      next.set(item.id, slots);
    }
    return next;
  }

  // Triggered when navigating from step 3 (Participants) back into step 2,
  // or when the user wants to "Dividir todo igual" from inside step 2.
  async function handleAssignEqual() {
    if (!bill) return;
    setBusy(true);
    try {
      const b = await assignEqual(bill.id);
      setBill(b);
      seedFromBill(b);
      const smart = smartDistribute(b);
      if (smart.size > 0) {
        setAssignments((prev) => {
          const merged = new Map(prev);
          smart.forEach((slots, itemId) => merged.set(itemId, slots));
          return merged;
        });
      }
    } catch (e: unknown) { showError(e instanceof Error ? e.message : "Error"); }
    finally { setBusy(false); }
  }

  function sharesForItem(item: BillItem): { participant_id: number; weight: number; units?: number }[] {
    const slots = slotsOf(item.id, item.qty);
    if (item.qty === 1) {
      const s = slots[0];
      if (s === null) return [];
      if (s === -1) {
        const sharers = (slots as unknown as { sharers?: number[] }).sharers ?? [];
        const n = sharers.length;
        if (n === 0) return [];
        const w = 1 / n;
        return sharers.map((pid) => ({ participant_id: pid, weight: w }));
      }
      return [{ participant_id: s, weight: 1 }];
    }
    const counts = new Map<number, number>();
    for (const v of slots) {
      if (v == null || v === -1) continue;
      counts.set(v, (counts.get(v) ?? 0) + 1);
    }
    const totalUnits = Array.from(counts.values()).reduce((a, b) => a + b, 0);
    if (totalUnits === 0) return [];
    return Array.from(counts.entries()).map(([pid, units]) => ({
      participant_id: pid,
      units,
      weight: units / totalUnits,
    }));
  }

  async function goToWhoPaid() {
    if (!bill) return;
    if (bill.participants.length === 0) { showError("Agrega al menos un participante"); return; }
    const allAssigned = bill.items.length > 0 && bill.items.every((i) => itemFullyAssigned(i));
    if (!allAssigned) { showError("Asigna todos los ítems primero"); return; }
    setBusy(true);
    let b = bill;
    const items = bill.items;
    try {
      for (const item of items) {
        const shares = sharesForItem(item);
        if (shares.length === 0) continue;
        const sum = shares.reduce((s, x) => s + x.weight, 0);
        if (Math.abs(sum - 1) > 0.01) {
          const last = shares[shares.length - 1];
          last.weight = last.weight + (1 - sum);
        }
        b = await postShares(b.id, item.id, shares);
      }
      setBill(b);
      setStep(4);
    } catch (e: unknown) {
      setBill(b);
      showError(e instanceof Error ? e.message : "Error");
    } finally { setBusy(false); }
  }

  const assignProgress = useMemo(() => {
    if (!bill) return { done: 0, total: 0 };
    const total = bill.items.length;
    const done = bill.items.filter((i) => itemFullyAssigned(i)).length;
    return { done, total };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bill, assignments]);

  // Celebrate when all items are fully assigned (only inside step 2 when participants exist)
  useEffect(() => {
    if (step !== 2 || !bill || bill.items.length === 0 || bill.participants.length === 0) return;
    if (assignProgress.done === assignProgress.total) {
      setCelebrate(true);
      const t = setTimeout(() => setCelebrate(false), 1400);
      return () => clearTimeout(t);
    }
  }, [step, bill, assignProgress.done, assignProgress.total]);

  // Close inline popovers if user leaves step 2
  useEffect(() => {
    if (step !== 2) { setSplitChoice(null); setChipChoice(null); }
  }, [step]);

  // ── Image viewer (zoom + pan + draw) ─────────────────────────

  useEffect(() => {
    if (step !== 2) return;
    const container = imgContainerRef.current;
    const canvas = canvasRef.current;
    if (!container || !canvas) return;
    const sync = () => {
      const w = container.clientWidth, h = container.clientHeight;
      if (canvas.width === w && canvas.height === h) return;
      const prev = document.createElement("canvas");
      prev.width = canvas.width; prev.height = canvas.height;
      prev.getContext("2d")?.drawImage(canvas, 0, 0);
      canvas.width = w; canvas.height = h;
      if (prev.width > 0 && prev.height > 0) canvas.getContext("2d")?.drawImage(prev, 0, 0, w, h);
    };
    sync();
    const ro = new ResizeObserver(sync);
    ro.observe(container);
    return () => ro.disconnect();
  }, [step, leftW, bill?.image_url]);

  const applyTransform = (scale: number, x: number, y: number) => {
    imgTransformRef.current = { scale, x, y };
    setImgScale(scale); setImgPan({ x, y });
  };

  const onImgTouchStart = (e: React.TouchEvent) => {
    if (drawMode) return;
    const t = e.touches;
    if (t.length === 2) {
      const dx = t[0].clientX - t[1].clientX, dy = t[0].clientY - t[1].clientY;
      pinchRef.current = { dist: Math.hypot(dx, dy), scale: imgTransformRef.current.scale };
    } else if (t.length === 1 && imgTransformRef.current.scale > 1) {
      panStartRef.current = { x: t[0].clientX, y: t[0].clientY, px: imgTransformRef.current.x, py: imgTransformRef.current.y };
    }
  };
  const onImgTouchMove = (e: React.TouchEvent) => {
    if (drawMode) return;
    const t = e.touches;
    if (t.length === 2 && pinchRef.current) {
      const dx = t[0].clientX - t[1].clientX, dy = t[0].clientY - t[1].clientY;
      const ratio = Math.hypot(dx, dy) / pinchRef.current.dist;
      const next = Math.min(4, Math.max(1, pinchRef.current.scale * ratio));
      const { x, y } = imgTransformRef.current;
      next === 1 ? applyTransform(1, 0, 0) : applyTransform(next, x, y);
    } else if (t.length === 1 && panStartRef.current) {
      const dx = t[0].clientX - panStartRef.current.x, dy = t[0].clientY - panStartRef.current.y;
      applyTransform(imgTransformRef.current.scale, panStartRef.current.px + dx, panStartRef.current.py + dy);
    }
  };
  const onImgTouchEnd = (e: React.TouchEvent) => {
    if (e.touches.length < 2) pinchRef.current = null;
    if (e.touches.length === 0) panStartRef.current = null;
  };

  const onImgMouseDown = (e: React.MouseEvent) => {
    if (drawMode || imgTransformRef.current.scale <= 1) return;
    panStartRef.current = { x: e.clientX, y: e.clientY, px: imgTransformRef.current.x, py: imgTransformRef.current.y };
  };
  const onImgMouseMove = (e: React.MouseEvent) => {
    if (!panStartRef.current || drawMode) return;
    const dx = e.clientX - panStartRef.current.x, dy = e.clientY - panStartRef.current.y;
    applyTransform(imgTransformRef.current.scale, panStartRef.current.px + dx, panStartRef.current.py + dy);
  };
  const onImgMouseUp = () => { panStartRef.current = null; };

  const canvasPoint = (clientX: number, clientY: number) => {
    const c = canvasRef.current;
    if (!c) return { x: 0, y: 0 };
    const r = c.getBoundingClientRect();
    return { x: (clientX - r.left) * (c.width / r.width), y: (clientY - r.top) * (c.height / r.height) };
  };
  const onCanvasPointerDown = (e: React.PointerEvent<HTMLCanvasElement>) => {
    if (!drawMode) return;
    const c = canvasRef.current; if (!c) return;
    const ctx = c.getContext("2d"); if (!ctx) return;
    c.setPointerCapture(e.pointerId);
    const { x, y } = canvasPoint(e.clientX, e.clientY);
    ctx.strokeStyle = "rgba(220, 38, 38, 0.75)";
    ctx.lineWidth = 1.5; ctx.lineCap = "round"; ctx.lineJoin = "round";
    ctx.beginPath(); ctx.moveTo(x, y);
    drawingRef.current = true;
  };
  const onCanvasPointerMove = (e: React.PointerEvent<HTMLCanvasElement>) => {
    if (!drawMode || !drawingRef.current) return;
    const c = canvasRef.current; if (!c) return;
    const ctx = c.getContext("2d"); if (!ctx) return;
    const { x, y } = canvasPoint(e.clientX, e.clientY);
    ctx.lineTo(x, y); ctx.stroke();
  };
  const onCanvasPointerUp = () => { drawingRef.current = false; };
  const clearCanvas = () => {
    const c = canvasRef.current; if (!c) return;
    c.getContext("2d")?.clearRect(0, 0, c.width, c.height);
  };

  useEffect(() => {
    const onMove = (e: PointerEvent) => {
      if (!vDivRef.current) return;
      const container = splitContainerRef.current;
      const containerW = container?.clientWidth || window.innerWidth;
      const dx = e.clientX - vDivRef.current.startX;
      const newW = Math.min(0.55, Math.max(0.25, vDivRef.current.startW + dx / containerW));
      requestAnimationFrame(() => setLeftW(newW));
    };
    const onUp = () => { vDivRef.current = null; };
    document.addEventListener("pointermove", onMove);
    document.addEventListener("pointerup", onUp);
    return () => {
      document.removeEventListener("pointermove", onMove);
      document.removeEventListener("pointerup", onUp);
    };
  }, []);
  const onVDividerDown = (e: React.PointerEvent) => { vDivRef.current = { startX: e.clientX, startW: leftW }; };
  const resetImgTransform = () => applyTransform(1, 0, 0);

  // ── Step 4 ────────────────────────────────────────────────────

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
    setBusy(true);
    try { setBill(await setPayers(bill.id, payers)); setStep(5); }
    catch (e: unknown) { showError(e instanceof Error ? e.message : "Error"); }
    finally { setBusy(false); }
  }

  // ── Step 5 ────────────────────────────────────────────────────

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

  const stepLabels: Record<number, string> = {
    1: "Dividir cuenta",
    2: bill?.merchant || "Revisar",
    3: "Participantes",
    4: "¿Quién pagó?",
    5: "Resumen",
  };

  const splitSum = Object.values(multiAmounts).reduce((s, v) => s + (parseFloat(v) || 0), 0);
  const splitMatches = bill ? Math.abs(splitSum - bill.total_amount) <= 1 : false;

  // Pre-finalize: compute "Yo" total locally from current shares so the summary
  // card doesn't show $0 while the backend still has owes_amount=0.
  const previewMyShare = useMemo(() => {
    if (!bill) return 0;
    const me = bill.participants.find((p) => p.is_me);
    if (!me) return 0;
    let total = 0;
    for (const item of bill.items) {
      const share = item.shares.find((s) => s.participant_id === me.id);
      if (!share) continue;
      total += item.line_total * share.weight;
    }
    if (bill.tip_amount > 0) {
      const totalOwed = bill.participants.reduce((acc, p) => {
        let s = 0;
        for (const item of bill.items) {
          const sh = item.shares.find((x) => x.participant_id === p.id);
          if (sh) s += item.line_total * sh.weight;
        }
        return acc + s;
      }, 0);
      if (totalOwed > 0) total += (total / totalOwed) * bill.tip_amount;
    }
    return Math.round(total);
  }, [bill]);

  // Renders the people row (avatars with running totals + "Add new").
  // Shown above the split-screen as a horizontal sticky bar.
  function renderStep2PeopleBar() {
    if (!bill) return null;
    const hasParticipants = bill.participants.length > 0;
    if (!hasParticipants) return null;
    return (
      <div className="bg-white border-b border-slate-200 px-3 py-2">
        <div className="max-w-lg mx-auto">
          <div className="flex items-center justify-between text-[11px] mb-1.5">
            <span className="text-slate-500 font-medium">
              {assignProgress.done} de {assignProgress.total} ítems asignados
            </span>
            <button onClick={handleAssignEqual} className="text-indigo-600 font-medium">
              Dividir todo igual
            </button>
          </div>
          <div className="w-full h-1 bg-slate-100 rounded-full overflow-hidden mb-2">
            <div
              className="h-full bg-emerald-500 transition-all"
              style={{ width: assignProgress.total > 0 ? `${(assignProgress.done / assignProgress.total) * 100}%` : "0%" }}
            />
          </div>
          <div className="flex gap-2 overflow-x-auto pb-1 items-start">
            {bill.participants.map((p) => (
              <div key={p.id} className="flex flex-col items-center gap-0.5 shrink-0 min-w-[52px]">
                <div
                  className="w-9 h-9 rounded-full flex items-center justify-center text-white text-[11px] font-bold"
                  style={{ background: p.color }}
                >
                  {initials(p.name)}
                </div>
                <span className="text-[10px] text-slate-500 max-w-[52px] truncate">{p.name.split(" ")[0]}</span>
                <span className="text-[10px] font-semibold text-slate-700">{clp(runningTotal(p.id))}</span>
              </div>
            ))}
            <button
              type="button"
              onClick={() => setStep(3)}
              className="flex flex-col items-center gap-0.5 shrink-0 min-w-[52px] opacity-70 hover:opacity-100"
              title="Agregar persona"
            >
              <div className="w-9 h-9 rounded-full border-2 border-dashed border-slate-300 flex items-center justify-center text-slate-400">
                <Plus size={16} />
              </div>
              <span className="text-[10px] text-slate-500">Agregar</span>
            </button>
          </div>
        </div>
      </div>
    );
  }

  // Renders the right-side content of step 2 (revisar + asignar).
  // Used both in split-screen (with image) and column layout (no image).
  function renderStep2RightPanel() {
    if (!bill) return null;
    const diff = Math.abs(subtotal - bill.total_amount);
    const match = diff < 2 || bill.total_amount === 0;
    const hasParticipants = bill.participants.length > 0;
    const allAssigned = bill.items.length > 0 && bill.items.every((i) => itemFullyAssigned(i));

    return (
      <div className="space-y-3 pb-6">
        {/* Auto-assign banner */}
        {autoAssignBanner && hasParticipants && (
          <div className="bg-indigo-50 border border-indigo-200 text-indigo-700 rounded-xl px-3 py-2 text-xs flex items-start gap-2">
            <Sparkles size={14} className="mt-0.5 shrink-0" />
            <span className="flex-1">Asignamos ítems automáticamente. Revisa y ajusta tocando los chips.</span>
            <button
              onClick={() => setAutoAssignBanner(false)}
              className="text-indigo-400 hover:text-indigo-700 shrink-0"
              aria-label="Cerrar aviso"
            >
              <X size={14} />
            </button>
          </div>
        )}

        {/* Total banner */}
        <div className={`rounded-xl px-4 py-2.5 text-sm font-medium ${match ? "bg-emerald-50 text-emerald-700 border border-emerald-200" : "bg-amber-50 text-amber-700 border border-amber-200"}`}>
          <div className="flex items-center justify-between">
            <span>{bill.items.length} ítems</span>
            <span>Total {clp(bill.total_amount || subtotal)}</span>
          </div>
          {!match && (
            <p className="text-[11px] font-normal mt-0.5">
              Suma de ítems: {clp(subtotal)} · Boleta: {clp(bill.total_amount)}
            </p>
          )}
        </div>

        {/* Items list */}
        {bill.items.map((item, idx) => {
          const slots = slotsOf(item.id, item.qty);
          const full = itemFullyAssigned(item);
          const partial = itemPartiallyAssigned(item);
          const itemBg = ITEM_COLORS[idx % ITEM_COLORS.length];
          return (
            <div
              key={item.id}
              className={`rounded-xl shadow-sm overflow-hidden border ${
                hasParticipants
                  ? full ? "border-emerald-300" : partial ? "border-amber-200" : "border-slate-200"
                  : "border-slate-200"
              }`}
              style={{ background: itemBg }}
            >
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
                <div className="px-4 py-3">
                  <div className="flex items-center gap-2 mb-2">
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-slate-800 leading-snug line-clamp-2">{item.qty > 1 ? `${item.qty}× ` : ""}{item.name}</p>
                      <p className="text-[11px] text-slate-400 whitespace-nowrap">{clp(item.unit_price)} c/u · {clp(item.line_total)}</p>
                    </div>
                    {hasParticipants && (
                      <button
                        onClick={() =>
                          setSplitChoice(
                            splitChoice?.itemId === item.id
                              ? null
                              : { itemId: item.id, n: String(Math.max(2, bill.participants.length || 2)) }
                          )
                        }
                        className="px-2 py-1 rounded-full text-xs font-bold bg-slate-100 text-slate-500 hover:bg-indigo-100 hover:text-indigo-600 transition-colors"
                        title={item.qty === 1 ? "Dividir ítem en varios" : "Re-dividir ítem"}
                      >
                        ÷
                      </button>
                    )}
                    {hasParticipants && (
                      <button
                        onClick={() => selectAll(item)}
                        className="text-[11px] text-indigo-600 font-medium"
                      >
                        Todos
                      </button>
                    )}
                    <button onClick={() => { setEditItemId(item.id); setEditDraft({ name: item.name, qty: item.qty, unit_price: item.unit_price }); }} className="text-slate-400 hover:text-indigo-600 p-1"><Pencil size={15} /></button>
                    <button onClick={() => handleDeleteItem(item.id)} className="text-slate-400 hover:text-red-500 p-1"><Trash2 size={15} /></button>
                  </div>

                  {/* Numbered slots for qty>1 (only if there are participants) */}
                  {hasParticipants && item.qty > 1 && (
                    item.qty >= 4 ? (
                      <div className="space-y-1 mb-2">
                        {slots.map((slot, idx) => {
                          const pid = typeof slot === "number" && slot > 0 ? slot : null;
                          const p = pid ? bill.participants.find((x) => x.id === pid) : null;
                          return (
                            <button
                              key={idx}
                              onClick={() => cycleSlot(item.id, idx)}
                              className="w-full flex items-center gap-2 px-2 py-1 rounded-lg text-xs transition-colors hover:bg-slate-50 active:scale-[0.98]"
                              style={{ borderLeft: `3px solid ${p?.color ?? "#fbbf24"}` }}
                            >
                              <span className="font-bold text-slate-400 w-5 text-center">{idx + 1}</span>
                              <span className="flex-1 text-left" style={{ color: p?.color ?? "#b45309" }}>
                                {p ? p.name.split(" ")[0] : "Sin asignar"}
                              </span>
                              <span className="text-slate-400">{clp(item.unit_price)}</span>
                            </button>
                          );
                        })}
                      </div>
                    ) : (
                      <div className="flex flex-wrap gap-1.5 mb-2">
                        {slots.map((slot, idx) => {
                          const owner = slot != null && slot !== -1 ? bill.participants.find((p) => p.id === slot) : null;
                          return (
                            <button
                              key={idx}
                              onClick={() => cycleSlot(item.id, idx)}
                              className={`flex items-center gap-1 px-2 py-1 rounded-full text-[11px] font-medium border ${
                                owner
                                  ? "border-transparent text-white"
                                  : "border-dashed border-slate-300 text-slate-400 bg-white"
                              }`}
                              style={owner ? { background: owner.color } : undefined}
                            >
                              <span>{circled(idx + 1)}</span>
                              <span>{owner ? owner.name.split(" ")[0] : "—"}</span>
                            </button>
                          );
                        })}
                      </div>
                    )
                  )}

                  {/* Participant chips */}
                  {hasParticipants && (
                    <div className="flex flex-wrap gap-1.5">
                      {bill.participants.map((p) => {
                        const on = isPersonOn(item, p.id);
                        const u = unitsFor(item, p.id);
                        return (
                          <button
                            key={p.id}
                            onClick={() => toggleChip(item, p.id)}
                            className={`flex items-center gap-1 px-2 py-1 rounded-full text-[11px] font-medium border transition-colors ${
                              on
                                ? "text-white border-transparent"
                                : "bg-slate-50 text-slate-600 border-slate-200"
                            }`}
                            style={on ? { background: p.color } : undefined}
                          >
                            <span>{p.name.split(" ")[0]}</span>
                            {item.qty > 1 && u > 0 && <span className="opacity-80">×{u}</span>}
                          </button>
                        );
                      })}
                    </div>
                  )}

                  {/* Inline qty>1 choice popover */}
                  {chipChoice && chipChoice.itemId === item.id && (
                    <div className="mt-2 bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 flex items-center gap-2">
                      <span className="text-[11px] text-slate-600 flex-1">
                        ¿Cuántos para {bill.participants.find((p) => p.id === chipChoice.pid)?.name.split(" ")[0]}?
                      </span>
                      <button
                        onClick={() => applyChipChoice("half")}
                        className="px-2 py-1 rounded-md bg-white border border-slate-200 text-[11px] text-slate-700"
                      >
                        Mitad
                      </button>
                      <button
                        onClick={() => applyChipChoice("all")}
                        className="px-2 py-1 rounded-md bg-indigo-600 text-white text-[11px] font-medium"
                      >
                        Todos
                      </button>
                      <button
                        onClick={() => setChipChoice(null)}
                        className="text-slate-400 text-[11px]"
                      >
                        ✕
                      </button>
                    </div>
                  )}

                  {/* Inline "÷ split into N" popover */}
                  {splitChoice && splitChoice.itemId === item.id && (
                    <div className="mt-2 bg-indigo-50 border border-indigo-200 rounded-xl p-3 space-y-2">
                      <p className="text-xs text-indigo-700 font-medium">¿En cuántas unidades dividir?</p>
                      <div className="flex gap-2">
                        {[bill.participants.length, 2, 3, 4]
                          .filter((v, i, a) => v >= 2 && a.indexOf(v) === i)
                          .slice(0, 4)
                          .map((n) => (
                            <button
                              key={n}
                              onClick={() => setSplitChoice({ itemId: item.id, n: String(n) })}
                              className={`px-3 py-1.5 rounded-full text-xs font-semibold border-2 transition-colors ${
                                splitChoice.n === String(n)
                                  ? "bg-indigo-600 text-white border-indigo-600"
                                  : "bg-white text-indigo-600 border-indigo-300"
                              }`}
                            >
                              {n}
                            </button>
                          ))}
                        <input
                          type="number"
                          min={2}
                          max={50}
                          value={splitChoice.n}
                          onChange={(e) => setSplitChoice({ itemId: item.id, n: e.target.value })}
                          className="w-14 border border-slate-200 rounded-lg px-2 py-1 text-xs text-center"
                        />
                      </div>
                      {parseInt(splitChoice.n) >= 2 && (
                        <p className="text-[11px] text-slate-500">
                          {parseInt(splitChoice.n)} × {clp(Math.round(item.line_total / parseInt(splitChoice.n)))} c/u
                        </p>
                      )}
                      <div className="flex gap-2">
                        <button
                          onClick={() => applySplit(item)}
                          disabled={busy}
                          className="flex-1 bg-indigo-600 text-white text-xs font-semibold py-2 rounded-lg disabled:opacity-50"
                        >
                          Dividir
                        </button>
                        <button
                          onClick={() => setSplitChoice(null)}
                          className="px-3 text-slate-400 text-xs"
                        >
                          ×
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}

        {/* Add item */}
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

        {/* Primary CTA */}
        {!hasParticipants ? (
          <button
            onClick={() => setStep(3)}
            disabled={bill.items.length === 0}
            className="w-full bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed text-white font-semibold py-3.5 rounded-xl flex items-center justify-center gap-2 mt-2"
          >
            Participantes <ChevronRight size={18} />
          </button>
        ) : (
          <button
            onClick={goToWhoPaid}
            disabled={busy || !allAssigned}
            title={!allAssigned ? "Asigna todos los ítems primero" : undefined}
            className="w-full bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed text-white font-semibold py-3.5 rounded-xl flex items-center justify-center gap-2 mt-2"
          >
            {busy ? (
              <>
                <div className="w-5 h-5 border-2 border-white/40 border-t-white rounded-full animate-spin" />
                Guardando…
              </>
            ) : !allAssigned ? (
              <>Asigna todos los ítems ({assignProgress.done}/{assignProgress.total})</>
            ) : (
              <>¿Quién pagó? <ChevronRight size={18} /></>
            )}
          </button>
        )}
      </div>
    );
  }

  return (
    <div className="h-[100dvh] bg-slate-50 flex flex-col overflow-hidden">
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

      {/* Content (steps 1, 3, 4, 5) */}
      {step !== 2 && (
      <div className="flex-1 min-h-0 overflow-y-auto max-w-lg mx-auto w-full px-4 py-4 pb-10">

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

        {/* ── STEP 3: Participants ── */}
        {step === 3 && bill && (
          <div className="space-y-5">
            <p className="text-sm text-slate-500">Toca para agregar o quitar personas de la cuenta.</p>
            <div className="flex flex-wrap gap-4 items-start">
              {people.map((p) => {
                const inBill = bill.participants.some((bp) => bp.person_id === p.id);
                return (
                  <div key={p.id} className="relative">
                    <Avatar name={p.name} color={p.color} selected={inBill} locked={!!p.is_me} onClick={() => toggleParticipant(p.id)} />
                    {p.is_me && <span className="absolute -top-1 -right-1 bg-indigo-600 text-white text-[8px] font-bold px-1 rounded-full">Yo</span>}
                  </div>
                );
              })}
              {!newPersonForm && (
                <button
                  type="button"
                  onClick={() => setNewPersonForm(true)}
                  className="flex flex-col items-center gap-1 opacity-60 hover:opacity-100 transition-opacity"
                >
                  <div className="w-11 h-11 rounded-full border-2 border-dashed border-slate-300 flex items-center justify-center text-slate-400">
                    <Plus size={18} />
                  </div>
                  <span className="text-[10px] text-slate-500">Nueva</span>
                </button>
              )}
            </div>
            {newPersonForm && (
              <div className="flex items-center gap-2 p-3 bg-white rounded-xl shadow-sm w-full flex-wrap">
                <input
                  autoFocus
                  className="flex-1 min-w-[120px] border border-slate-200 rounded-lg px-3 py-2 text-sm"
                  placeholder="Nombre"
                  value={newPersonName}
                  onChange={(e) => setNewPersonName(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") handleCreatePerson(); }}
                />
                <div className="flex gap-1">
                  {["#6366f1","#ec4899","#f59e0b","#10b981","#3b82f6","#ef4444"].map((c) => (
                    <button
                      key={c}
                      type="button"
                      onClick={() => setNewPersonColor(c)}
                      className={`w-6 h-6 rounded-full border-2 ${newPersonColor === c ? "border-slate-800" : "border-transparent"}`}
                      style={{ background: c }}
                      aria-label={`Color ${c}`}
                    />
                  ))}
                </div>
                <button
                  type="button"
                  onClick={handleCreatePerson}
                  className="bg-indigo-600 text-white text-sm px-3 py-2 rounded-lg"
                >
                  OK
                </button>
                <button
                  type="button"
                  onClick={() => { setNewPersonForm(false); setNewPersonName(""); }}
                  className="text-slate-400 text-sm px-2"
                  aria-label="Cancelar"
                >
                  ×
                </button>
              </div>
            )}
            <div className="bg-slate-100 rounded-xl px-4 py-3 text-sm text-slate-600">
              <Users size={14} className="inline mr-1.5 mb-0.5" />
              {bill.participants.length} participante{bill.participants.length !== 1 ? "s" : ""}:&nbsp;
              {bill.participants.map((p) => p.name.split(" ")[0]).join(", ")}
            </div>
            <button
              onClick={() => {
                if (bill.participants.length === 0) { showError("Agrega al menos un participante"); return; }
                setAutoAssignBanner(true);
                setStep(2);
              }}
              disabled={busy || bill.participants.length === 0}
              className="w-full bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed text-white font-semibold py-3.5 rounded-xl flex items-center justify-center gap-2 mt-2"
            >
              {busy ? <><div className="w-5 h-5 border-2 border-white/40 border-t-white rounded-full animate-spin" /> Preparando…</> : <>Volver a asignar <ChevronRight size={18} /></>}
            </button>
          </div>
        )}

        {/* ── STEP 4: Who paid ── */}
        {step === 4 && bill && (
          <div className="space-y-4">
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
            <button onClick={() => setPayerMode("other")} className={`w-full rounded-2xl p-4 text-left border-2 transition-colors ${payerMode === "other" ? "border-indigo-500 bg-indigo-50" : "border-slate-200 bg-white"}`}>
              <p className="font-semibold text-slate-800">Pagó otra persona</p>
              {payerMode === "other" && (
                <div className="flex gap-3 mt-3 flex-wrap" onClick={(e) => e.stopPropagation()}>
                  {bill.participants.filter((p) => !p.is_me).map((p) => <Avatar key={p.id} name={p.name} color={p.color} selected={otherPayer === p.id} onClick={() => setOtherPayer(p.id)} />)}
                </div>
              )}
            </button>
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
                  <div className="flex justify-between text-xs pt-1">
                    <span className="text-slate-400">Total: {clp(bill.total_amount)}</span>
                    <span className={splitMatches ? "text-emerald-600 font-medium" : "text-red-600 font-medium"}>
                      Ingresado: {clp(splitSum)}{!splitMatches && " ✗"}
                    </span>
                  </div>
                </div>
              )}
            </button>
            <button
              onClick={handleSetPayers}
              disabled={busy}
              className="w-full bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white font-semibold py-3.5 rounded-xl flex items-center justify-center gap-2 mt-2"
            >
              {busy ? <><div className="w-5 h-5 border-2 border-white/40 border-t-white rounded-full animate-spin" /> Guardando…</> : <>Ver resumen <ChevronRight size={18} /></>}
            </button>
          </div>
        )}

        {/* ── STEP 5: Summary ── */}
        {step === 5 && bill && (
          <div className="space-y-4">
            <div className="bg-indigo-600 rounded-2xl px-5 py-6 text-white text-center">
              <p className="text-sm opacity-80 mb-1">Tu gasto personal</p>
              <p className="text-4xl font-extrabold">{clp(finalized ? myShare : (bill.participants.find((p) => p.is_me)?.owes_amount || previewMyShare))}</p>
              {finalized && <p className="text-sm mt-2 opacity-80">Guardado en Lucas ✓</p>}
            </div>

            {/* Per-person owes/paid summary */}
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

            {/* Per-item breakdown */}
            <details className="bg-white rounded-2xl shadow-sm overflow-hidden">
              <summary className="px-4 py-3 text-sm font-semibold text-slate-700 cursor-pointer select-none">
                Desglose por ítem
              </summary>
              <div className="divide-y divide-slate-100">
                {bill.items.map((item) => {
                  const cost = (pid: number) => itemCostFor(item, pid);
                  const eaters = bill.participants.filter((p) => cost(p.id) > 0.01);
                  return (
                    <div key={item.id} className="px-4 py-3">
                      <div className="flex justify-between items-center mb-1.5">
                        <p className="text-xs font-medium text-slate-800 truncate">
                          {item.qty > 1 ? `${item.qty}× ` : ""}{item.name}
                        </p>
                        <span className="text-xs text-slate-500">{clp(item.line_total)}</span>
                      </div>
                      {eaters.length === 0 ? (
                        <p className="text-[11px] text-slate-400 italic">Sin asignar</p>
                      ) : (
                        <div className="space-y-0.5">
                          {eaters.map((p) => (
                            <div key={p.id} className="flex justify-between text-[11px]">
                              <span className="flex items-center gap-1.5 text-slate-600">
                                <span className="w-2 h-2 rounded-full" style={{ background: p.color }} />
                                {p.name.split(" ")[0]}
                                {item.qty > 1 && unitsFor(item, p.id) > 0 && (
                                  <span className="text-slate-400">×{unitsFor(item, p.id)}</span>
                                )}
                              </span>
                              <span className="text-slate-500 font-medium">{clp(cost(p.id))}</span>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </details>

            {!finalized && accounts.length > 0 && (
              <select className="w-full border border-slate-200 rounded-xl px-3 py-2.5 text-sm bg-white" value={selectedAccountId ?? ""} onChange={(e) => setSelectedAccountId(e.target.value ? parseInt(e.target.value) : null)}>
                <option value="">Sin cuenta específica</option>
                {accounts.map((a) => <option key={a.id} value={a.id}>{a.name} ({a.bank})</option>)}
              </select>
            )}
            {!finalized && (
              <button onClick={handleFinalize} disabled={loading} className="w-full bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 text-white font-semibold py-3.5 rounded-xl flex items-center justify-center gap-2">
                {loading ? <div className="w-5 h-5 border-2 border-white/40 border-t-white rounded-full animate-spin" /> : <><Check size={18} /> Guardar en Lucas</>}
              </button>
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
      )}

      {/* ── STEP 2: Revisar + Asignar (split-screen with image | column without) ── */}
      {step === 2 && bill && (
        !bill.image_url ? (
          <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
            {renderStep2PeopleBar()}
            <div className="flex-1 min-h-0 overflow-y-auto max-w-lg mx-auto w-full px-4 py-4 pb-10">
              {renderStep2RightPanel()}
            </div>
          </div>
        ) : (
          <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
            {renderStep2PeopleBar()}
            <div ref={splitContainerRef} className="flex-1 min-h-0 flex flex-row overflow-hidden">
            {/* LEFT: image viewer + draw canvas */}
            <div
              ref={imgContainerRef}
              className="relative bg-slate-900 overflow-hidden select-none touch-none shrink-0 flex flex-col min-h-0"
              style={{ width: `${leftW * 100}%` }}
              onTouchStart={onImgTouchStart}
              onTouchMove={onImgTouchMove}
              onTouchEnd={onImgTouchEnd}
              onMouseDown={onImgMouseDown}
              onMouseMove={onImgMouseMove}
              onMouseUp={onImgMouseUp}
              onMouseLeave={onImgMouseUp}
            >
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={resolveBackendUrl(bill.image_url)}
                alt="Boleta"
                className="absolute inset-0 w-full h-full object-contain pointer-events-none"
                style={{ transform: `translate(${imgPan.x}px, ${imgPan.y}px) scale(${imgScale})`, transformOrigin: "center center" }}
                draggable={false}
              />
              <canvas
                ref={canvasRef}
                className="absolute inset-0 w-full h-full"
                style={{ touchAction: "none", pointerEvents: drawMode ? "auto" : "none" }}
                onPointerDown={onCanvasPointerDown}
                onPointerMove={onCanvasPointerMove}
                onPointerUp={onCanvasPointerUp}
                onPointerCancel={onCanvasPointerUp}
              />
              {/* Toolbar overlay */}
              <div className="absolute top-2 left-2 right-2 flex items-center gap-1.5 z-10">
                <button
                  onClick={() => setDrawMode(false)}
                  className={`p-2 rounded-lg shadow ${!drawMode ? "bg-indigo-600 text-white" : "bg-white/90 text-slate-700"}`}
                  title="Mover"
                >
                  <Hand size={14} />
                </button>
                <button
                  onClick={() => setDrawMode(true)}
                  className={`p-2 rounded-lg shadow ${drawMode ? "bg-indigo-600 text-white" : "bg-white/90 text-slate-700"}`}
                  title="Dibujar"
                >
                  <Pencil size={14} />
                </button>
                <button
                  onClick={clearCanvas}
                  className="p-2 rounded-lg shadow bg-white/90 text-slate-700"
                  title="Borrar trazos"
                >
                  <Eraser size={14} />
                </button>
                {imgScale > 1 && (
                  <button
                    onClick={resetImgTransform}
                    className="ml-auto px-2 py-1 rounded-lg shadow bg-white/90 text-[11px] text-slate-700 font-medium"
                  >
                    {Math.round(imgScale * 100)}% ✕
                  </button>
                )}
              </div>
            </div>
            {/* Divider */}
            <div
              onPointerDown={onVDividerDown}
              className="w-1 bg-slate-200 hover:bg-indigo-400 cursor-col-resize shrink-0 touch-none transition-colors"
              title="Arrastra para redimensionar"
            />
            {/* RIGHT: items + assign content */}
            <div className="flex-1 min-w-0 min-h-0 overflow-y-auto bg-slate-50 px-3 py-3 pb-6">
              {renderStep2RightPanel()}
            </div>
            </div>
          </div>
        )
      )}

      {/* Celebration on full assignment */}
      {celebrate && step === 2 && (
        <div className="fixed inset-0 z-40 pointer-events-none flex items-center justify-center">
          <div className="bg-emerald-500 text-white rounded-full p-6 shadow-2xl animate-ping-once">
            <Check size={42} strokeWidth={3} />
          </div>
        </div>
      )}

      {/* Toasts */}
      {error && <Toast msg={error} onClose={() => setError(null)} kind="error" />}
      {successMsg && <Toast msg={successMsg} onClose={() => setSuccessMsg(null)} kind="success" />}

      <style jsx>{`
        @keyframes ping-once {
          0% { transform: scale(0.4); opacity: 0; }
          40% { transform: scale(1.1); opacity: 1; }
          100% { transform: scale(1.4); opacity: 0; }
        }
        :global(.animate-ping-once) {
          animation: ping-once 1.2s cubic-bezier(0.16, 1, 0.3, 1) forwards;
        }
      `}</style>
    </div>
  );
}
