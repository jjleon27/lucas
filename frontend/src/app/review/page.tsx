"use client";
/**
 * /review — Cola de revisión al estilo Kuanto.
 *
 * Los gastos importados desde emails del banco (status="pending_review") se
 * revisan de a uno: el usuario ve la tarjeta, puede cambiar categoría o monto,
 * y elige una acción: Confirmar / Por Cobrar / No es gasto.
 */
import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import {
  Transaction,
  Account,
  getToken,
  listPendingTransactions,
  listAccounts,
  reviewTransaction,
  getForwardingAddress,
  ForwardingAddressOut,
} from "@/lib/api";
import { formatMoney } from "@/lib/i18n";
import NumericInput from "@/components/NumericInput";
import { CATEGORIES } from "@/components/TransactionList";
import { Check, Clock, Trash2, ChevronRight, Copy, Mail } from "lucide-react";

// Emoji por categoría
const CAT_EMOJI: Record<string, string> = {
  "Alimentación": "🍽️", "Supermercado": "🛒", "Transporte": "🚌",
  "Entretenimiento": "🎭", "Bares y Salidas": "🍻", "Compras": "🛍️",
  "Cuentas y Servicios": "💡", "Salud": "🏥", "Viajes": "✈️",
  "Suscripciones": "📺", "Tecnología": "💻", "Educación": "📚",
  "Hogar": "🏠", "Ropa": "👕", "Ingresos": "💰",
  "Transferencia": "↔️", "Inversión": "📈", "Seguros": "🛡️",
  "Otros": "📦",
};

// ── Card de revisión ─────────────────────────────────────────────────────────
function ReviewCard({
  tx,
  total,
  current,
  creditAccounts,
  onAction,
}: {
  tx: Transaction;
  total: number;
  current: number;
  creditAccounts: Account[];
  onAction: (
    action: "confirm" | "skip" | "not_expense" | "pending" | "confirm_cc_payment",
    overrides?: { category?: string; merchant?: string; amount?: number; remember?: boolean; target_account_id?: number }
  ) => void;
}) {
  const [category, setCategory] = useState(tx.category || "Otros");
  const [merchant, setMerchant] = useState(tx.merchant || "");
  const [amount, setAmount] = useState(tx.amount);
  const [remember, setRemember] = useState(true);
  const [editingMerchant, setEditingMerchant] = useState(false);
  const [editingAmount, setEditingAmount] = useState(false);
  const [acting, setActing] = useState(false);
  const [selectedCCId, setSelectedCCId] = useState<number | null>(creditAccounts[0]?.id ?? null);

  // A pending_review with is_transfer=true is a CC payment waiting for account assignment
  const isCCPayment = tx.is_transfer && !tx.is_income;

  async function act(
    action: "confirm" | "skip" | "not_expense" | "pending" | "confirm_cc_payment",
  ) {
    if (acting) return;
    setActing(true);
    if (action === "confirm_cc_payment") {
      onAction(action, { target_account_id: selectedCCId ?? undefined });
    } else {
      onAction(action, { category, merchant, amount, remember });
    }
  }

  return (
    <div className="card space-y-4 animate-in fade-in duration-200">
      {/* Progress */}
      <div className="flex items-center justify-between text-xs text-slate-400">
        <span>{current} de {total} pendientes</span>
        <div className="h-1.5 flex-1 mx-3 bg-slate-100 rounded-full overflow-hidden">
          <div
            className="h-full bg-indigo-500 rounded-full transition-all"
            style={{ width: `${Math.round(((current - 1) / Math.max(total, 1)) * 100)}%` }}
          />
        </div>
        <span className="font-medium text-indigo-600">{current}/{total}</span>
      </div>

      {/* Amount + merchant */}
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          {editingMerchant ? (
            <input
              autoFocus
              className="input text-base font-semibold w-full"
              value={merchant}
              onChange={(e) => setMerchant(e.target.value)}
              onBlur={() => setEditingMerchant(false)}
              onKeyDown={(e) => e.key === "Enter" && setEditingMerchant(false)}
            />
          ) : (
            <button
              className="text-left text-base font-semibold text-slate-800 hover:text-indigo-600 transition truncate max-w-full"
              onClick={() => setEditingMerchant(true)}
              title="Toca para editar el nombre"
            >
              {merchant || <span className="text-slate-400 italic">Sin nombre</span>}
              <span className="ml-1 text-xs text-slate-300">✏️</span>
            </button>
          )}
          <p className="text-xs text-slate-400 mt-0.5">{tx.date}</p>
          {tx.notes && (
            <p className="text-xs text-slate-400 mt-0.5 truncate" title={tx.notes}>
              {tx.notes}
            </p>
          )}
        </div>

        <div className="text-right shrink-0">
          {editingAmount ? (
            <NumericInput
              autoFocus
              className="input w-28 text-right font-mono font-bold text-lg"
              value={amount}
              onChange={setAmount}
              onBlur={() => setEditingAmount(false)}
            />
          ) : (
            <button
              className="font-mono font-bold text-xl text-rose-500 hover:text-rose-600 transition"
              onClick={() => setEditingAmount(true)}
              title="Toca para editar el monto"
            >
              {formatMoney(amount, tx.currency || "CLP")}
            </button>
          )}
          <p className="text-xs text-slate-400">
            {tx.is_income ? "ingreso" : "gasto"}
          </p>
        </div>
      </div>

      {/* Category chips — horizontal scroll */}
      <div>
        <p className="text-[10px] uppercase font-semibold text-slate-400 mb-2 tracking-wide">
          Categoría
        </p>
        <div className="flex gap-1.5 overflow-x-auto pb-1 scrollbar-hide -mx-1 px-1">
          {CATEGORIES.map((cat) => (
            <button
              key={cat}
              type="button"
              onClick={() => setCategory(cat)}
              className={`shrink-0 flex items-center gap-1 px-2.5 py-1.5 rounded-full text-xs font-medium border transition-all ${
                category === cat
                  ? "bg-indigo-600 border-indigo-600 text-white shadow-sm"
                  : "bg-white border-slate-200 text-slate-600 hover:border-indigo-300"
              }`}
            >
              <span>{CAT_EMOJI[cat] || "📦"}</span>
              <span>{cat}</span>
            </button>
          ))}
        </div>
      </div>

      {/* Remember toggle */}
      <label className="flex items-center gap-2 text-sm text-slate-600 cursor-pointer select-none">
        <button
          type="button"
          role="checkbox"
          aria-checked={remember}
          onClick={() => setRemember((r) => !r)}
          className={`w-9 h-5 rounded-full transition-colors ${
            remember ? "bg-indigo-500" : "bg-slate-200"
          } relative`}
        >
          <span
            className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${
              remember ? "translate-x-4" : ""
            }`}
          />
        </button>
        Clasificar así automáticamente en el futuro
      </label>

      {/* CC payment: account selector */}
      {isCCPayment && creditAccounts.length > 0 && (
        <div className="bg-sky-50 border border-sky-200 rounded-2xl p-3 space-y-2">
          <p className="text-xs font-semibold text-sky-700 uppercase tracking-wide">
            💳 Pago de tarjeta de crédito
          </p>
          <p className="text-xs text-sky-600">¿A qué tarjeta corresponde este pago?</p>
          <select
            className="input text-sm py-1.5"
            value={selectedCCId ?? ""}
            onChange={(e) => setSelectedCCId(e.target.value ? Number(e.target.value) : null)}
          >
            {creditAccounts.map((a) => (
              <option key={a.id} value={a.id}>{a.name}</option>
            ))}
          </select>
        </div>
      )}

      {/* Action buttons */}
      {isCCPayment ? (
        <div className="grid grid-cols-2 gap-2 pt-1">
          <button
            type="button"
            disabled={acting || !selectedCCId}
            onClick={() => act("confirm_cc_payment")}
            className="flex flex-col items-center gap-1 py-3 rounded-xl bg-emerald-50 border-2 border-emerald-200 text-emerald-700 font-medium text-xs hover:bg-emerald-100 active:scale-95 transition disabled:opacity-50"
          >
            <Check className="w-5 h-5" />
            Confirmar pago
          </button>
          <button
            type="button"
            disabled={acting}
            onClick={() => act("not_expense")}
            className="flex flex-col items-center gap-1 py-3 rounded-xl bg-slate-50 border-2 border-slate-200 text-slate-500 font-medium text-xs hover:bg-slate-100 active:scale-95 transition disabled:opacity-50"
          >
            <Trash2 className="w-5 h-5" />
            Eliminar
          </button>
        </div>
      ) : (
      <div className="grid grid-cols-3 gap-2 pt-1">
        <button
          type="button"
          disabled={acting}
          onClick={() => act("confirm")}
          className="flex flex-col items-center gap-1 py-3 rounded-xl bg-emerald-50 border-2 border-emerald-200 text-emerald-700 font-medium text-xs hover:bg-emerald-100 active:scale-95 transition disabled:opacity-50"
        >
          <Check className="w-5 h-5" />
          Confirmar
        </button>

        <button
          type="button"
          disabled={acting}
          onClick={() => act("pending")}
          className="flex flex-col items-center gap-1 py-3 rounded-xl bg-amber-50 border-2 border-amber-200 text-amber-700 font-medium text-xs hover:bg-amber-100 active:scale-95 transition disabled:opacity-50"
        >
          <Clock className="w-5 h-5" />
          Por Cobrar
        </button>

        <button
          type="button"
          disabled={acting}
          onClick={() => act("not_expense")}
          className="flex flex-col items-center gap-1 py-3 rounded-xl bg-slate-50 border-2 border-slate-200 text-slate-500 font-medium text-xs hover:bg-slate-100 active:scale-95 transition disabled:opacity-50"
        >
          <Trash2 className="w-5 h-5" />
          No es gasto
        </button>
      </div>
      )}

      {/* Skip */}
      <button
        type="button"
        disabled={acting}
        onClick={() => act("skip")}
        className="w-full text-center text-sm text-slate-400 hover:text-slate-600 py-1 transition disabled:opacity-50 flex items-center justify-center gap-1"
      >
        Saltar por ahora <ChevronRight className="w-3.5 h-3.5" />
      </button>
    </div>
  );
}

// ── Setup Card (cuando no hay pendientes) ────────────────────────────────────
function SetupCard({ address }: { address: ForwardingAddressOut | null }) {
  const [copied, setCopied] = useState(false);

  function copy() {
    if (!address) return;
    navigator.clipboard.writeText(address.email).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }

  return (
    <div className="card space-y-4">
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 rounded-full bg-indigo-100 flex items-center justify-center">
          <Mail className="w-5 h-5 text-indigo-600" />
        </div>
        <div>
          <h2 className="font-semibold text-slate-800">Conecta tu banco</h2>
          <p className="text-sm text-slate-500">Reenvía las notificaciones a Lucas</p>
        </div>
      </div>

      {address ? (
        <>
          <div className="bg-slate-50 rounded-xl p-3 flex items-center gap-2">
            <span className="font-mono text-sm text-slate-700 flex-1 break-all">
              {address.email}
            </span>
            <button
              onClick={copy}
              className="shrink-0 p-1.5 rounded-lg hover:bg-slate-200 transition"
              title="Copiar"
            >
              {copied ? <Check className="w-4 h-4 text-emerald-500" /> : <Copy className="w-4 h-4 text-slate-500" />}
            </button>
          </div>

          <div className="text-sm text-slate-600 space-y-2">
            <p className="font-medium text-slate-700">¿Cómo configurarlo?</p>
            <ol className="list-decimal list-inside space-y-1.5 text-slate-500">
              <li>Abre Gmail en tu celular → Configuración → tu cuenta</li>
              <li>Toca <strong>Filtros</strong> → <strong>Crear filtro</strong></li>
              <li>
                En "De:" escribe el email de tu banco:<br />
                <span className="font-mono text-xs bg-slate-100 px-1 rounded">notificaciones@bancochile.cl</span>,{" "}
                <span className="font-mono text-xs bg-slate-100 px-1 rounded">alertas@santander.cl</span>, etc.
              </li>
              <li>Marca <strong>"Reenviar a"</strong> y pega tu dirección Lucas</li>
            </ol>
          </div>

          <p className="text-xs text-slate-400 bg-slate-50 rounded-lg p-3">
            💡 Cada cobro que llegue por email aparecerá aquí para confirmar — tú decides qué se guarda.
          </p>
        </>
      ) : (
        <p className="text-sm text-slate-400">Cargando dirección…</p>
      )}
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────
export default function ReviewPage() {
  const router = useRouter();
  const [txs, setTxs] = useState<Transaction[]>([]);
  const [creditAccounts, setCreditAccounts] = useState<Account[]>([]);
  const [loading, setLoading] = useState(true);
  const [address, setAddress] = useState<ForwardingAddressOut | null>(null);
  const [skipped, setSkipped] = useState<Set<number>>(new Set());

  useEffect(() => {
    if (!getToken()) { router.replace("/"); return; }
    Promise.all([
      listPendingTransactions(),
      getForwardingAddress(),
      listAccounts(),
    ]).then(([pending, addr, accounts]) => {
      setTxs(pending);
      setAddress(addr);
      setCreditAccounts(accounts.filter((a) => a.type === "credit" && !a.archived));
    }).finally(() => setLoading(false));
  }, [router]);

  const handleAction = useCallback(
    async (
      tx: Transaction,
      action: "confirm" | "skip" | "not_expense" | "pending" | "confirm_cc_payment",
      overrides?: { category?: string; merchant?: string; amount?: number; remember?: boolean; target_account_id?: number },
    ) => {
      if (action === "skip") {
        setSkipped((s) => new Set([...s, tx.id]));
        return;
      }
      try {
        await reviewTransaction(tx.id, {
          action,
          category: overrides?.category,
          merchant: overrides?.merchant,
          amount: overrides?.amount,
          remember: overrides?.remember,
          target_account_id: overrides?.target_account_id,
        });
        // Remove from list
        setTxs((prev) => prev.filter((t) => t.id !== tx.id));
      } catch (e: any) {
        alert(e.message || "Error");
      }
    },
    [],
  );

  // Visible = pending minus skipped (skipped are shown at the end)
  const visible = txs.filter((t) => !skipped.has(t.id));
  const skippedTxs = txs.filter((t) => skipped.has(t.id));
  const currentTx = visible[0] ?? skippedTxs[0] ?? null;
  const totalPending = txs.length;
  const currentIdx = currentTx ? txs.findIndex((t) => t.id === currentTx.id) + 1 : 0;

  if (loading) {
    return (
      <div className="max-w-lg mx-auto pt-16 text-center text-slate-400">
        <div className="w-8 h-8 border-2 border-indigo-400 border-t-transparent rounded-full animate-spin mx-auto mb-3" />
        Cargando cola de revisión…
      </div>
    );
  }

  return (
    <div className="max-w-lg mx-auto space-y-4 pb-24">
      <div className="flex items-center justify-between mb-2">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Revisar gastos</h1>
          <p className="text-sm text-slate-500">
            {totalPending > 0
              ? `${totalPending} movimiento${totalPending !== 1 ? "s" : ""} por revisar`
              : "Todo al día ✓"}
          </p>
        </div>
        {totalPending > 0 && (
          <span className="bg-rose-500 text-white text-xs font-bold px-2.5 py-1 rounded-full">
            {totalPending}
          </span>
        )}
      </div>

      {currentTx ? (
        <ReviewCard
          key={currentTx.id}
          tx={currentTx}
          total={totalPending}
          current={currentIdx}
          creditAccounts={creditAccounts}
          onAction={(action, overrides) => handleAction(currentTx, action, overrides)}
        />
      ) : (
        <div className="card text-center py-8 space-y-2">
          <p className="text-3xl">🎉</p>
          <p className="font-semibold text-slate-700">¡Todo revisado!</p>
          <p className="text-sm text-slate-400">Los nuevos gastos del banco aparecerán acá automáticamente.</p>
        </div>
      )}

      {/* Setup card — always visible below */}
      <SetupCard address={address} />
    </div>
  );
}
