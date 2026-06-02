"use client";
/**
 * Split page — 3-step flow:
 *  1. Setup  — people + upload receipt OR manual amount entry
 *  2. Assign — tap person avatars per item; adjust split rules
 *  3. Settle — who paid? → show debts → optionally save to Lucas
 */
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Account,
  AssigneeIn,
  Person,
  ReceiptItemV2,
  SettleOut,
  SplitResultV2,
  addSplitItem,
  updateSplitItem,
  deleteSplitItem,
  assignItemV2,
  createPerson,
  createTransaction,
  deletePerson,
  getMe,
  getToken,
  listAccounts,
  listPeople,
  settleSplit,
  splitResultV2,
  startManualSplit,
  startSplit,
  uploadImage,
  resolveBackendUrl,
} from "@/lib/api";
import UploadZone from "@/components/UploadZone";
import BillSplitter from "@/components/BillSplitter";
import NumericInput from "@/components/NumericInput";
import { useT, formatMoney } from "@/lib/i18n";
import { X, ZoomIn, Pencil, Trash2, Plus, Check } from "lucide-react";

const PALETTE = [
  "#ef4444", "#f97316", "#eab308", "#10b981",
  "#06b6d4", "#6366f1", "#a855f7", "#ec4899",
];

type Transfer = {
  fromId: number; fromName: string; fromColor: string;
  toId: number;   toName: string;   toColor: string;
  amount: number;
};

function computeMultiPayer(
  people: Person[],
  resultPeople: { person_id: number; person_name: string; person_color: string; total: number }[],
  amounts: Record<number, number>,
): Transfer[] {
  const net = people.map((p) => ({
    id: p.id, name: p.name, color: p.color,
    balance: (amounts[p.id] ?? 0) - (resultPeople.find((r) => r.person_id === p.id)?.total ?? 0),
  }));
  const creds = net.filter((p) => p.balance > 0.5).map((p) => ({ ...p }));
  const debts = net.filter((p) => p.balance < -0.5).map((p) => ({ ...p }));
  creds.sort((a, b) => b.balance - a.balance);
  debts.sort((a, b) => a.balance - b.balance);
  const out: Transfer[] = [];
  let ci = 0, di = 0;
  while (ci < creds.length && di < debts.length) {
    const t = Math.min(creds[ci].balance, -debts[di].balance);
    if (t > 0.5) out.push({ fromId: debts[di].id, fromName: debts[di].name, fromColor: debts[di].color, toId: creds[ci].id, toName: creds[ci].name, toColor: creds[ci].color, amount: Math.round(t) });
    creds[ci].balance -= t; debts[di].balance += t;
    if (creds[ci].balance < 0.5) ci++;
    if (debts[di].balance > -0.5) di++;
  }
  return out;
}

type Step = "setup" | "review" | "assign" | "settle";

// ── Step indicator ──────────────────────────────────────────
function StepBar({ step }: { step: Step }) {
  const steps: Step[] = ["setup", "review", "assign", "settle"];
  const labels = ["Subir", "Revisar", "Asignar", "Liquidar"];
  const current = steps.indexOf(step);
  return (
    <div className="flex items-center gap-0 mb-6">
      {steps.map((s, i) => (
        <div key={s} className="flex items-center flex-1">
          <div className="flex flex-col items-center flex-1">
            <div
              className={`w-8 h-8 rounded-full flex items-center justify-center text-sm font-bold border-2 transition-all ${
                i < current
                  ? "bg-emerald-500 border-emerald-500 text-white"
                  : i === current
                    ? "bg-indigo-600 border-indigo-600 text-white"
                    : "bg-white border-slate-300 text-slate-400"
              }`}
            >
              {i < current ? "✓" : i + 1}
            </div>
            <span
              className={`text-[11px] mt-1 font-medium ${
                i === current ? "text-indigo-600" : "text-slate-400"
              }`}
            >
              {labels[i]}
            </span>
          </div>
          {i < steps.length - 1 && (
            <div
              className={`h-0.5 flex-1 mb-4 ${
                i < current ? "bg-emerald-400" : "bg-slate-200"
              }`}
            />
          )}
        </div>
      ))}
    </div>
  );
}

// ── Review Step ─────────────────────────────────────────────
function ReviewStep({
  items,
  imageUrl,
  imageFullscreen,
  onOpenFullscreen,
  onCloseFullscreen,
  onUpdateItem,
  onDeleteItem,
  onAddItem,
  onConfirm,
}: {
  items: ReceiptItemV2[];
  imageUrl: string;
  imageFullscreen: boolean;
  onOpenFullscreen: () => void;
  onCloseFullscreen: () => void;
  onUpdateItem: (id: number, patch: { name?: string; price?: number }) => void;
  onDeleteItem: (id: number) => void;
  onAddItem: (name: string, price: number) => void;
  onConfirm: () => void;
}) {
  const [addName, setAddName] = useState("");
  const [addPrice, setAddPrice] = useState(0);
  const [showAdd, setShowAdd] = useState(false);

  return (
    <div className="pb-24">
      {/* ── Sticky receipt image ─────────────────────────────── */}
      {imageUrl && (
        <div className="sticky top-0 z-10 -mx-4 px-4 bg-white/95 backdrop-blur-sm pt-1 pb-2">
          <div
            className="relative rounded-2xl overflow-hidden bg-slate-100 cursor-pointer"
            onClick={onOpenFullscreen}
          >
            <img
              src={imageUrl}
              alt="Boleta"
              className="w-full object-contain"
              style={{ maxHeight: "38vh" }}
            />
            {/* "Ver completa" overlay button */}
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); onOpenFullscreen(); }}
              className="absolute top-2 right-2 flex items-center gap-1 bg-black/50 backdrop-blur-sm text-white text-[11px] font-medium px-2 py-1 rounded-full"
            >
              <ZoomIn className="w-3 h-3" />
              Ver completa
            </button>
            {/* Bottom label */}
            <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/40 to-transparent px-3 py-2">
              <p className="text-white text-[11px] font-medium">
                Compara cada ítem con tu boleta · toca para ampliar
              </p>
            </div>
          </div>
        </div>
      )}

      {/* ── Items list ──────────────────────────────────────── */}
      <div className="card mt-3">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-base font-semibold">Ítems reconocidos</h2>
          <span className="text-xs text-slate-400">{items.length} ítems · toca ✏️ para editar</span>
        </div>
        <ul className="divide-y divide-slate-100">
          {items.map((item) => (
            <ReviewItemRow
              key={item.id}
              item={item}
              onUpdate={(patch) => onUpdateItem(item.id, patch)}
              onDelete={() => onDeleteItem(item.id)}
            />
          ))}
        </ul>

        {/* Add item */}
        {showAdd ? (
          <div className="flex items-center gap-2 mt-3 pt-3 border-t border-slate-100">
            <input
              className="input flex-1 text-sm py-1.5"
              placeholder="Nombre del ítem"
              value={addName}
              onChange={(e) => setAddName(e.target.value)}
              autoFocus
            />
            <NumericInput
              className="input w-24 text-sm py-1.5 font-mono"
              placeholder="Precio"
              value={addPrice}
              onChange={setAddPrice}
              allowDecimals
            />
            <button
              type="button"
              className="text-emerald-600 hover:text-emerald-700"
              onClick={() => {
                if (addName.trim() && addPrice > 0) {
                  onAddItem(addName.trim(), addPrice);
                  setAddName(""); setAddPrice(0); setShowAdd(false);
                }
              }}
            >
              <Check className="w-5 h-5" />
            </button>
            <button type="button" className="text-slate-400"
              onClick={() => { setAddName(""); setAddPrice(0); setShowAdd(false); }}
            >
              <X className="w-5 h-5" />
            </button>
          </div>
        ) : (
          <button
            type="button"
            onClick={() => setShowAdd(true)}
            className="mt-3 w-full flex items-center justify-center gap-1.5 text-sm text-slate-500 hover:text-indigo-600 py-2 border border-dashed border-slate-300 rounded-xl hover:border-indigo-300 transition"
          >
            <Plus className="w-4 h-4" />
            Agregar ítem faltante
          </button>
        )}
      </div>

      {/* ── Sticky confirm button at bottom ─────────────────── */}
      <div className="fixed bottom-0 left-0 right-0 z-10 p-4 bg-white/90 backdrop-blur-sm border-t border-slate-100">
        <div className="max-w-2xl mx-auto">
          <button
            type="button"
            className="btn-primary w-full py-3 text-base font-semibold shadow-lg"
            onClick={onConfirm}
          >
            Todo correcto — Asignar →
          </button>
        </div>
      </div>

      {/* ── Fullscreen image modal ───────────────────────────── */}
      {imageFullscreen && (
        <div
          className="fixed inset-0 z-50 bg-black/95 overflow-auto"
          onClick={onCloseFullscreen}
        >
          <button
            type="button"
            className="fixed top-4 right-4 z-10 bg-black/60 rounded-full p-2.5 text-white"
            onClick={onCloseFullscreen}
          >
            <X className="w-6 h-6" />
          </button>
          <img
            src={imageUrl}
            alt="Boleta completa"
            className="w-full"
            style={{ touchAction: "pinch-zoom" }}
            onClick={(e) => e.stopPropagation()}
          />
        </div>
      )}
    </div>
  );
}

// ── Inline editable row for review step ─────────────────────
function ReviewItemRow({
  item,
  onUpdate,
  onDelete,
}: {
  item: ReceiptItemV2;
  onUpdate: (patch: { name?: string; price?: number }) => void;
  onDelete: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [editName, setEditName] = useState(item.name);
  const [editPrice, setEditPrice] = useState(item.line_total / Math.max(item.quantity, 1));

  useEffect(() => {
    if (!editing) {
      setEditName(item.name);
      setEditPrice(item.line_total / Math.max(item.quantity, 1));
    }
  }, [item.name, item.line_total, item.quantity, editing]);

  if (editing) {
    return (
      <li className="flex items-center gap-2 py-2.5">
        <input
          className="input flex-1 text-sm py-1 px-2"
          value={editName}
          onChange={(e) => setEditName(e.target.value)}
          autoFocus
        />
        <NumericInput
          className="input w-24 font-mono text-sm py-1 px-2"
          value={editPrice}
          onChange={setEditPrice}
          allowDecimals
          placeholder="0"
        />
        <button
          type="button"
          className="text-emerald-600 hover:text-emerald-700"
          onClick={() => { onUpdate({ name: editName, price: editPrice }); setEditing(false); }}
        >
          <Check className="w-4 h-4" />
        </button>
        <button
          type="button"
          className="text-slate-400"
          onClick={() => setEditing(false)}
        >
          <X className="w-4 h-4" />
        </button>
      </li>
    );
  }

  return (
    <li className="flex items-center gap-2 py-2.5">
      <span className="flex-1 text-sm text-slate-800 truncate">{item.name}</span>
      {item.quantity > 1 && (
        <span className="text-xs text-slate-400 shrink-0">×{item.quantity}</span>
      )}
      <span className="font-mono text-sm text-slate-700 shrink-0">
        ${item.line_total.toLocaleString("es-CL")}
      </span>
      <button
        type="button"
        className="text-slate-400 hover:text-indigo-500 transition shrink-0"
        onClick={() => setEditing(true)}
      >
        <Pencil className="w-4 h-4" />
      </button>
      <button
        type="button"
        className="text-slate-400 hover:text-rose-500 transition shrink-0"
        onClick={onDelete}
      >
        <Trash2 className="w-4 h-4" />
      </button>
    </li>
  );
}

export default function SplitPage() {
  const router = useRouter();
  const { t, locale } = useT();

  // ── State ──────────────────────────────────────────────────
  const [step, setStep] = useState<Step>("setup");
  const [people, setPeople] = useState<Person[]>([]);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [result, setResult] = useState<SplitResultV2 | null>(null);
  const [txId, setTxId] = useState<number | null>(null);
  const [currency, setCurrency] = useState("CLP");

  // Setup form — manual entry
  const [manualAmount, setManualAmount] = useState(0);
  const [manualMerchant, setManualMerchant] = useState("");

  // Upload state
  const [uploading, setUploading] = useState(false);
  const [uploadErr, setUploadErr] = useState("");
  // Propina state
  const [propinaAmount, setPropinaAmount] = useState(0);
  const [propinaPct, setPropinaPct] = useState(10);
  const [propinaMode, setPropinaMode] = useState<"pct" | "clp">("pct");
  const [addingPropina, setAddingPropina] = useState(false);
  const [addingIva, setAddingIva] = useState(false);
  // Descuento state
  const [discountAmount, setDiscountAmount] = useState(0);
  const [discountPct, setDiscountPct] = useState(10);
  const [discountMode, setDiscountMode] = useState<"pct" | "clp">("pct");
  const [addingDiscount, setAddingDiscount] = useState(false);
  // IVA info
  const [ivaIncluded, setIvaIncluded] = useState(false);
  // Duplicate detection
  const [dupeDetected, setDupeDetected] = useState(false);
  // Receipt image for review step
  const [receiptImageUrl, setReceiptImageUrl] = useState("");
  const [imageFullscreen, setImageFullscreen] = useState(false);

  // Settlement — single payer
  const [settlement, setSettlement] = useState<SettleOut | null>(null);
  const [payerPersonId, setPayerPersonId] = useState<number | null>(null);
  const [payerAccountId, setPayerAccountId] = useState<number | null>(null);
  const [settling, setSettling] = useState(false);
  // Settlement — multi payer
  const [multiPayer, setMultiPayer] = useState(false);
  const [payerAmounts, setPayerAmounts] = useState<Record<number, number>>({});
  const [multiSettlement, setMultiSettlement] = useState<Transfer[] | null>(null);
  const [copied, setCopied] = useState(false);

  // ── Boot ───────────────────────────────────────────────────
  useEffect(() => {
    if (!getToken()) { router.replace("/"); return; }
    Promise.all([getMe(), listPeople(), listAccounts()]).then(
      ([me, all, accs]) => {
        // "Yo" first, then the rest (deduped)
        const others = all.filter((p) => !p.is_me);
        setPeople([me, ...others]);
        setAccounts(accs);
        setPayerPersonId(null); // default payer = me
      },
    );
  }, [router]);

  // ── Helpers ────────────────────────────────────────────────
  const refreshResult = useCallback(async (id: number) => {
    const r = await splitResultV2(id);
    setResult(r);
    return r;
  }, []);

  // ── Upload receipt ─────────────────────────────────────────
  async function handleFile(file: File) {
    setUploadErr("");
    setUploading(true);
    try {
      const upload = await uploadImage(file);
      if (!upload.transactions.length) throw new Error("No se pudo leer la boleta");

      // Duplicate detection via dupe_of flag from the OCR parser
      const hasDupe = upload.transactions.some((t) => t.dupe_of != null);
      if (hasDupe) setDupeDetected(true);

      const txCurrency = upload.transactions[0].currency || upload.currency || "CLP";

      // Normalize items: OCR may return (a) 1 tx with many items, or
      // (b) N txs each as a separate line item. Detect and unify.
      let totalAmount: number;
      let merchant: string;
      let txDate: string;
      let splitItems: { name: string; price: number; quantity: number }[];

      if (upload.transactions.length === 1) {
        const t0 = upload.transactions[0];
        totalAmount = t0.amount;
        merchant = t0.merchant || "";
        txDate = t0.date;
        splitItems = t0.items.length > 0
          ? t0.items
          : [{ name: t0.merchant || "Total", price: t0.amount, quantity: 1 }];
      } else {
        // Multiple transactions — each is a line item of the same receipt
        const allTxs = upload.transactions.filter((t) => !t.is_income && !t.is_cc_payment);
        totalAmount = allTxs.reduce((s, t) => s + t.amount, 0);
        merchant = allTxs[0]?.merchant || upload.transactions[0].merchant || "";
        txDate = allTxs[0]?.date || upload.transactions[0].date;

        // If some txs already have items, flatten them; otherwise each tx = 1 item
        const flatItems = allTxs.flatMap((t) => t.items);
        splitItems = flatItems.length > 0
          ? flatItems
          : allTxs.map((t) => ({ name: t.merchant || "Ítem", price: t.amount, quantity: 1 }));
      }

      // ── IVA detection ──────────────────────────────────────────────────────
      // Chilean boletas have two formats:
      //
      //  Case A — OCR returns an explicit "IVA" row (boleta shows Total Neto +
      //           IVA + Total con IVA). Remove the IVA row and distribute its
      //           amount proportionally into each item's unit price.
      //
      //  Case B — No "IVA" row but itemsSum < totalAmount by ~19%. Same math,
      //           but derived from the gap instead of an explicit row.
      //
      //  Case C — No gap (items already include IVA, or it's a service ticket
      //           with no IVA). Do nothing.
      //
      // Either way: each person pays IVA proportional to THEIR items.

      /** Distribute `ivaTotal` pesos across `items` in proportion to their subtotals. */
      function distributeIva(
        items: { name: string; price: number; quantity: number }[],
        ivaTotal: number,
      ) {
        const baseSum = items.reduce((s, it) => s + it.price * it.quantity, 0);
        if (baseSum === 0) return items;
        let remaining = Math.round(ivaTotal);
        return items.map((it, idx) => {
          const itemBase = it.price * it.quantity;
          const share =
            idx === items.length - 1
              ? remaining                                          // last absorbs rounding
              : Math.round((itemBase / baseSum) * ivaTotal);
          remaining -= share;
          return { ...it, price: Math.round((itemBase + share) / it.quantity) };
        });
      }

      const ivaIdx = splitItems.findIndex((it) =>
        /^(iva|impuesto|tax)\b/i.test(it.name.trim()),
      );

      if (ivaIdx >= 0) {
        // Case A: OCR returned an explicit IVA item — remove it, distribute its amount
        const ivaRow = splitItems[ivaIdx];
        const ivaTotal = ivaRow.price * ivaRow.quantity;
        const nonIva = splitItems.filter((_, i) => i !== ivaIdx);
        splitItems = distributeIva(nonIva, ivaTotal);
        setIvaIncluded(true);
      } else {
        // Case B or C: no explicit IVA row
        const itemsSum = splitItems.reduce((s, it) => s + it.price * it.quantity, 0);
        const gap = Math.round(totalAmount - itemsSum);
        const expectedIva = Math.round(itemsSum * 0.19);

        if (gap > 0 && Math.abs(gap - expectedIva) <= Math.max(1, expectedIva * 0.05)) {
          // Case B: gap ≈ 19% → the boleta shows neto prices, IVA not listed explicitly.
          // Calculate IVA once on the full total (SII rule), then distribute across items.
          const ivaAmount = Math.round(itemsSum * 0.19);
          splitItems = distributeIva(splitItems, ivaAmount);
          setIvaIncluded(true);
        } else if (gap > 0 && gap > itemsSum * 0.01) {
          // Unknown positive gap > 1% → "Otros cargos"
          splitItems = [...splitItems, { name: "Otros cargos", price: gap, quantity: 1 }];
        }
        // Case C: gap ≈ 0 → IVA already included in prices, do nothing
      }

      // Drop free modifier items (price = 0) — they don't affect the split
      splitItems = splitItems.filter((it) => it.price !== 0);

      // Auto-detect discount lines from OCR: ensure their price is negative
      splitItems = splitItems.map((it) => {
        if (/^(descuento|dscto|dcto|rebaja)/i.test(it.name.trim()) && it.price > 0) {
          return { ...it, price: -it.price };
        }
        return it;
      });

      // Expand items with quantity > 1 into individual units (capped at 20)
      splitItems = splitItems.flatMap((it) => {
        if (it.quantity > 1 && it.price > 0) {
          return Array.from({ length: Math.min(it.quantity, 20) }, () => ({
            name: it.name,
            price: it.price,
            quantity: 1,
          }));
        }
        return [it];
      });

      // Validate we have something real to split
      if (totalAmount <= 0) {
        throw new Error(
          "No se pudo leer el monto de la boleta. " +
          "Intenta con otra foto o ingresa el monto manualmente."
        );
      }
      const zeroItems = splitItems.every((it) => it.price <= 0);
      if (zeroItems) {
        throw new Error(
          "Los precios de los ítems son 0. " +
          "Ingresa el monto total manualmente."
        );
      }

      // Create the transaction — handle 409 (same receipt re-uploaded) gracefully
      let txIdToUse: number;
      try {
        const tx = await createTransaction({
          amount: totalAmount,
          currency: txCurrency,
          category: "Dividido",
          date: txDate,
          merchant,
          notes: "",
          is_income: false,
          account_id: null,
          image_url: upload.image_url,
          items: splitItems,
        });
        txIdToUse = tx.id;
      } catch (e: any) {
        // 409 = duplicate receipt within 60s — reuse the existing transaction
        const dupeMatch = e.message?.match(/"existing_id"\s*:\s*(\d+)/);
        if (dupeMatch) {
          txIdToUse = parseInt(dupeMatch[1]);
          setDupeDetected(true);
        } else {
          throw e;
        }
      }

      setCurrency(txCurrency);
      setTxId(txIdToUse);
      setReceiptImageUrl(resolveBackendUrl(upload.image_url || ""));
      await startSplit(txIdToUse, splitItems);
      await refreshResult(txIdToUse);
      setStep("review");
    } catch (e: any) {
      setUploadErr(e.message || "Error al subir");
    } finally {
      setUploading(false);
    }
  }

  // ── Manual start ───────────────────────────────────────────
  async function handleManualStart() {
    const amount = manualAmount;
    if (!amount || isNaN(amount)) return;
    setUploading(true);
    try {
      const today = new Date().toISOString().slice(0, 10);
      const res = await startManualSplit({
        merchant: manualMerchant || undefined,
        total_amount: amount,
        currency,
        date: today,
      });
      setTxId(res.transaction_id);
      await refreshResult(res.transaction_id);
      setStep("assign");
    } catch (e: any) {
      setUploadErr(e.message || "Error");
    } finally {
      setUploading(false);
    }
  }

  // ── Toggle a person on/off for an item ─────────────────────
  async function handleTogglePerson(itemId: number, personId: number) {
    if (!result) return;
    const item = result.items.find((i) => i.id === itemId);
    if (!item) return;

    const currentIds = new Set(item.assignees.map((a) => a.person_id));
    let newIds: number[];
    if (currentIds.has(personId)) {
      newIds = [...currentIds].filter((id) => id !== personId);
    } else {
      newIds = [...currentIds, personId];
    }

    const assignees: AssigneeIn[] = newIds.map((pid) => {
      const existing = item.assignees.find((a) => a.person_id === pid);
      return {
        person_id: pid,
        split_type: existing?.split_type ?? "equal",
        value: existing?.value ?? null,
      };
    });

    try {
      await assignItemV2(itemId, assignees);
      if (txId) await refreshResult(txId);
    } catch (e: any) {
      alert(e?.message || "Error al asignar");
    }
  }

  // ── Assign all people to one item in a single call ────────
  async function handleAssignAll(itemId: number, personIds: number[]) {
    const assignees: AssigneeIn[] = personIds.map((pid) => ({
      person_id: pid,
      split_type: "equal",
      value: null,
    }));
    try {
      await assignItemV2(itemId, assignees);
      if (txId) await refreshResult(txId);
    } catch (e: any) {
      alert(e?.message || "Error al asignar");
    }
  }

  // ── Save adjusted split for one item ──────────────────────
  async function handleSaveAdjust(itemId: number, assignees: AssigneeIn[]) {
    try {
      await assignItemV2(itemId, assignees);
      if (txId) await refreshResult(txId);
    } catch (e: any) {
      alert(e?.message || "Error al guardar ajuste");
    }
  }

  // ── Settle ─────────────────────────────────────────────────
  async function handleSettle(saveToLucas: boolean = true) {
    if (!txId) return;
    setSettling(true);
    try {
      const out = await settleSplit({
        transaction_id: txId,
        payer_person_id: payerPersonId,
        account_id: payerAccountId ?? undefined,
        save_to_lucas: saveToLucas,
      });
      setSettlement(out);
    } catch (e: any) {
      alert(e.message);
    } finally {
      setSettling(false);
    }
  }

  // ── People management ──────────────────────────────────────
  async function handleAddPerson(name: string, color: string) {
    try {
      const p = await createPerson(name, color);
      setPeople((prev) => [...prev, p]);
    } catch (e: any) {
      alert(e?.message || "Error al agregar persona");
    }
  }
  async function handleRemovePerson(id: number) {
    try {
      await deletePerson(id);
      setPeople((prev) => prev.filter((x) => x.id !== id));
    } catch (e: any) {
      alert(e?.message || "Error al eliminar persona");
    }
  }
  async function handleClearPeople() {
    try {
      const toRemove = people.filter((p) => !p.is_me);
      await Promise.all(toRemove.map((p) => deletePerson(p.id)));
      setPeople((prev) => prev.filter((p) => p.is_me));
    } catch (e: any) {
      alert(e?.message || "Error al limpiar lista");
    }
  }

  // ── Add propina ────────────────────────────────────────────
  async function handleAddPropina() {
    if (!txId || !result) return;
    const base = result.items
      .filter((it) => !/propina|tip|iva/i.test(it.name))
      .reduce((s, it) => s + it.price * it.quantity, 0);
    const amount = propinaMode === "pct" ? Math.round(base * propinaPct / 100) : propinaAmount;
    if (amount <= 0) return;
    setAddingPropina(true);
    try {
      await addSplitItem(txId, { name: "Propina", price: amount, quantity: 1 });
      setPropinaAmount(0);
      await refreshResult(txId);
    } catch (e: any) {
      alert(e.message || "Error al agregar propina");
    } finally {
      setAddingPropina(false);
    }
  }

  // ── Add discount ───────────────────────────────────────────
  async function handleAddDiscount() {
    if (!txId || !result) return;
    const base = result.items
      .filter((it) => !/propina|tip|iva|descuento|dscto|dcto|rebaja/i.test(it.name))
      .reduce((s, it) => s + it.price * it.quantity, 0);
    const rawAmt = discountMode === "pct"
      ? Math.round(base * discountPct / 100)
      : Math.abs(discountAmount);
    if (rawAmt <= 0) return;
    const negativePrice = -rawAmt;
    setAddingDiscount(true);
    try {
      const discountItem = await addSplitItem(txId, { name: "Descuento", price: negativePrice, quantity: 1 });
      // Assign discount to all people equally
      await handleAssignAll(discountItem.id, people.map((p) => p.id));
      setDiscountAmount(0);
      await refreshResult(txId);
    } catch (e: any) {
      alert(e.message || "Error al agregar descuento");
    } finally {
      setAddingDiscount(false);
    }
  }

  // ── Update / delete / add item ────────────────────────────
  async function handleUpdateItem(itemId: number, patch: { name?: string; price?: number }) {
    try {
      await updateSplitItem(itemId, patch);
      // Optimistic local update (preserves order) + server sync for accurate totals/amounts
      setResult((prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          items: prev.items.map((it) => {
            if (it.id !== itemId) return it;
            const newPrice = patch.price ?? it.price;
            const newName = patch.name ?? it.name;
            return { ...it, name: newName, price: newPrice, line_total: newPrice * Math.max(it.quantity, 1) };
          }),
        };
      });
      if (txId) await refreshResult(txId);
    } catch (e: any) {
      alert(e?.message || "Error al actualizar ítem");
    }
  }

  async function handleDeleteItem(itemId: number) {
    try {
      await deleteSplitItem(itemId);
      if (txId) {
        await refreshResult(txId);
      } else {
        setResult((prev) => {
          if (!prev) return prev;
          return { ...prev, items: prev.items.filter((it) => it.id !== itemId) };
        });
      }
    } catch (e: any) {
      alert(e?.message || "Error al eliminar ítem");
    }
  }

  async function handleAddItem(name: string, price: number) {
    if (!txId) return;
    try {
      await addSplitItem(txId, { name, price, quantity: 1 });
      await refreshResult(txId);
    } catch (e: any) {
      alert(e?.message || "Error al agregar ítem");
    }
  }

  // ── Correct total ─────────────────────────────────────────
  async function handleUpdateTotal(newTotal: number) {
    if (!txId || !result) return;
    const currentSum = result.items.reduce((s, it) => s + it.price * it.quantity, 0);
    const gap = Math.round(newTotal - currentSum);
    // Find existing adjustment item to update or delete
    const adjItem = result.items.find((it) => /^(otros cargos|ajuste)/i.test(it.name));
    try {
      if (gap === 0) {
        if (adjItem) await deleteSplitItem(adjItem.id);
      } else if (adjItem) {
        await updateSplitItem(adjItem.id, { price: adjItem.price + gap });
      } else {
        await addSplitItem(txId, { name: gap > 0 ? "Otros cargos" : "Descuento", price: gap, quantity: 1 });
      }
      await refreshResult(txId);
    } catch (e: any) {
      alert(e?.message || "Error al actualizar total");
    }
  }

  // ── Add IVA manually ──────────────────────────────────────
  async function handleAddIva() {
    if (!txId || !result) return;
    setAddingIva(true);
    try {
      const baseTotal = result.items.reduce((s, it) => s + it.price * it.quantity, 0);
      const ivaAmt = Math.round(baseTotal * 0.19);
      await addSplitItem(txId, { name: "IVA (19%)", price: ivaAmt, quantity: 1 });
      setIvaIncluded(true);
      await refreshResult(txId);
    } catch (e: any) {
      alert(e.message || "Error al agregar IVA");
    } finally {
      setAddingIva(false);
    }
  }

  // ── Reset ──────────────────────────────────────────────────
  function reset() {
    setStep("setup");
    setResult(null);
    setTxId(null);
    setSettlement(null);
    setMultiSettlement(null);
    setMultiPayer(false);
    setPayerAmounts({});
    setPayerPersonId(null);
    setPayerAccountId(null);
    setManualAmount(0);
    setManualMerchant("");
    setUploadErr("");
    setPropinaAmount(0);
    setPropinaPct(10);
    setPropinaMode("pct");
    setDiscountAmount(0);
    setDiscountPct(10);
    setDiscountMode("pct");
    setIvaIncluded(false);
    setDupeDetected(false);
    setReceiptImageUrl("");
    setImageFullscreen(false);
  }

  // ── Render ─────────────────────────────────────────────────
  return (
    <div className="max-w-2xl mx-auto space-y-2 pb-24 md:pb-0">
      <h1 className="text-3xl font-semibold tracking-tight mb-1">{t("split.title")}</h1>
      <StepBar step={step} />

      {/* ══ STEP 1: Setup ══════════════════════════════════════ */}
      {step === "setup" && (
        <div className="space-y-5">
          {/* Upload receipt */}
          <div className="card">
            <h2 className="text-base font-semibold mb-3">{t("split.uploadReceipt")}</h2>
            <UploadZone onFile={handleFile} loading={uploading} />
            {uploadErr && <p className="text-sm text-rose-500 mt-2">{uploadErr}</p>}
          </div>

          {/* Divider */}
          <div className="flex items-center gap-3 text-slate-400 text-sm">
            <div className="flex-1 h-px bg-slate-200" />
            {t("split.orManual")}
            <div className="flex-1 h-px bg-slate-200" />
          </div>

          {/* Manual entry */}
          <div className="card space-y-3">
            <input
              className="input"
              placeholder={t("split.manualMerchant")}
              value={manualMerchant}
              onChange={(e) => setManualMerchant(e.target.value)}
            />
            <div className="flex gap-2">
              <NumericInput
                className="input flex-1"
                placeholder={t("split.manualAmount")}
                value={manualAmount}
                onChange={setManualAmount}
                allowDecimals
              />
              <select
                className="input w-24"
                value={currency}
                onChange={(e) => setCurrency(e.target.value)}
              >
                {["CLP", "USD", "EUR", "ARS", "MXN"].map((c) => (
                  <option key={c}>{c}</option>
                ))}
              </select>
            </div>
            <button
              className="btn-primary w-full"
              disabled={!manualAmount || manualAmount === 0 || uploading}
              onClick={handleManualStart}
            >
              {t("split.manualStart")}
            </button>
          </div>
        </div>
      )}

      {/* ══ STEP 2: Review ═════════════════════════════════════ */}
      {step === "review" && result && (
        <ReviewStep
          items={result.items}
          imageUrl={receiptImageUrl}
          imageFullscreen={imageFullscreen}
          onOpenFullscreen={() => setImageFullscreen(true)}
          onCloseFullscreen={() => setImageFullscreen(false)}
          onUpdateItem={handleUpdateItem}
          onDeleteItem={handleDeleteItem}
          onAddItem={handleAddItem}
          onConfirm={() => setStep("assign")}
        />
      )}

      {/* ══ STEP 3: Assign ══════════════════════════════════════ */}
      {step === "assign" && result && (
        <div className="space-y-4">
          {/* Duplicate warning banner */}
          {dupeDetected && (
            <div className="flex items-start gap-2 rounded-xl bg-amber-50 border border-amber-200 px-4 py-3 text-sm text-amber-800">
              <span className="shrink-0 text-base">⚠️</span>
              <span>
                <strong>Boleta duplicada detectada.</strong> Esta boleta ya fue subida anteriormente. Puedes continuar de todas formas, pero verifica que no estés dividiendo la misma cuenta dos veces.
              </span>
            </div>
          )}

          {/* IVA info banner */}
          {ivaIncluded && (
            <div className="flex items-start gap-2 rounded-xl bg-amber-50 border border-amber-200 px-4 py-3 text-sm text-amber-800">
              <span className="shrink-0 text-base">🧾</span>
              <span>
                <strong>IVA 19% incluido por ítem.</strong> Cada precio ya incluye el IVA correspondiente — quien pide más, paga más IVA automáticamente.
              </span>
            </div>
          )}
          <BillSplitter
            result={result}
            people={people}
            currency={currency}
            onTogglePerson={handleTogglePerson}
            onSaveAdjust={handleSaveAdjust}
            onAddPerson={handleAddPerson}
            onRemovePerson={handleRemovePerson}
            onClearPeople={handleClearPeople}
            onUpdateItem={handleUpdateItem}
            onDeleteItem={handleDeleteItem}
            onAddItem={handleAddItem}
            onAssignAll={handleAssignAll}
            onUpdateTotal={handleUpdateTotal}
          />

          {/* ── Propina ─────────────────────────────────────────── */}
          {!result.items.some((it) => /propina|tip/i.test(it.name)) && (() => {
            const base = result.items
              .filter((it) => !/propina|tip|iva/i.test(it.name))
              .reduce((s, it) => s + it.price * it.quantity, 0);
            const clp = propinaMode === "pct"
              ? Math.round(base * propinaPct / 100)
              : propinaAmount;
            return (
              <div className="card space-y-3">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium text-slate-700">🙏 Propina</span>
                  <div className="flex text-xs rounded-lg bg-slate-100 p-0.5">
                    {(["pct", "clp"] as const).map((m) => (
                      <button
                        key={m}
                        type="button"
                        className={`px-3 py-1 rounded-md transition ${
                          propinaMode === m ? "bg-white shadow-soft font-medium" : "text-slate-500"
                        }`}
                        onClick={() => setPropinaMode(m)}
                      >
                        {m === "pct" ? "%" : currency}
                      </button>
                    ))}
                  </div>
                </div>
                {propinaMode === "pct" ? (
                  <div className="flex items-center gap-3">
                    <div className="flex items-center gap-1 flex-1">
                      <NumericInput
                        className="input w-20 font-mono text-sm text-center"
                        value={propinaPct}
                        onChange={setPropinaPct}
                        allowDecimals
                        placeholder="10"
                      />
                      <span className="text-sm text-slate-500">%</span>
                    </div>
                    {base > 0 && (
                      <span className="text-sm text-slate-500">
                        = {formatMoney(clp, currency)}
                      </span>
                    )}
                  </div>
                ) : (
                  <div className="flex items-center gap-2">
                    <NumericInput
                      className="input flex-1 font-mono text-sm"
                      value={propinaAmount}
                      onChange={setPropinaAmount}
                      placeholder="0"
                    />
                    <span className="text-xs text-slate-400 shrink-0">{currency}</span>
                  </div>
                )}
                <button
                  type="button"
                  className="btn-primary w-full text-sm"
                  disabled={clp <= 0 || addingPropina}
                  onClick={handleAddPropina}
                >
                  {addingPropina ? "…" : `+ Agregar propina${clp > 0 ? ` (${formatMoney(clp, currency)})` : ""}`}
                </button>
              </div>
            );
          })()}

          {/* ── Descuento ─────────────────────────────────────────── */}
          {!result.items.some((it) => /descuento|dscto|dcto|rebaja/i.test(it.name)) && (() => {
            const base = result.items
              .filter((it) => !/propina|tip|iva|descuento|dscto|dcto|rebaja/i.test(it.name))
              .reduce((s, it) => s + it.price * it.quantity, 0);
            const clp = discountMode === "pct"
              ? Math.round(base * discountPct / 100)
              : Math.abs(discountAmount);
            return (
              <div className="card space-y-3">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium text-slate-700">% Descuento</span>
                  <div className="flex text-xs rounded-lg bg-slate-100 p-0.5">
                    {(["pct", "clp"] as const).map((m) => (
                      <button
                        key={m}
                        type="button"
                        className={`px-3 py-1 rounded-md transition ${
                          discountMode === m ? "bg-white shadow-soft font-medium" : "text-slate-500"
                        }`}
                        onClick={() => setDiscountMode(m)}
                      >
                        {m === "pct" ? "%" : currency}
                      </button>
                    ))}
                  </div>
                </div>
                {discountMode === "pct" ? (
                  <div className="flex items-center gap-3">
                    <div className="flex items-center gap-1 flex-1">
                      <NumericInput
                        className="input w-20 font-mono text-sm text-center"
                        value={discountPct}
                        onChange={setDiscountPct}
                        allowDecimals
                        placeholder="10"
                      />
                      <span className="text-sm text-slate-500">%</span>
                    </div>
                    {base > 0 && (
                      <span className="text-sm text-slate-500 text-emerald-600">
                        = -{formatMoney(clp, currency)}
                      </span>
                    )}
                  </div>
                ) : (
                  <div className="flex items-center gap-2">
                    <NumericInput
                      className="input flex-1 font-mono text-sm"
                      value={discountAmount}
                      onChange={setDiscountAmount}
                      placeholder="0"
                    />
                    <span className="text-xs text-slate-400 shrink-0">{currency}</span>
                  </div>
                )}
                <button
                  type="button"
                  className="btn-ghost w-full text-sm border border-emerald-200 text-emerald-700 hover:bg-emerald-50"
                  disabled={clp <= 0 || addingDiscount}
                  onClick={handleAddDiscount}
                >
                  {addingDiscount ? "…" : `− Agregar descuento${clp > 0 ? ` (-${formatMoney(clp, currency)})` : ""}`}
                </button>
              </div>
            );
          })()}

          {/* ── IVA manual ─────────────────────────────────────── */}
          {!ivaIncluded && (
            <div className="card">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p className="text-sm font-medium text-slate-700">Agregar IVA (19%)</p>
                  <p className="text-xs text-slate-500 mt-0.5">
                    Para boletas donde el IVA no estaba incluido en los precios
                  </p>
                </div>
                <button
                  type="button"
                  className="btn-ghost text-sm px-4 py-2 shrink-0"
                  disabled={addingIva}
                  onClick={handleAddIva}
                >
                  {addingIva ? "…" : "+ IVA"}
                </button>
              </div>
            </div>
          )}

          <div className="flex gap-3 pt-2">
            <button className="btn-ghost flex-1" onClick={reset}>
              ← Volver
            </button>
            <button
              className="btn-primary flex-1"
              onClick={() => setStep("settle")}
            >
              {t("split.stepSettle")} →
            </button>
          </div>
        </div>
      )}

      {/* ══ STEP 3: Settle ═════════════════════════════════════ */}
      {step === "settle" && result && !settlement && !multiSettlement && (
        <div className="space-y-5">
          {/* Who paid? */}
          <div className="card space-y-3">
            <h2 className="text-base font-semibold">{t("split.whoPayd")}</h2>

            {/* Mode toggle */}
            <div className="flex text-sm rounded-xl bg-slate-100 p-1">
              <button type="button" className={`flex-1 py-2 rounded-lg transition ${!multiPayer ? "bg-white shadow-soft font-medium" : "text-slate-500"}`} onClick={() => setMultiPayer(false)}>
                Una persona
              </button>
              <button type="button" className={`flex-1 py-2 rounded-lg transition ${multiPayer ? "bg-white shadow-soft font-medium" : "text-slate-500"}`} onClick={() => setMultiPayer(true)}>
                Entre varios
              </button>
            </div>

            {!multiPayer ? (
              <>
                <div className="flex flex-wrap gap-2">
                  <button type="button" onClick={() => setPayerPersonId(null)}
                    className={`px-4 py-2 rounded-xl text-sm font-medium border-2 transition ${payerPersonId === null ? "border-indigo-600 bg-indigo-50 text-indigo-700" : "border-slate-200 text-slate-600 hover:border-slate-300"}`}>
                    {t("split.paidByMe")}
                  </button>
                  {people.filter((p) => !p.is_me).map((p) => (
                    <button key={p.id} type="button" onClick={() => setPayerPersonId(p.id)}
                      className={`px-4 py-2 rounded-xl text-sm font-medium border-2 transition ${payerPersonId === p.id ? "text-white" : "border-slate-200 text-slate-600 hover:border-slate-300"}`}
                      style={payerPersonId === p.id ? { borderColor: p.color, backgroundColor: p.color } : {}}>
                      {p.name}
                    </button>
                  ))}
                </div>
                {payerPersonId === null && accounts.length > 0 && (
                  <div className="space-y-1">
                    <label className="text-sm text-slate-500">{t("split.deductFromAccount")}</label>
                    <select className="input" value={payerAccountId ?? ""} onChange={(e) => setPayerAccountId(e.target.value ? Number(e.target.value) : null)}>
                      <option value="">— No descontar —</option>
                      {accounts.map((a) => (
                        <option key={a.id} value={a.id}>{a.name} ({formatMoney(a.current_balance, a.currency)})</option>
                      ))}
                    </select>
                  </div>
                )}
              </>
            ) : (
              <div className="space-y-2">
                {people.map((p) => (
                  <div key={p.id} className="flex items-center gap-3">
                    <span className="text-sm font-medium w-20 truncate shrink-0" style={{ color: p.color }}>
                      {p.is_me ? "Yo" : p.name}
                    </span>
                    <NumericInput
                      className="input flex-1 font-mono text-sm"
                      value={payerAmounts[p.id] ?? 0}
                      onChange={(v) => setPayerAmounts((prev) => ({ ...prev, [p.id]: v }))}
                      placeholder="0"
                    />
                    <span className="text-xs text-slate-400 shrink-0 w-8">{currency}</span>
                  </div>
                ))}
                {(() => {
                  const paid = Object.values(payerAmounts).reduce((s, v) => s + v, 0);
                  const diff = result.total_amount - paid;
                  return (
                    <p className={`text-xs font-medium ${Math.abs(diff) < 1 ? "text-emerald-600" : "text-amber-600"}`}>
                      Pagado: {formatMoney(paid, currency)} / {formatMoney(result.total_amount, currency)}
                      {Math.abs(diff) >= 1 && ` (${diff > 0 ? "falta" : "sobra"} ${formatMoney(Math.abs(diff), currency)})`}
                    </p>
                  );
                })()}
              </div>
            )}
          </div>

          {/* Per-person totals */}
          <div className="card">
            <h3 className="text-sm font-semibold text-slate-500 uppercase tracking-wide mb-3">{t("split.totals")}</h3>
            <ul className="space-y-2">
              {result.people.map((p) => (
                <li key={p.person_id} className="flex justify-between text-sm">
                  <span className="flex items-center gap-2">
                    <span className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: p.person_color }} />
                    {p.person_name}
                    {p.is_me && <span className="text-xs text-slate-400">(tú)</span>}
                  </span>
                  <span className="font-mono">{formatMoney(p.total, currency)}</span>
                </li>
              ))}
            </ul>
          </div>

          <button type="button" className={`btn-ghost w-full flex items-center justify-center gap-2 text-sm ${copied ? "text-emerald-600" : ""}`}
            onClick={() => {
              const lines = ["💰 División de cuenta", ""];
              result.people.forEach((p) => lines.push(`${p.person_name}: ${formatMoney(p.total, currency)}`));
              lines.push(`\nTotal: ${formatMoney(result.total_amount, currency)}`);
              navigator.clipboard.writeText(lines.join("\n"))
                .then(() => { setCopied(true); setTimeout(() => setCopied(false), 2500); })
                .catch(() => alert("No se pudo copiar al portapapeles"));
            }}>
            {copied ? "✓ Copiado" : "📋 Copiar para WhatsApp"}
          </button>

          <div className="flex gap-3">
            <button className="btn-ghost flex-1" onClick={() => setStep("assign")}>← Editar</button>
            <button
              className="btn-primary flex-1"
              disabled={settling || (multiPayer && Math.abs(result.total_amount - Object.values(payerAmounts).reduce((s, v) => s + v, 0)) > 1)}
              onClick={() => {
                if (multiPayer) {
                  setMultiSettlement(computeMultiPayer(people, result.people, payerAmounts));
                } else {
                  handleSettle(true);
                }
              }}
            >
              {settling ? "…" : "Liquidar →"}
            </button>
          </div>
        </div>
      )}

      {/* ══ MULTI-PAYER RESULT ══════════════════════════════════ */}
      {step === "settle" && multiSettlement && (
        <div className="space-y-4">
          <div className="card space-y-3">
            <h2 className="text-lg font-semibold">Resultado simplificado</h2>
            {multiSettlement.length === 0 ? (
              <p className="text-sm text-emerald-600 font-medium">¡Todos pagaron exacto! No hay deudas pendientes.</p>
            ) : (
              <ul className="space-y-2.5">
                {multiSettlement.map((tr, i) => (
                  <li key={i} className="flex justify-between items-center">
                    <div className="flex items-center gap-2 text-sm">
                      <span className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: tr.fromColor }} />
                      <span className="font-medium">{tr.fromName}</span>
                      <span className="text-slate-400">→</span>
                      <span className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: tr.toColor }} />
                      <span className="font-medium">{tr.toName}</span>
                    </div>
                    <span className="font-mono font-semibold text-rose-500">
                      {formatMoney(tr.amount, currency)}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <button className={`btn-ghost w-full flex items-center justify-center gap-2 ${copied ? "text-emerald-600" : ""}`}
            onClick={() => {
              const lines = ["💰 División de cuenta", ""];
              multiSettlement.forEach((tr) => lines.push(`${tr.fromName} → ${tr.toName}: ${formatMoney(tr.amount, currency)}`));
              navigator.clipboard.writeText(lines.join("\n"))
                .then(() => { setCopied(true); setTimeout(() => setCopied(false), 2500); })
                .catch(() => alert("No se pudo copiar al portapapeles"));
            }}>
            {copied ? "✓ Copiado" : "📋 Copiar para WhatsApp"}
          </button>
          <button className="btn-ghost w-full" onClick={reset}>{t("split.splitAnother")}</button>
        </div>
      )}

      {/* ══ SETTLEMENT RESULT ═══════════════════════════════════ */}
      {step === "settle" && settlement && (
        <div className="space-y-4">
          <div className="card space-y-3">
            <h2 className="text-lg font-semibold">{t("split.settleSummary")}</h2>

            {/* My share */}
            <div className="flex justify-between items-center py-2 border-b">
              <span className="text-sm text-slate-600">{t("split.myShare")}</span>
              <span className="font-mono font-semibold">
                {formatMoney(settlement.my_total, currency)}
              </span>
            </div>

            {/* Debt rows */}
            <ul className="space-y-2.5">
              {settlement.debts.map((d) => {
                const payerIsMe = settlement.payer_person_id === null ||
                  people.find((p) => p.id === settlement.payer_person_id)?.is_me;
                const label = payerIsMe
                  ? t("split.owesYou").replace("{name}", d.person_name)
                  : d.is_me
                    ? t("split.youOwe").replace("{name}", settlement.payer_name)
                    : `${d.person_name} → ${settlement.payer_name}`;

                return (
                  <li key={d.person_id} className="flex justify-between items-center">
                    <div className="flex items-center gap-2 text-sm">
                      <span
                        className="w-2.5 h-2.5 rounded-full"
                        style={{ backgroundColor: d.person_color }}
                      />
                      <span>{label}</span>
                    </div>
                    <span
                      className={`font-mono font-semibold ${
                        payerIsMe && !d.is_me ? "text-emerald-600" : "text-rose-500"
                      }`}
                    >
                      {formatMoney(d.amount, currency)}
                    </span>
                  </li>
                );
              })}
            </ul>

            {settlement.saved_transaction_id && (
              <p className="text-xs text-emerald-600 mt-2">
                ✓ Tu parte ({formatMoney(settlement.my_total, currency)}) guardada en gastos
              </p>
            )}
            {!settlement.saved_transaction_id && settlement.my_total === 0 && (
              <p className="text-xs text-slate-400 mt-2">
                No tienes ítems asignados — no se guardó ningún gasto.
              </p>
            )}
          </div>

          <button
            className={`btn-ghost w-full flex items-center justify-center gap-2 ${copied ? "text-emerald-600" : ""}`}
            onClick={() => {
              const payerIsMe = settlement.payer_person_id === null ||
                people.find((p) => p.id === settlement.payer_person_id)?.is_me;
              const lines = ["💰 División de cuenta"];
              if (settlement.debts.length > 0) {
                lines.push("");
                settlement.debts.forEach((d) => {
                  const who = payerIsMe
                    ? `${d.person_name} te debe`
                    : d.is_me
                      ? `Tú le debes a ${settlement.payer_name}`
                      : `${d.person_name} → ${settlement.payer_name}`;
                  lines.push(`${who}: ${formatMoney(d.amount, currency)}`);
                });
              }
              lines.push("");
              lines.push(`Mi parte: ${formatMoney(settlement.my_total, currency)}`);
              navigator.clipboard.writeText(lines.join("\n"))
                .then(() => { setCopied(true); setTimeout(() => setCopied(false), 2500); })
                .catch(() => alert("No se pudo copiar al portapapeles"));
            }}
          >
            {copied ? "✓ Copiado" : "📋 Copiar para WhatsApp"}
          </button>

          <button className="btn-ghost w-full" onClick={reset}>
            {t("split.splitAnother")}
          </button>
        </div>
      )}
    </div>
  );
}
