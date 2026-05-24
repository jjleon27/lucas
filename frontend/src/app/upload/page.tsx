"use client";
/**
 * Upload flow — now supports multi-transaction statements.
 * If the backend returns >1 transaction, we show a checklist with editable
 * rows; users can uncheck noise (like "PAGO TARJETA"), tweak anything,
 * then hit "Save N".
 *
 * The currency defaults to the user's preferred currency (set on dashboard).
 * Users can also override the currency for the whole batch right here.
 */
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import UploadZone from "@/components/UploadZone";
import NumericInput from "@/components/NumericInput";
import {
  createTransaction, me, ParsedReceipt, ParsedUpload, uploadImage,
  listAccounts, type Account,
} from "@/lib/api";
import { useT, formatMoney } from "@/lib/i18n";

interface Row extends ParsedReceipt {
  _selected: boolean;
}

const CURRENCIES: { code: string; label: string }[] = [
  { code: "CLP", label: "🇨🇱 CLP" },
  { code: "USD", label: "🇺🇸 USD" },
  { code: "EUR", label: "🇪🇺 EUR" },
  { code: "BRL", label: "🇧🇷 BRL" },
  { code: "MXN", label: "🇲🇽 MXN" },
  { code: "ARS", label: "🇦🇷 ARS" },
  { code: "PEN", label: "🇵🇪 PEN" },
  { code: "COP", label: "🇨🇴 COP" },
  { code: "GBP", label: "🇬🇧 GBP" },
];

const CATEGORIES = [
  "Food", "Groceries", "Transport", "Shopping",
  "Entertainment", "Bills", "Health", "Travel",
  "Subscriptions", "Other",
];

export default function UploadPage() {
  const router = useRouter();
  const { t, locale } = useT();
  const [upload, setUpload] = useState<ParsedUpload | null>(null);
  const [rows, setRows] = useState<Row[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");
  const [batchCurrency, setBatchCurrency] = useState<string>(
    locale === "es" ? "CLP" : locale === "pt" ? "BRL" : "USD",
  );
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [pickedAccount, setPickedAccount] = useState<number | null>(null);

  // Load the user's preferred currency + accounts once.
  useEffect(() => {
    me()
      .then((u) => {
        const c = (u.settings as any)?.currency;
        if (c) setBatchCurrency(c);
      })
      .catch(() => {});
    listAccounts().then(setAccounts).catch(() => {});
  }, []);

  async function handleFile(f: File) {
    setErr("");
    setLoading(true);
    try {
      const res = await uploadImage(f);
      setUpload(res);
      // The backend auto-suggests an account based on the bank/card type it
      // sees in the header. If the user only has one account, fall back to it.
      setPickedAccount(
        res.suggested_account_id ?? (accounts.length === 1 ? accounts[0].id : null),
      );
      // Force every row to use the user's preferred currency by default — the
      // OCR's guess is usually wrong (e.g. "$" interpreted as USD when it's CLP).
      // Default-unselect rows the backend flagged as duplicates (user can opt-in).
      setRows(
        res.transactions.map((tx) => ({
          ...tx,
          currency: batchCurrency,
          _selected: tx.dupe_of == null,
        })),
      );
    } catch (e: any) {
      setErr(e.message || t("upload.uploadFailed"));
    } finally {
      setLoading(false);
    }
  }

  function setBatchCurrencyAll(code: string) {
    setBatchCurrency(code);
    setRows((prev) => prev.map((r) => ({ ...r, currency: code })));
  }

  function patch(i: number, fields: Partial<Row>) {
    setRows((prev) => {
      const copy = [...prev];
      copy[i] = { ...copy[i], ...fields };
      return copy;
    });
  }

  async function confirm() {
    if (!upload) return;
    const picked = rows.filter((r) => r._selected);
    if (picked.length === 0) return;
    setSaving(true);
    try {
      // Persist sequentially so order is deterministic; speed isn't the concern here.
      for (const r of picked) {
        await createTransaction({
          amount: r.amount,
          currency: r.currency || upload.currency || "USD",
          category: r.category || "Other",
          date: r.date,
          merchant: r.merchant || "",
          notes: r.description && r.description !== r.merchant ? r.description : "",
          is_income: r.is_income,
          // Flag CC payments so the server auto-links them as internal transfers.
          is_transfer: !!r.is_cc_payment,
          account_id: pickedAccount,
          image_url: upload.image_url,
          items: r.items,
        });
      }
      router.push("/transactions");
    } catch (e: any) {
      setErr(e.message || t("upload.saveFailed"));
    } finally {
      setSaving(false);
    }
  }

  const isMulti = (upload?.transactions.length ?? 0) > 1;
  const selectedCount = rows.filter((r) => r._selected).length;
  const currency = batchCurrency;

  return (
    <div className="max-w-4xl mx-auto space-y-6 pb-24 md:pb-0">
      <div>
        <h1 className="text-3xl font-semibold tracking-tight">{t("upload.title")}</h1>
        <p className="text-slate-500 mt-1">{t("upload.subtitle")}</p>
      </div>

      {!upload && <UploadZone onFile={handleFile} loading={loading} />}
      {err && <p className="text-sm text-rose-600">{err}</p>}

      {upload && (
        <div className="card space-y-3">
          <div className="flex items-center gap-2 text-sm">
            <label className="text-slate-500 w-24">
              {t("accounts.pickAccount") || "Cuenta"}:
            </label>
            <select
              className="input flex-1"
              value={pickedAccount ?? ""}
              onChange={(e) =>
                setPickedAccount(e.target.value ? Number(e.target.value) : null)
              }
            >
              <option value="">— elegí una cuenta —</option>
              {accounts.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name} {a.bank ? `· ${a.bank}` : ""} ({a.type})
                </option>
              ))}
            </select>
            {upload.suggested_account_id != null && (
              <span className="text-xs text-brand-600 whitespace-nowrap">
                ✨ sugerido
              </span>
            )}
          </div>
          {accounts.length === 0 && (
            <p className="text-xs text-amber-600">
              Aún no tienes cuentas. <a href="/accounts" className="underline">Crea una</a> para
              llevar el saldo correctamente.
            </p>
          )}
          <div className="flex items-center gap-2 text-sm">
            <label className="text-slate-500 w-24">{t("dashboard.currency")}:</label>
            <select
              className="input w-32"
              value={batchCurrency}
              onChange={(e) => setBatchCurrencyAll(e.target.value)}
            >
              {CURRENCIES.map((c) => (
                <option key={c.code} value={c.code}>{c.label}</option>
              ))}
            </select>
          </div>
          {rows.some((r) => r.dupe_of != null) && (
            <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-2 py-1.5">
              ⚠️ Detecté {rows.filter((r) => r.dupe_of != null).length} movimiento(s) que
              ya están en tu cuenta (los desmarqué). Si quieres guardarlos igual, márcalos de nuevo.
            </p>
          )}
          {rows.some((r) => r.is_cc_payment) && (
            <p className="text-xs text-sky-700 bg-sky-50 border border-sky-200 rounded-lg px-2 py-1.5">
              💳 Hay pagos de tarjeta. No los cuento como ingreso — los registro como transferencia
              interna para que no inflen tu saldo.
            </p>
          )}
        </div>
      )}

      {/* Single-transaction confirmation */}
      {upload && !isMulti && rows[0] && (
        <div className="card space-y-4">
          <h3 className="font-semibold">{t("upload.confirmTitle")}</h3>
          {rows[0].dupe_of != null && (
            <div className="flex items-start gap-2 bg-amber-50 border border-amber-200 rounded-xl px-3 py-2.5 text-sm text-amber-800">
              <span className="text-base mt-0.5">⚠️</span>
              <div>
                <strong>{t("tx.duplicateWarning")}</strong>
                <p className="text-xs mt-0.5 text-amber-700">
                  {locale === "en"
                    ? "This transaction already exists in your account. Save anyway?"
                    : "Esta transacción ya existe en tu cuenta. ¿Guardar de todas formas?"}
                </p>
              </div>
            </div>
          )}
          <div className="flex gap-2">
            {rows[0].dupe_of != null && (
              <span className="text-[10px] uppercase tracking-wide bg-amber-200 text-amber-900 px-2 py-0.5 rounded">
                ya existe
              </span>
            )}
            {rows[0].is_cc_payment && (
              <span className="text-[10px] uppercase tracking-wide bg-sky-100 text-sky-800 px-2 py-0.5 rounded">
                pago tarjeta
              </span>
            )}
            {rows[0].cuotas_total != null && rows[0].cuotas_total > 1 && (
              <span className="text-[10px] uppercase tracking-wide bg-purple-100 text-purple-800 px-2 py-0.5 rounded">
                cuota {rows[0].cuota_actual}/{rows[0].cuotas_total}
              </span>
            )}
          </div>
          <EditRow
            row={rows[0]}
            currency={currency}
            onChange={(f) => patch(0, f)}
            showSelectable={false}
          />
          <div className="flex justify-end gap-2">
            <button className="btn-ghost" onClick={() => { setUpload(null); setRows([]); }}>
              {t("upload.discard")}
            </button>
            <button className="btn-primary" onClick={confirm} disabled={saving}>
              {saving ? t("upload.saving") : t("upload.save")}
            </button>
          </div>
        </div>
      )}

      {/* Multi-transaction list */}
      {upload && isMulti && (
        <div className="space-y-3">
          <div className="card">
            <h3 className="font-semibold">
              {t("upload.multiTitle", { count: upload.transactions.length })}
            </h3>
            <p className="text-sm text-slate-500 mt-1">{t("upload.multiSubtitle")}</p>
          </div>

          {rows.map((r, i) => (
            <div
              key={i}
              className={`card transition ${r._selected ? "" : "opacity-50"} ${
                r.dupe_of != null ? "border-2 border-amber-200" : ""
              }`}
            >
              <div className="flex gap-2 mb-2">
                {r.dupe_of != null && (
                  <span className="text-[10px] uppercase tracking-wide bg-amber-200 text-amber-900 px-2 py-0.5 rounded">
                    ya existe
                  </span>
                )}
                {r.is_cc_payment && (
                  <span className="text-[10px] uppercase tracking-wide bg-sky-100 text-sky-800 px-2 py-0.5 rounded">
                    pago tarjeta
                  </span>
                )}
                {r.cuotas_total != null && r.cuotas_total > 1 && (
                  <span className="text-[10px] uppercase tracking-wide bg-purple-100 text-purple-800 px-2 py-0.5 rounded">
                    cuota {r.cuota_actual}/{r.cuotas_total}
                  </span>
                )}
              </div>
              <EditRow
                row={r}
                currency={currency}
                onChange={(f) => patch(i, f)}
                showSelectable
              />
            </div>
          ))}

          <div className="flex justify-end gap-2 sticky bottom-4">
            <button
              className="btn-ghost"
              onClick={() => { setUpload(null); setRows([]); }}
            >
              {t("upload.discard")}
            </button>
            <button
              className="btn-primary"
              onClick={confirm}
              disabled={saving || selectedCount === 0}
            >
              {saving ? t("upload.saving") : t("upload.saveAll", { count: selectedCount })}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------- Inline row editor ----------
function EditRow({
  row, currency, onChange, showSelectable,
}: {
  row: Row;
  currency: string;
  onChange: (f: Partial<Row>) => void;
  showSelectable: boolean;
}) {
  const { t } = useT();
  return (
    <div className="space-y-3">
      <div className="flex items-start gap-3">
        {showSelectable && (
          <input
            type="checkbox"
            checked={row._selected}
            onChange={(e) => onChange({ _selected: e.target.checked })}
            className="mt-2 w-4 h-4 accent-brand-600"
          />
        )}
        <div className="flex-1 grid grid-cols-2 md:grid-cols-4 gap-3">
          <label className="block">
            <span className="text-xs uppercase text-slate-500">{t("upload.merchant")}</span>
            <input
              className="input mt-1"
              value={row.merchant}
              onChange={(e) => onChange({ merchant: e.target.value })}
            />
          </label>
          <label className="block">
            <span className="text-xs uppercase text-slate-500">{t("upload.amount")}</span>
            <NumericInput
              className="input mt-1 font-mono"
              value={row.amount}
              onChange={(v) => onChange({ amount: v })}
              allowDecimals
              placeholder="0"
            />
          </label>
          <label className="block">
            <span className="text-xs uppercase text-slate-500">{t("upload.date")}</span>
            <input
              className="input mt-1"
              type="date"
              value={row.date}
              onChange={(e) => onChange({ date: e.target.value })}
            />
          </label>
          <label className="block">
            <span className="text-xs uppercase text-slate-500">{t("upload.category")}</span>
            <select
              className="input mt-1"
              value={row.category}
              onChange={(e) => onChange({ category: e.target.value })}
            >
              {CATEGORIES.map((c) => <option key={c}>{c}</option>)}
            </select>
          </label>
          <label className="block md:col-span-2">
            <span className="text-xs uppercase text-slate-500">{t("upload.type")}</span>
            <div className="mt-1 flex rounded-xl bg-slate-100 p-1 text-sm">
              <button
                type="button"
                className={`flex-1 py-1.5 rounded-lg ${!row.is_income ? "bg-white shadow-soft" : "text-slate-500"}`}
                onClick={() => onChange({ is_income: false })}
              >
                {t("upload.expense")}
              </button>
              <button
                type="button"
                className={`flex-1 py-1.5 rounded-lg ${row.is_income ? "bg-white shadow-soft text-brand-700" : "text-slate-500"}`}
                onClick={() => onChange({ is_income: true })}
              >
                {t("upload.income")}
              </button>
            </div>
          </label>
        </div>
      </div>

      {row.items.length > 0 && (
        <div>
          <span className="text-xs uppercase text-slate-500">{t("upload.lineItems")}</span>
          <ul className="mt-1 text-sm divide-y">
            {row.items.map((it, i) => (
              <li key={i} className="flex justify-between py-1.5">
                <span>{it.name} {it.quantity > 1 ? `×${it.quantity}` : ""}</span>
                <span className="font-mono">{formatMoney(it.price, currency)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
