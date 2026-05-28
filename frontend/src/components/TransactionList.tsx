"use client";
/**
 * TransactionList — shows transactions with inline edit + delete.
 *
 * - Click the pencil icon to expand an inline edit form
 * - Delete with a confirmation prompt
 * - Duplicate badge if a transaction shares date+amount+merchant with another
 */
import { useState, useMemo } from "react";
import { Pencil, Trash2, Check, X, AlertTriangle } from "lucide-react";
import { Transaction, Account, updateTransaction, deleteTransaction } from "@/lib/api";
import { useT, formatMoney } from "@/lib/i18n";
import NumericInput from "@/components/NumericInput";

interface Props {
  txs: Transaction[];
  accounts?: Account[];
  onRefresh?: () => void;
}

export const CATEGORIES = [
  "Alimentación", "Supermercado", "Transporte", "Compras",
  "Entretenimiento", "Bares y Salidas", "Cuentas y Servicios",
  "Salud", "Viajes", "Suscripciones", "Tecnología",
  "Educación", "Hogar", "Ropa", "Ingresos", "Transferencia",
  "Pago Tarjeta", "Inversión", "Seguros", "Otros",
];

function isDuplicate(tx: Transaction, all: Transaction[]): boolean {
  return all.some(
    (other) =>
      other.id !== tx.id &&
      other.date === tx.date &&
      Math.abs(other.amount - tx.amount) < 0.01 &&
      other.merchant?.toLowerCase() === tx.merchant?.toLowerCase(),
  );
}

interface EditState {
  amount: number;
  merchant: string;
  category: string;
  date: string;
  notes: string;
  is_income: boolean;
  account_id: number | null;
}

export default function TransactionList({ txs: initial, accounts = [], onRefresh }: Props) {
  const { t } = useT();
  const [txs, setTxs] = useState<Transaction[]>(initial);
  const [editId, setEditId] = useState<number | null>(null);
  const [editState, setEditState] = useState<EditState | null>(null);
  const [saving, setSaving] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState<number | null>(null);

  // Sync when parent refreshes
  useState(() => { setTxs(initial); });

  const dupeIds = useMemo(
    () => new Set(txs.filter((tx) => isDuplicate(tx, txs)).map((tx) => tx.id)),
    [txs],
  );

  function startEdit(tx: Transaction) {
    setEditId(tx.id);
    setEditState({
      amount: Math.abs(tx.amount),
      merchant: tx.merchant,
      category: tx.category,
      date: tx.date,
      notes: tx.notes,
      is_income: tx.is_income,
      account_id: tx.account_id,
    });
    setConfirmDelete(null);
  }

  function cancelEdit() {
    setEditId(null);
    setEditState(null);
  }

  async function saveEdit(tx: Transaction) {
    if (!editState) return;
    setSaving(true);
    try {
      const updated = await updateTransaction(tx.id, {
        amount: editState.amount,
        merchant: editState.merchant,
        category: editState.category,
        date: editState.date,
        notes: editState.notes,
        is_income: editState.is_income,
        account_id: editState.account_id,
      });
      setTxs((prev) => prev.map((t) => (t.id === tx.id ? updated : t)));
      setEditId(null);
      setEditState(null);
      onRefresh?.();
    } catch (e) {
      alert(String(e));
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(id: number) {
    setSaving(true);
    try {
      await deleteTransaction(id);
      setTxs((prev) => prev.filter((t) => t.id !== id));
      setConfirmDelete(null);
      onRefresh?.();
    } catch (e) {
      alert(String(e));
    } finally {
      setSaving(false);
    }
  }

  if (txs.length === 0) {
    return <div className="card text-center text-slate-500">{t("tx.empty")}</div>;
  }

  // Hide the "receiving" side of linked transfers — show only the outgoing side
  const visibleTxs = txs.filter(
    (tx) => !(tx.is_transfer && tx.is_income && tx.linked_transaction_id != null),
  );

  // Group by date, preserving order
  const dates = [...new Set(visibleTxs.map((tx) => tx.date))];

  return (
    <div className="space-y-4">
      {dates.map((date) => (
        <div key={date}>
          {/* Date header */}
          <p className="text-xs font-semibold text-slate-400 uppercase tracking-wide px-1 mb-1">
            {date}
          </p>
          <div className="space-y-1">
          {visibleTxs.filter((tx) => tx.date === date).map((tx) => {
        const isEditing = editId === tx.id;
        const isDupe = dupeIds.has(tx.id);
        const isDeleting = confirmDelete === tx.id;
        const accountName = accounts.find((a) => a.id === tx.account_id)?.name;

        return (
          <div
            key={tx.id}
            className={`card p-0 overflow-hidden transition-colors ${
              isDupe ? "border border-amber-300" : ""
            }`}
          >
            {/* Santander-style: description wraps left, amount right */}
            <div className="px-3 py-3">
              {/* Row 1: merchant (wraps up to 2 lines) + amount pinned right */}
              <div className="flex items-start justify-between gap-3">
                <p className="font-medium leading-snug line-clamp-2 break-words min-w-0">
                  {tx.merchant || "—"}
                </p>
                <span className={`font-mono text-sm shrink-0 pt-px ${tx.is_income ? "text-emerald-600" : ""}`}>
                  {tx.is_income ? "+" : "−"}{formatMoney(Math.abs(tx.amount), tx.currency)}
                </span>
              </div>

              {/* Row 2: date · category · badges (left) + edit/delete (right) */}
              <div className="flex items-center justify-between gap-2 mt-1">
                <div className="flex items-center gap-1 flex-wrap min-w-0 text-xs text-slate-400">
                  <span className="px-1.5 py-0.5 rounded-full bg-slate-100 text-slate-500 max-w-[9rem] truncate">
                    {tx.category}
                  </span>
                  {tx.is_transfer && (
                    <span className="px-1.5 py-0.5 rounded-full bg-sky-100 text-sky-600 max-w-[7rem] truncate">
                      ↔ {tx.category === "Pago Tarjeta" ? "Sin presup." : "Transfer"}
                    </span>
                  )}
                  {accountName && (
                    <span className="truncate max-w-[8rem]">{accountName}</span>
                  )}
                  {isDupe && (
                    <span className="inline-flex items-center gap-0.5 text-amber-600 shrink-0">
                      <AlertTriangle size={11} />
                      {t("tx.duplicateWarning")}
                    </span>
                  )}
                </div>

                <div className="flex gap-1 shrink-0">
                  <button
                    onClick={() => (isEditing ? cancelEdit() : startEdit(tx))}
                    className="p-1.5 rounded-lg hover:bg-slate-100 text-slate-400 hover:text-slate-700 transition-colors"
                    title={isEditing ? t("tx.cancel") : t("tx.edit")}
                  >
                    {isEditing ? <X size={14} /> : <Pencil size={14} />}
                  </button>
                  {!isEditing && (
                    <button
                      onClick={() =>
                        isDeleting ? setConfirmDelete(null) : setConfirmDelete(tx.id)
                      }
                      className={`p-1.5 rounded-lg transition-colors ${
                        isDeleting
                          ? "bg-rose-100 text-rose-600"
                          : "hover:bg-slate-100 text-slate-400 hover:text-rose-500"
                      }`}
                      title={t("tx.delete")}
                    >
                      <Trash2 size={14} />
                    </button>
                  )}
                </div>
              </div>
            </div>

            {/* Delete confirmation bar */}
            {isDeleting && !isEditing && (
              <div className="bg-rose-50 border-t border-rose-100 px-4 py-2 flex items-center justify-between gap-3">
                <span className="text-sm text-rose-700">{t("tx.deleteConfirm")}</span>
                <div className="flex gap-2">
                  <button
                    className="text-xs px-3 py-1 rounded-lg bg-white border border-rose-200 text-rose-600 hover:bg-rose-50"
                    onClick={() => setConfirmDelete(null)}
                    disabled={saving}
                  >
                    {t("tx.cancel")}
                  </button>
                  <button
                    className="text-xs px-3 py-1 rounded-lg bg-rose-600 text-white hover:bg-rose-700"
                    onClick={() => handleDelete(tx.id)}
                    disabled={saving}
                  >
                    {saving ? "…" : t("tx.delete")}
                  </button>
                </div>
              </div>
            )}

            {/* Inline edit form */}
            {isEditing && editState && (
              <div className="border-t border-slate-100 bg-slate-50 px-4 py-3 space-y-3">
                <div className="grid grid-cols-2 gap-3">
                  <label className="block">
                    <span className="text-xs text-slate-500 uppercase">{t("tx.amount")}</span>
                    <NumericInput
                      className="input mt-0.5 font-mono"
                      value={editState.amount}
                      onChange={(v) => setEditState((s) => s && { ...s, amount: v })}
                      allowDecimals
                    />
                  </label>
                  <label className="block">
                    <span className="text-xs text-slate-500 uppercase">{t("tx.date")}</span>
                    <input
                      type="date"
                      className="input mt-0.5"
                      value={editState.date}
                      onChange={(e) => setEditState((s) => s && { ...s, date: e.target.value })}
                    />
                  </label>
                </div>
                <label className="block">
                  <span className="text-xs text-slate-500 uppercase">{t("tx.merchant")}</span>
                  <input
                    className="input mt-0.5"
                    value={editState.merchant}
                    onChange={(e) => setEditState((s) => s && { ...s, merchant: e.target.value })}
                  />
                </label>
                <div className="grid grid-cols-2 gap-3">
                  <label className="block">
                    <span className="text-xs text-slate-500 uppercase">{t("tx.category")}</span>
                    <select
                      className="input mt-0.5"
                      value={CATEGORIES.includes(editState.category) ? editState.category : "__otra__"}
                      onChange={(e) => {
                        if (e.target.value !== "__otra__") {
                          setEditState((s) => s && { ...s, category: e.target.value });
                        }
                      }}
                    >
                      {CATEGORIES.map((c) => <option key={c}>{c}</option>)}
                      <option value="__otra__">✏️ Otra categoría...</option>
                    </select>
                    {!CATEGORIES.includes(editState.category) && (
                      <input
                        className="input mt-1"
                        placeholder="Nombre de categoría"
                        value={editState.category}
                        onChange={(e) => setEditState((s) => s && { ...s, category: e.target.value })}
                      />
                    )}
                  </label>
                  <label className="flex items-center gap-2 mt-5">
                    <input
                      type="checkbox"
                      checked={editState.is_income}
                      onChange={(e) => setEditState((s) => s && { ...s, is_income: e.target.checked })}
                      className="rounded"
                    />
                    <span className="text-sm">Es ingreso</span>
                  </label>
                </div>
                {accounts.length > 0 && (
                  <label className="block">
                    <span className="text-xs text-slate-500 uppercase">Cuenta</span>
                    <select
                      className="input mt-0.5"
                      value={editState.account_id ?? ""}
                      onChange={(e) => setEditState((s) => s && { ...s, account_id: e.target.value ? Number(e.target.value) : null })}
                    >
                      <option value="">Sin cuenta</option>
                      {accounts.map((a) => (
                        <option key={a.id} value={a.id}>{a.name}{a.bank ? ` — ${a.bank}` : ""}</option>
                      ))}
                    </select>
                  </label>
                )}
                <label className="block">
                  <span className="text-xs text-slate-500 uppercase">{t("tx.notes")}</span>
                  <input
                    className="input mt-0.5"
                    value={editState.notes}
                    onChange={(e) => setEditState((s) => s && { ...s, notes: e.target.value })}
                  />
                </label>
                <div className="flex gap-2 pt-1">
                  <button
                    className="btn-primary flex-1 flex items-center justify-center gap-1"
                    onClick={() => saveEdit(tx)}
                    disabled={saving}
                  >
                    <Check size={14} />
                    {saving ? "…" : t("tx.save")}
                  </button>
                  <button
                    className="btn-ghost"
                    onClick={cancelEdit}
                    disabled={saving}
                  >
                    {t("tx.cancel")}
                  </button>
                </div>
              </div>
            )}
          </div>
        );
      })}
          </div>
        </div>
      ))}
    </div>
  );
}
