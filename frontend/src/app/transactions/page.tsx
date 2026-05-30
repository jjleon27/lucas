"use client";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { ChevronDown, ChevronUp, X } from "lucide-react";
import {
  Account,
  Transaction,
  createOwnTransfer,
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

  type Mode = "expense" | "income" | "transfer";
  const [mode, setMode] = useState<Mode>("expense");
  const [amount, setAmount] = useState(0);
  const [merchant, setMerchant] = useState("");
  const [category, setCategory] = useState("Alimentación");
  const [customCategory, setCustomCategory] = useState("");
  const [date, setDate] = useState(today);
  const [accountId, setAccountId] = useState<number | null>(
    accounts.length > 0 ? accounts[0].id : null,
  );
  const [destAccountId, setDestAccountId] = useState<number | null>(
    accounts.length > 1 ? accounts[1].id : null,
  );
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const effectiveCategory = category === "__otra__" && customCategory.trim()
    ? customCategory.trim()
    : category === "__otra__" ? "Otros" : category;

  function switchMode(m: Mode) {
    setMode(m);
    setErr("");
    if (m === "income" && category === "Alimentación") setCategory("Ingresos");
    if (m === "expense" && category === "Ingresos") setCategory("Alimentación");
  }

  async function save() {
    if (!amount || amount <= 0) { setErr("Escribe un monto válido"); return; }
    setBusy(true);
    setErr("");
    try {
      if (mode === "transfer") {
        if (!accountId) { setErr("Selecciona la cuenta origen"); setBusy(false); return; }
        if (!destAccountId) { setErr("Selecciona la cuenta destino"); setBusy(false); return; }
        if (accountId === destAccountId) { setErr("Las cuentas deben ser distintas"); setBusy(false); return; }
        await createOwnTransfer({
          from_account_id: accountId,
          to_account_id: destAccountId,
          amount,
          date,
          merchant: merchant.trim() || "Transferencia entre cuentas",
          notes,
          currency: accounts.find((a) => a.id === accountId)?.currency || "CLP",
        });
      } else {
        if (!merchant.trim()) { setErr("Escribe una descripción"); setBusy(false); return; }
        if (accounts.length > 0 && !accountId) { setErr("Selecciona una cuenta"); setBusy(false); return; }
        await createTransaction({
          amount,
          currency: accounts.find((a) => a.id === accountId)?.currency || "CLP",
          category: effectiveCategory,
          date,
          merchant: merchant.trim(),
          notes,
          is_income: mode === "income",
          account_id: accountId,
        });
      }
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

        {/* Mode toggle */}
        <div className="flex rounded-xl overflow-hidden border border-slate-200 text-sm font-medium">
          <button
            type="button"
            onClick={() => switchMode("expense")}
            className={`flex-1 py-2.5 transition ${
              mode === "expense" ? "bg-rose-500 text-white" : "text-slate-500 hover:bg-slate-50"
            }`}
          >
            💸 Gasto
          </button>
          <button
            type="button"
            onClick={() => switchMode("income")}
            className={`flex-1 py-2.5 transition ${
              mode === "income" ? "bg-emerald-500 text-white" : "text-slate-500 hover:bg-slate-50"
            }`}
          >
            💰 Ingreso
          </button>
          <button
            type="button"
            onClick={() => switchMode("transfer")}
            className={`flex-1 py-2.5 transition ${
              mode === "transfer" ? "bg-sky-500 text-white" : "text-slate-500 hover:bg-slate-50"
            }`}
          >
            🔄 Transferir
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

        {mode === "transfer" ? (
          /* ── Transfer mode ─────────────────────────────────────────────── */
          <div className="space-y-3">
            {accounts.length > 0 && (
              <label className="block">
                <span className="text-xs uppercase text-slate-500">Cuenta origen</span>
                <select
                  className="input mt-1"
                  value={accountId ?? ""}
                  onChange={(e) => setAccountId(Number(e.target.value))}
                >
                  {accounts.map((a) => (
                    <option key={a.id} value={a.id}>{a.name}{a.bank ? ` — ${a.bank}` : ""}</option>
                  ))}
                </select>
              </label>
            )}
            {accounts.length > 0 && (
              <label className="block">
                <span className="text-xs uppercase text-slate-500">Cuenta destino</span>
                <select
                  className="input mt-1"
                  value={destAccountId ?? ""}
                  onChange={(e) => setDestAccountId(Number(e.target.value))}
                >
                  {accounts.filter((a) => a.id !== accountId).map((a) => (
                    <option key={a.id} value={a.id}>{a.name}{a.bank ? ` — ${a.bank}` : ""}</option>
                  ))}
                </select>
              </label>
            )}
            <div className="grid grid-cols-2 gap-3">
              <label className="block">
                <span className="text-xs uppercase text-slate-500">Descripción (opcional)</span>
                <input
                  className="input mt-1"
                  placeholder="Transferencia entre cuentas"
                  value={merchant}
                  onChange={(e) => setMerchant(e.target.value)}
                />
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
          </div>
        ) : (
          /* ── Expense / Income mode ──────────────────────────────────────── */
          <>
            <label className="block">
              <span className="text-xs uppercase text-slate-500">Descripción</span>
              <input
                className="input mt-1"
                placeholder={mode === "income" ? "Ej: Sueldo, Freelance…" : "Ej: Almuerzo, Arriendo, Uber…"}
                value={merchant}
                onChange={(e) => setMerchant(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && save()}
              />
            </label>

            <div className="grid grid-cols-2 gap-3">
              <label className="block">
                <span className="text-xs uppercase text-slate-500">Categoría</span>
                <select
                  className="input mt-1"
                  value={category}
                  onChange={(e) => { setCategory(e.target.value); if (e.target.value !== "__otra__") setCustomCategory(""); }}
                >
                  {(mode === "income"
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
          </>
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

  // URL params become initial filter state
  const initAccountId = params.get("account_id") ? Number(params.get("account_id")) : null;
  const initType = (params.get("type") as "expense" | "income") || "all";
  const initCategory = params.get("category") || "";

  const [allTxs, setAllTxs] = useState<Transaction[] | null>(null);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [showManual, setShowManual] = useState(false);

  // ── Filter state ─────────────────────────────────────────────────────────
  const [search, setSearch] = useState("");
  const [fType, setFType] = useState<"all" | "expense" | "income">(initType as any);
  const [fCategory, setFCategory] = useState(initCategory);
  const [fAccountId, setFAccountId] = useState<number | null>(initAccountId);
  const [fDateFrom, setFDateFrom] = useState(
    // Pre-fill current month only when coming from dashboard with explicit params
    (params.get("type") || params.get("category")) ? new Date().toISOString().slice(0, 7) + "-01" : ""
  );
  const [fDateTo, setFDateTo] = useState("");
  const [fAmountMin, setFAmountMin] = useState("");
  const [fAmountMax, setFAmountMax] = useState("");
  const [showFilters, setShowFilters] = useState(false);

  const load = useCallback(async () => {
    setAllTxs(null);
    try {
      const [list, accs] = await Promise.all([
        listTransactions(pending ? { pending_transfers: true } : {}),
        listAccounts(),
      ]);
      setAllTxs(list);
      setAccounts(accs);
    } catch {
      router.replace("/");
    }
  }, [router, pending]);

  useEffect(() => {
    if (!getToken()) { router.replace("/"); return; }
    load();
  }, [router, load]);

  // ── Apply all filters client-side ────────────────────────────────────────
  const filtered = useMemo(() => {
    if (!allTxs) return null;
    return allTxs.filter((tx) => {
      if (search && !tx.merchant?.toLowerCase().includes(search.toLowerCase()) &&
          !tx.category?.toLowerCase().includes(search.toLowerCase())) return false;
      if (fType === "expense" && (tx.is_income || tx.is_transfer)) return false;
      if (fType === "income" && (!tx.is_income || (tx.is_transfer && tx.linked_transaction_id != null))) return false;
      if (fCategory && tx.category !== fCategory) return false;
      if (fAccountId && tx.account_id !== fAccountId) return false;
      if (fDateFrom && tx.date < fDateFrom) return false;
      if (fDateTo && tx.date > fDateTo) return false;
      if (fAmountMin && Math.abs(tx.amount) < Number(fAmountMin)) return false;
      if (fAmountMax && Math.abs(tx.amount) > Number(fAmountMax)) return false;
      return true;
    });
  }, [allTxs, search, fType, fCategory, fAccountId, fDateFrom, fDateTo, fAmountMin, fAmountMax]);

  const activeFilterCount = [
    fType !== "all", fCategory, fAccountId, fDateFrom, fDateTo, fAmountMin, fAmountMax,
  ].filter(Boolean).length;

  function clearFilters() {
    setSearch(""); setFType("all"); setFCategory(""); setFAccountId(null);
    setFDateFrom(""); setFDateTo(""); setFAmountMin(""); setFAmountMax("");
  }

  const backHref = initAccountId ? "/accounts" : (params.get("type") || params.get("category")) ? "/dashboard" : null;

  return (
    <div className="max-w-5xl mx-auto w-full space-y-4 pb-24 md:pb-0">
      {pending ? (
        <>
          <header className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h1 className="text-3xl font-semibold tracking-tight">{t("tx.pending.title")}</h1>
              <p className="text-slate-500 text-sm max-w-2xl mt-1">{t("tx.pending.subtitle")}</p>
            </div>
            <a href="/transactions" className="btn-ghost text-sm">← {t("tx.pending.clearFilter")}</a>
          </header>
          {allTxs === null ? (
            <div className="text-slate-500">{t("tx.loading")}</div>
          ) : (
            <PendingTransferList txs={allTxs} accounts={accounts} onLinked={load} />
          )}
        </>
      ) : (
        <>
          {/* Header */}
          <header className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
            <div>
              <h1 className="text-2xl sm:text-3xl font-semibold tracking-tight">{t("tx.title")}</h1>
              {backHref && (
                <a href={backHref} className="text-xs text-slate-400 hover:text-slate-600">
                  ← {initAccountId ? "Volver a cuentas" : "Volver al resumen"}
                </a>
              )}
            </div>
            <div className="flex items-center gap-2">
              <a href="/upload" className="btn-ghost text-sm">📷 <span className="hidden sm:inline">Subir foto</span></a>
              <button className="btn-primary text-sm" onClick={() => setShowManual(true)}>✏️ {t("tx.add")}</button>
            </div>
          </header>

          {/* ── Filter bar ─────────────────────────────────────────────────── */}
          <div className="space-y-2">
            {/* Search + toggle */}
            <div className="flex gap-2">
              <input
                className="input flex-1"
                placeholder="Buscar por descripción o categoría…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
              <button
                onClick={() => setShowFilters((v) => !v)}
                className={`btn text-sm flex items-center gap-1.5 shrink-0 ${
                  activeFilterCount > 0
                    ? "bg-brand-600 text-white hover:bg-brand-700"
                    : "btn-ghost"
                }`}
              >
                {showFilters ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
                Filtros{activeFilterCount > 0 ? ` (${activeFilterCount})` : ""}
              </button>
            </div>

            {/* Quick type chips */}
            <div className="flex gap-1.5">
              {(["all", "expense", "income"] as const).map((tp) => (
                <button
                  key={tp}
                  onClick={() => setFType(tp)}
                  className={`px-3 py-1 rounded-full text-xs font-medium border transition ${
                    fType === tp
                      ? tp === "expense" ? "bg-rose-500 border-rose-500 text-white"
                        : tp === "income" ? "bg-emerald-500 border-emerald-500 text-white"
                        : "bg-slate-800 border-slate-800 text-white"
                      : "border-slate-200 text-slate-500 hover:border-slate-400"
                  }`}
                >
                  {tp === "all" ? "Todos" : tp === "expense" ? "Gastos" : "Ingresos"}
                </button>
              ))}
              {activeFilterCount > 0 && (
                <button
                  onClick={clearFilters}
                  className="ml-auto px-2.5 py-1 rounded-full text-xs text-slate-400 hover:text-slate-600 border border-slate-200 hover:border-slate-400 transition flex items-center gap-1"
                >
                  <X className="w-3 h-3" /> Limpiar
                </button>
              )}
            </div>

            {/* Expanded filter panel */}
            {showFilters && (
              <div className="card p-4 grid grid-cols-2 sm:grid-cols-3 gap-3 text-sm">
                {/* Category */}
                <label className="block">
                  <span className="text-xs uppercase text-slate-400 font-semibold tracking-wide">Categoría</span>
                  <select className="input mt-1" value={fCategory} onChange={(e) => setFCategory(e.target.value)}>
                    <option value="">Todas</option>
                    {CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
                  </select>
                </label>
                {/* Account */}
                <label className="block">
                  <span className="text-xs uppercase text-slate-400 font-semibold tracking-wide">Cuenta</span>
                  <select className="input mt-1" value={fAccountId ?? ""} onChange={(e) => setFAccountId(e.target.value ? Number(e.target.value) : null)}>
                    <option value="">Todas</option>
                    {accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
                  </select>
                </label>
                {/* Date from */}
                <label className="block">
                  <span className="text-xs uppercase text-slate-400 font-semibold tracking-wide">Desde</span>
                  <input type="date" className="input mt-1" value={fDateFrom} onChange={(e) => setFDateFrom(e.target.value)} />
                </label>
                {/* Date to */}
                <label className="block">
                  <span className="text-xs uppercase text-slate-400 font-semibold tracking-wide">Hasta</span>
                  <input type="date" className="input mt-1" value={fDateTo} onChange={(e) => setFDateTo(e.target.value)} />
                </label>
                {/* Amount min */}
                <label className="block">
                  <span className="text-xs uppercase text-slate-400 font-semibold tracking-wide">Monto mín.</span>
                  <input type="number" min="0" className="input mt-1 font-mono" placeholder="0" value={fAmountMin} onChange={(e) => setFAmountMin(e.target.value)} />
                </label>
                {/* Amount max */}
                <label className="block">
                  <span className="text-xs uppercase text-slate-400 font-semibold tracking-wide">Monto máx.</span>
                  <input type="number" min="0" className="input mt-1 font-mono" placeholder="∞" value={fAmountMax} onChange={(e) => setFAmountMax(e.target.value)} />
                </label>
              </div>
            )}

            {/* Result count */}
            {filtered !== null && (
              <p className="text-xs text-slate-400">
                {filtered.length} movimiento{filtered.length !== 1 ? "s" : ""}
                {activeFilterCount > 0 || search ? " con los filtros aplicados" : ""}
              </p>
            )}
          </div>

          {filtered === null ? (
            <div className="text-slate-500">{t("tx.loading")}</div>
          ) : (
            <TransactionList txs={filtered} accounts={accounts} onRefresh={load} />
          )}
        </>
      )}

      {showManual && (
        <ManualTxModal accounts={accounts} onClose={() => setShowManual(false)} onSaved={load} />
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
