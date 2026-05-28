"use client";
/**
 * Accounts page — manage cards, debit, savings, wallets.
 *
 * Each card shows live balance (debit/savings) or used+available (credit).
 * The user sets a "known balance" anchor; the app computes the live value
 * by adding/subtracting transactions since that date.
 */
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { CreditCard, Wallet, PiggyBank, Banknote, Pencil, Trash2 } from "lucide-react";
import {
  Account, AccountInput, AccountType,
  createAccount, deleteAccount, getToken, listAccounts, updateAccount, uploadCardImage,
} from "@/lib/api";
import { useT, formatMoney } from "@/lib/i18n";
import NumericInput from "@/components/NumericInput";
import CardImagePicker, {
  resolveCardBackground, resolveCardTextColor, CARD_PRESETS,
} from "@/components/CardImagePicker";

const TYPE_OPTIONS: AccountType[] = ["debit", "credit", "savings", "wallet", "cash"];
const COLOR_PALETTE = [
  "#10b981", "#6366f1", "#f97316", "#ef4444",
  "#a855f7", "#06b6d4", "#eab308", "#ec4899",
  "#0ea5e9", "#84cc16",
];
const BANK_OPTIONS = [
  "", "Santander", "Banco de Chile", "BancoEstado", "BCI", "Itaú",
  "Scotiabank", "Falabella / CMR", "Ripley", "Banco Security",
  "Banco Internacional", "Mercado Pago", "Otro",
];

const TYPE_ICON: Record<AccountType, any> = {
  debit: Wallet,
  credit: CreditCard,
  savings: PiggyBank,
  wallet: Wallet,
  cash: Banknote,
};

export default function AccountsPage() {
  const router = useRouter();
  const { t, locale } = useT();
  const [accounts, setAccounts] = useState<Account[] | null>(null);
  const [editing, setEditing] = useState<AccountInput & { id?: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    if (!getToken()) {
      router.replace("/");
      return;
    }
    listAccounts().then(setAccounts).catch(() => router.replace("/"));
  }, [router]);

  function openNew() {
    setEditing({
      name: "",
      bank: "",
      type: "debit",
      currency: locale === "es" ? "CLP" : locale === "pt" ? "BRL" : "USD",
      color: COLOR_PALETTE[Math.floor(Math.random() * COLOR_PALETTE.length)],
      icon: "card",
      card_image_url: "",
      credit_limit: 0,
      anchor_date: new Date().toISOString().slice(0, 10),
      anchor_balance: 0,
    });
  }

  function openEdit(a: Account) {
    setEditing({
      id: a.id,
      name: a.name,
      bank: a.bank,
      type: a.type,
      currency: a.currency,
      color: a.color,
      icon: a.icon,
      card_image_url: a.card_image_url || "",
      credit_limit: a.credit_limit,
      anchor_date: a.anchor_date || new Date().toISOString().slice(0, 10),
      // For credit: store as "available" (limit - used) so the form field is intuitive
      anchor_balance:
        a.type === "credit"
          ? (a.credit_limit ?? 0) - (a.current_used ?? a.anchor_balance ?? 0)
          : a.current_balance ?? a.anchor_balance ?? 0,
    });
  }

  async function save() {
    if (!editing) return;
    if (!editing.name.trim()) {
      setErr(t("accounts.name") + " *");
      return;
    }
    setBusy(true);
    setErr("");
    try {
      // Credit cards: form stores "available", backend expects "used" (owed)
      const payload =
        editing.type === "credit"
          ? { ...editing, anchor_balance: (editing.credit_limit ?? 0) - (editing.anchor_balance ?? 0) }
          : editing;
      if (editing.id) {
        const upd = await updateAccount(editing.id, payload);
        setAccounts((prev) =>
          (prev ?? []).map((a) => (a.id === upd.id ? upd : a)),
        );
      } else {
        const created = await createAccount(payload);
        setAccounts((prev) => [...(prev ?? []), created]);
      }
      setEditing(null);
    } catch (e: any) {
      setErr(e.message || "Error");
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: number) {
    if (!confirm(t("accounts.confirmDelete"))) return;
    await deleteAccount(id);
    setAccounts((prev) => (prev ?? []).filter((a) => a.id !== id));
  }

  if (!accounts) return <div className="p-8 text-slate-500">{t("tx.loading")}</div>;

  // ── Net worth calculation ──────────────────────────────────────────────────
  // Group by currency so multi-currency users see each separately.
  const netByCurrency: Record<string, { assets: number; debts: number }> = {};
  for (const a of accounts) {
    if (!netByCurrency[a.currency]) netByCurrency[a.currency] = { assets: 0, debts: 0 };
    if (a.type === "credit") {
      netByCurrency[a.currency].debts += a.current_used ?? 0;
    } else {
      netByCurrency[a.currency].assets += a.current_balance ?? 0;
    }
  }
  const netEntries = Object.entries(netByCurrency);

  return (
    <div className="max-w-5xl mx-auto space-y-6 pb-24 md:pb-0">
      <header className="flex flex-col md:flex-row md:items-end justify-between gap-3">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight">{t("accounts.title")}</h1>
          <p className="text-slate-500 mt-1">{t("accounts.subtitle")}</p>
        </div>
        <button className="btn-primary" onClick={openNew}>{t("accounts.add")}</button>
      </header>

      {/* ── Posición financiera ────────────────────────────────────────────── */}
      {accounts.length > 0 && (
        <div className="card space-y-4">
          <h2 className="text-base font-semibold text-slate-700">Posición financiera</h2>
          {netEntries.map(([currency, { assets, debts }]) => {
            const net = assets - debts;
            const fmt = (v: number) => formatMoney(v, currency);
            return (
              <div key={currency}>
                {netEntries.length > 1 && (
                  <p className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-2">{currency}</p>
                )}
                <div className="space-y-2">
                  <div className="flex items-center justify-between py-2 px-1">
                    <div>
                      <span className="text-sm font-medium text-slate-700">Activos</span>
                      <span className="text-xs text-slate-400 ml-2">débito, ahorro, efectivo</span>
                    </div>
                    <span className="text-base font-semibold font-mono text-emerald-600">{fmt(assets)}</span>
                  </div>
                  <div className="flex items-center justify-between py-2 px-1">
                    <div>
                      <span className="text-sm font-medium text-slate-700">Deudas</span>
                      <span className="text-xs text-slate-400 ml-2">tarjetas de crédito</span>
                    </div>
                    <span className="text-base font-semibold font-mono text-rose-500">−{fmt(debts)}</span>
                  </div>
                  <div className={`flex items-center justify-between py-3 px-3 rounded-2xl ${net >= 0 ? "bg-brand-50" : "bg-orange-50"}`}>
                    <span className={`text-sm font-semibold ${net >= 0 ? "text-brand-700" : "text-orange-700"}`}>Patrimonio neto</span>
                    <span className={`text-xl font-bold font-mono ${net >= 0 ? "text-brand-700" : "text-orange-600"}`}>{fmt(net)}</span>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {accounts.length === 0 ? (
        <div className="card text-center text-slate-500">{t("accounts.empty")}</div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {accounts.map((a) => (
            <AccountCard
              key={a.id}
              account={a}
              onEdit={openEdit}
              onDelete={remove}
              onRefresh={() => listAccounts().then(setAccounts)}
            />
          ))}
        </div>
      )}

      {editing && (
        <AccountFormModal
          value={editing}
          onChange={setEditing}
          onClose={() => setEditing(null)}
          onSave={save}
          busy={busy}
          err={err}
        />
      )}
    </div>
  );
}

function AccountCard({
  account, onEdit, onDelete, onRefresh,
}: {
  account: Account;
  onEdit: (a: Account) => void;
  onDelete: (id: number) => void;
  onRefresh: () => void;
}) {
  const { t } = useT();
  const router = useRouter();
  const Icon = TYPE_ICON[account.type] ?? CreditCard;
  const fmt = (v: number) => formatMoney(v, account.currency);

  const bgStyle = resolveCardBackground(account.card_image_url || "", account.color);
  const textColor = resolveCardTextColor(account.card_image_url || "");

  return (
    <div
      className="rounded-2xl shadow-soft p-5 relative overflow-hidden"
      style={{ ...bgStyle, color: textColor }}
    >
      <div className="absolute top-3 right-3 flex gap-1">
        <button
          onClick={async () => {
            const input = prompt(
              account.type === "credit"
                ? `¿Cuánto dices que debes hoy en ${account.name}? (lo que ves en el banco)`
                : `¿Cuánto dices que tienes hoy en ${account.name}? (lo que ves en el banco)`,
              String(account.type === "credit" ? account.current_used : account.current_balance),
            );
            if (input == null) return;
            const n = Number(input.replace(/[^\d.-]/g, ""));
            if (!isFinite(n)) { alert("Número inválido"); return; }
            try {
              const { reconcileAccount } = await import("@/lib/api");
              const r = await reconcileAccount(account.id, n);
              alert(`Saldo ajustado. Diferencia: ${r.drift.toLocaleString("es-CL")}`);
              onRefresh();
            } catch (e: any) {
              alert(e?.message || "No se pudo ajustar");
            }
          }}
          title="Ajustar saldo al del banco"
          className="p-1.5 rounded-lg bg-white/20 hover:bg-white/30 text-[10px] font-semibold"
        >
          ⚖️
        </button>
        <button onClick={() => onEdit(account)} className="p-1.5 rounded-lg bg-white/20 hover:bg-white/30">
          <Pencil className="w-3.5 h-3.5" />
        </button>
        <button onClick={() => onDelete(account.id)} className="p-1.5 rounded-lg bg-white/20 hover:bg-white/30">
          <Trash2 className="w-3.5 h-3.5" />
        </button>
      </div>

      <div className="flex items-start gap-3">
        <div className="p-2 rounded-xl bg-white/20"><Icon className="w-5 h-5" /></div>
        <div>
          <div className="text-xs uppercase opacity-80">{account.bank || t(`accounts.type.${account.type}` as any)}</div>
          <div className="text-lg font-semibold">{account.name}</div>
        </div>
      </div>

      <button
        className="mt-6 space-y-1 w-full text-left cursor-pointer"
        onClick={() => router.push(`/transactions?account_id=${account.id}`)}
        title="Ver movimientos"
      >
        {account.type === "credit" ? (
          <>
            <div className="text-xs uppercase opacity-80">{t("accounts.used")}</div>
            <div className="text-3xl font-mono font-semibold">{fmt(account.current_used)}</div>
            <div className="text-xs opacity-80 mt-1">
              {t("accounts.available")}: <span className="font-mono">{fmt(account.available_credit)}</span>
              {" / "}
              {t("accounts.limit")}: <span className="font-mono">{fmt(account.credit_limit)}</span>
            </div>
            <div className="mt-3 w-full h-1.5 bg-white/20 rounded-full overflow-hidden">
              <div
                className="h-full bg-white"
                style={{
                  width: `${Math.min(100, account.credit_limit > 0 ? (account.current_used / account.credit_limit) * 100 : 0)}%`,
                }}
              />
            </div>
          </>
        ) : (
          <>
            <div className="text-xs uppercase opacity-80">{t("accounts.balance")}</div>
            <div className="text-3xl font-mono font-semibold">{fmt(account.current_balance)}</div>
          </>
        )}
        <div className="text-xs opacity-60 mt-2">Toca para ver movimientos →</div>
      </button>
    </div>
  );
}

function AccountFormModal({
  value, onChange, onClose, onSave, busy, err,
}: {
  value: AccountInput & { id?: number };
  onChange: (v: AccountInput & { id?: number }) => void;
  onClose: () => void;
  onSave: () => void;
  busy: boolean;
  err: string;
}) {
  const { t, locale } = useT();
  const isCredit = value.type === "credit";
  // For credit cards: anchor_balance in state = "available". Toggle lets user enter "used" instead.
  const [showUsed, setShowUsed] = useState(false);

  return (
    <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center p-4" onClick={onClose}>
      <div className="card max-w-lg w-full space-y-4 overflow-y-auto max-h-[90dvh]" onClick={(e) => e.stopPropagation()}>
        <h3 className="text-lg font-semibold">{value.id ? t("accounts.name") : t("accounts.add")}</h3>

        <label className="block">
          <span className="text-xs uppercase text-slate-500">{t("accounts.name")}</span>
          <input
            className="input mt-1"
            placeholder={t("accounts.namePlaceholder")}
            value={value.name}
            onChange={(e) => onChange({ ...value, name: e.target.value })}
          />
        </label>

        <div className="grid grid-cols-2 gap-3">
          <label className="block">
            <span className="text-xs uppercase text-slate-500">{t("accounts.bank")}</span>
            <select
              className="input mt-1"
              value={value.bank}
              onChange={(e) => onChange({ ...value, bank: e.target.value })}
            >
              {BANK_OPTIONS.map((b) => <option key={b} value={b}>{b || "—"}</option>)}
            </select>
          </label>
          <label className="block">
            <span className="text-xs uppercase text-slate-500">{t("accounts.type")}</span>
            <select
              className="input mt-1"
              value={value.type}
              onChange={(e) => onChange({ ...value, type: e.target.value as AccountType })}
            >
              {TYPE_OPTIONS.map((tp) => (
                <option key={tp} value={tp}>{t(`accounts.type.${tp}` as any)}</option>
              ))}
            </select>
          </label>
        </div>

        {isCredit && (
          <label className="block">
            <span className="text-xs uppercase text-slate-500">{t("accounts.creditLimit")}</span>
            <NumericInput
              className="input mt-1 font-mono"
              value={value.credit_limit ?? 0}
              onChange={(v) => onChange({ ...value, credit_limit: v })}
              placeholder="0"
            />
          </label>
        )}

        <div className="grid grid-cols-2 gap-3">
          <label className="block">
            <span className="text-xs uppercase text-slate-500">{t("accounts.anchorDate")}</span>
            <input
              type="date"
              lang={locale}
              className="input mt-1"
              value={value.anchor_date || ""}
              onChange={(e) => onChange({ ...value, anchor_date: e.target.value })}
            />
          </label>
          <label className="block">
            {isCredit ? (
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs uppercase text-slate-500">Saldo actual</span>
                <div className="flex rounded-lg overflow-hidden border border-slate-200 text-xs">
                  <button
                    type="button"
                    onClick={() => setShowUsed(false)}
                    className={`px-2 py-0.5 transition ${!showUsed ? "bg-indigo-600 text-white" : "text-slate-500 hover:bg-slate-50"}`}
                  >Disponible</button>
                  <button
                    type="button"
                    onClick={() => setShowUsed(true)}
                    className={`px-2 py-0.5 transition ${showUsed ? "bg-indigo-600 text-white" : "text-slate-500 hover:bg-slate-50"}`}
                  >Usado</button>
                </div>
              </div>
            ) : (
              <span className="text-xs uppercase text-slate-500">{t("accounts.anchorBalance")}</span>
            )}
            <NumericInput
              className="input mt-1 font-mono"
              value={
                isCredit && showUsed
                  ? (value.credit_limit ?? 0) - (value.anchor_balance ?? 0)
                  : value.anchor_balance ?? 0
              }
              onChange={(v) => onChange({
                ...value,
                anchor_balance: isCredit && showUsed
                  ? (value.credit_limit ?? 0) - v
                  : v,
              })}
              placeholder="0"
            />
          </label>
        </div>
        <p className="text-xs text-slate-500 -mt-2">
          {isCredit
            ? showUsed
              ? "¿Cuánto debés hoy en esta tarjeta? (lo que ves en el banco)"
              : "¿Cuánto tenés disponible para gastar hoy? (cupo − lo que debés)"
            : t("accounts.anchorHelp")}
        </p>

        <div>
          <span className="text-xs uppercase text-slate-500">{t("accounts.color")}</span>
          <div className="flex flex-wrap gap-2 mt-2">
            {COLOR_PALETTE.map((c) => (
              <button
                key={c}
                onClick={() => onChange({ ...value, color: c })}
                className={`w-7 h-7 rounded-full border-2 ${
                  value.color === c ? "border-slate-900" : "border-transparent"
                }`}
                style={{ backgroundColor: c }}
              />
            ))}
          </div>
        </div>

        <div>
          <span className="text-xs uppercase text-slate-500">Imagen de tarjeta</span>
          <div className="mt-2">
            <CardImagePicker
              value={value.card_image_url ?? ""}
              accountId={value.id}
              onSelect={(url) => onChange({ ...value, card_image_url: url })}
              onUpload={value.id
                ? async (file) => {
                    const acc = await uploadCardImage(value.id!, file);
                    return acc.card_image_url;
                  }
                : undefined
              }
            />
          </div>
        </div>

        {err && <p className="text-sm text-rose-600">{err}</p>}

        <div className="flex justify-end gap-2 pt-2">
          <button className="btn-ghost" onClick={onClose} disabled={busy}>{t("accounts.cancel")}</button>
          <button className="btn-primary" onClick={onSave} disabled={busy}>
            {busy ? "…" : t("accounts.save")}
          </button>
        </div>
      </div>
    </div>
  );
}

// Tiny helper to darken a hex color for the gradient.
function shade(hex: string, percent: number): string {
  const m = hex.replace("#", "").match(/.{2}/g);
  if (!m) return hex;
  const [r, g, b] = m.map((h) => parseInt(h, 16));
  const f = (n: number) => Math.max(0, Math.min(255, Math.round(n + (n * percent) / 100)));
  return `#${[f(r), f(g), f(b)].map((n) => n.toString(16).padStart(2, "0")).join("")}`;
}
