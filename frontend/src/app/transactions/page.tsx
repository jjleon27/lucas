"use client";
import { Suspense, useCallback, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { X } from "lucide-react";
import {
  Account,
  Transaction,
  createTransaction,
  getToken,
  listAccounts,
  listTransactions,
} from "@/lib/api";
import TransactionList, { CATEGORIES } from "@/components/TransactionList";
import PendingTransferList from "@/components/PendingTransferList";
import { useT, formatMoney } from "@/lib/i18n";
import NumericInput from "@/components/NumericInput";

// ─── Manual entry modal ──────────────────────────────────────────────────────
function ManualTxModal({
  accounts,
  onClose,
  onSaved,
}: {
  accounts: Account[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const { t, locale } = useT();
  const today = new Date().toISOString().slice(0, 10);

  const [isIncome, setIsIncome] = useState(false);
  const [amount, setAmount] = useState(0);
  const [merchant, setMerchant] = useState("");
  const [category, setCategory] = useState("Alimentación");
  const [customCategory, setCustomCategory] = useState("");
  const [date, setDate] = useState(today);
  const [accountId, setAccountId] = useState<number | null>(
    accounts.length > 0 ? accounts[0].id : null,
  );
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const effectiveCategory = category === "__otra__" && customCategory.trim()
    ? customCategory.trim()
    : category === "__otra__" ? "Otros" : category;

  async function save() {
    if (!amount || amount <= 0) { setErr("Escribe un monto válido"); return; }
    if (!merchant.trim()) { setErr("Escribe una descripción"); return; }
    if (!accountId) { setErr("Selecciona una cuenta para este movimiento"); return; }
    setBusy(true);
    setErr("");
    try {
      await createTransaction({
        amount,
        currency: accounts.find((a) => a.id === accountId)?.currency || "CLP",
        category: effectiveCategory,
        date,
        merchant: merchant.trim(),
        notes,
        is_income: isIncome,
        account_id: accountId,
      });
      onSaved();
      onClose();
    } catch (e: any) {
      setErr(e.message || "Error al guardar");
    } finally {
      setBusy(false);
    }
  }

  const currency = accounts.find((a) => a.id === accountId)?.currency || "CLP";

  return (
    <div
      className="fixed inset-0 z-50 bg-black/50 flex items-end sm:items-center justify-center p-4"
      onClick={onClose}
    >
      <div
        className="card w-full max-w-md space-y-4 max-h-[90vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between">
          <h3 className="text-lg font-semibold">Agregar movimiento</h3>
          <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-slate-100">
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Income / expense toggle */}
        <div className="flex rounded-xl overflow-hidden border border-slate-200 text-sm font-medium">
          <button
            type="button"
            onClick={() => { setIsIncome(false); if (category === "Ingresos") setCategory("Alimentación"); }}
            className={`flex-1 py-2.5 transition ${
              !isIncome ? "bg-rose-500 text-white" : "text-slate-500 hover:bg-slate-50"
            }`}
          >
            💸 Gasto
          </button>
          <button
            type="button"
            onClick={() => { setIsIncome(true); setCategory("Ingresos"); }}
            className={`flex-1 py-2.5 transition ${
              isIncome ? "bg-emerald-500 text-white" : "text-slate-500 hover:bg-slate-50"
            }`}
          >
            💰 Ingreso
          </button>
        </div>

        {/* Amount */}
        <label className="block">
          <span className="text-xs uppercase text-slate-500">Monto</span>
          <NumericInput
            className="input mt-1 font-mono text-2xl"
            value={amount}
            onChange={setAmount}
            placeholder="0"
            allowDecimals={currency !== "CLP"}
          />
          {amount > 0 && (
            <p className="text-xs text-slate-400 mt-1">{formatMoney(amount, currency)}</p>
          )}
        </label>

        {/* Description */}
        <label className="block">
          <span className="text-xs uppercase text-slate-500">Descripción</span>
          <input
            className="input mt-1"
            placeholder={isIncome ? "Ej: Sueldo, Show de magia, Freelance…" : "Ej: Almuerzo, Arriendo, Uber…"}
            value={merchant}
            onChange={(e) => setMerchant(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && save()}
          />
        </label>

        {/* Category + Date row */}
        <div className="grid grid-cols-2 gap-3">
          <label className="block">
            <span className="text-xs uppercase text-slate-500">Categoría</span>
            <select
              className="input mt-1"
              value={category}
              onChange={(e) => { setCategory(e.target.value); if (e.target.value !== "__otra__") setCustomCategory(""); }}
            >
              {(isIncome
                ? ["Ingresos", "Transferencia", "Otros"]
                : CATEGORIES.filter((c) => c !== "Ingresos")
              ).map((c) => (
                <option key={c} value={c}>{c}</option>
              ))}
              <option value="__otra__">✏️ Otra…</option>
            </select>
            {category === "__otra__" && (
              <input
                autoFocus
                className="input mt-1 text-sm"
                placeholder="Nueva categoría"
                value={customCategory}
                onChange={(e) => setCustomCategory(e.target.value)}
              />
            )}
          </label>
          <label className="block">
            <span className="text-xs uppercase text-slate-500">Fecha</span>
            <input
              type="date"
              lang={locale}
              className="input mt-1"
              value={date}
              onChange={(e) => setDate(e.target.value)}
            />
          </label>
        </div>

        {/* Account — obligatorio */}
        {accounts.length > 0 && (
          <label className="block">
            <span className="text-xs uppercase text-slate-500">Cuenta <span className="text-rose-500">*</span></span>
            <select
              className={`input mt-1 ${!accountId ? "border-rose-300" : ""}`}
              value={accountId ?? ""}
              onChange={(e) => setAccountId(e.target.value ? Number(e.target.value) : null)}
            >
              {!accountId && <option value="" disabled>Selecciona una cuenta…</option>}
              {accounts.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}{a.bank ? ` — ${a.bank}` : ""}
                </option>
              ))}
            </select>
          </label>
        )}

        {/* Notes */}
        <label className="block">
          <span className="text-xs uppercase text-slate-500">Nota (opcional)</span>
          <input
            className="input mt-1"
            placeholder="Agrega una nota…"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
          />
        </label>

        {err && <p className="text-sm text-rose-600">{err}</p>}

        <div className="flex gap-2 pt-1">
          <button className="btn-ghost flex-1" onClick={onClose} disabled={busy}>
            Cancelar
          </button>
          <button className="btn-primary flex-1" onClick={save} disabled={busy}>
            {busy ? "…" : "Guardar"}
          </button>
        </div>

        <p className="text-center text-xs text-slate-400">
          ¿Tienes un pantallazo o PDF?{" "}
          <a href="/upload" className="text-brand-600 hover:underline">
            Súbelo acá →
          </a>
        </p>
      </div>
    </div>
  );
}

// ─── Page ────────────────────────────────────────────────────────────────────
function TransactionsInner() {
  const router = useRouter();
  const params = useSearchParams();
  const { t } = useT();

  const pending = params.get("transfers") === "pending";
  const filterAccountId = params.get("account_id") ? Number(params.get("account_id")) : null;
  const [txs, setTxs] = useState<Transaction[] | null>(null);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [showManual, setShowManual] = useState(false);

  const load = useCallback(async () => {
    setTxs(null);
    try {
      const [list, accs] = await Promise.all([
        listTransactions(
          pending ? { pending_transfers: true }
          : filterAccountId ? { account_id: filterAccountId }
          : undefined,
        ),
        listAccounts(),
      ]);
      setTxs(list);
      setAccounts(accs);
    } catch {
      router.replace("/");
    }
  }, [pending, filterAccountId, router]);

  useEffect(() => {
    if (!getToken()) {
      router.replace("/");
      return;
    }
    load();
  }, [router, load]);

  return (
    <div className="max-w-5xl mx-auto w-full space-y-6 pb-24 md:pb-0">
      {pending ? (
        <>
          <header className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h1 className="text-3xl font-semibold tracking-tight">
                {t("tx.pending.title")}
              </h1>
              <p className="text-slate-500 text-sm max-w-2xl mt-1">
                {t("tx.pending.subtitle")}
              </p>
            </div>
            <a href="/transactions" className="btn-ghost text-sm">
              ← {t("tx.pending.clearFilter")}
            </a>
          </header>

          {txs === null ? (
            <div className="text-slate-500">{t("tx.loading")}</div>
          ) : (
            <PendingTransferList txs={txs} accounts={accounts} onLinked={load} />
          )}
        </>
      ) : (
        <>
          <header className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
            <div>
              <h1 className="text-2xl sm:text-3xl font-semibold tracking-tight">
                {filterAccountId
                  ? (accounts.find((a) => a.id === filterAccountId)?.name ?? t("tx.title"))
                  : t("tx.title")}
              </h1>
              {filterAccountId && (
                <a href="/accounts" className="text-xs text-slate-400 hover:text-slate-600">
                  ← Volver a cuentas
                </a>
              )}
            </div>
            <div className="flex items-center gap-2">
              <a href="/upload" className="btn-ghost text-sm">
                📷 <span className="hidden sm:inline">Subir foto</span>
              </a>
              <button className="btn-primary text-sm" onClick={() => setShowManual(true)}>
                ✏️ {t("tx.add")}
              </button>
            </div>
          </header>

          {txs === null ? (
            <div className="text-slate-500">{t("tx.loading")}</div>
          ) : (
            <TransactionList txs={txs} accounts={accounts} onRefresh={load} />
          )}
        </>
      )}

      {showManual && (
        <ManualTxModal
          accounts={accounts}
          onClose={() => setShowManual(false)}
          onSaved={load}
        />
      )}
    </div>
  );
}

export default function TransactionsPage() {
  return (
    <Suspense fallback={<div className="p-8 text-slate-500">…</div>}>
      <TransactionsInner />
    </Suspense>
  );
}
