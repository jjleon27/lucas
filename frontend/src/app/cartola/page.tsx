"use client";

/**
 * /cartola — import a monthly/annual bank statement PDF.
 *
 * Steps:
 *  1. Drop a PDF. We POST to /cartola/upload and get a CartolaReport with
 *     each movement tagged as new or duplicate-of-existing.
 *  2. User reviews — rows with a dupe_of are unchecked by default (they
 *     already exist in the app). User can force-save by re-checking.
 *  3. User confirms the destination account (auto-suggested from the PDF
 *     header). Option: "ajustar saldo al de la cartola" → re-anchors the
 *     account to the statement's closing balance.
 *  4. Submit → /cartola/commit. Redirect to /transactions.
 */
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { UploadCloud, Loader2, ArrowRight, CheckCircle2, AlertCircle } from "lucide-react";
import {
  uploadCartola, commitCartola, listAccounts, getToken,
  type CartolaReport, type Account, type ParsedReceipt,
} from "@/lib/api";
import { formatMoney } from "@/lib/i18n";

type Row = ParsedReceipt & { _include: boolean };

export default function CartolaPage() {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [report, setReport] = useState<CartolaReport | null>(null);
  const [rows, setRows] = useState<Row[]>([]);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [pickedAccount, setPickedAccount] = useState<number | null>(null);
  const [reanchor, setReanchor] = useState(true);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!getToken()) {
      router.replace("/");
      return;
    }
    listAccounts().then(setAccounts).catch(() => {});
  }, [router]);

  async function onUpload() {
    if (!file) return;
    setUploading(true);
    setErr(null);
    try {
      const r = await uploadCartola(file);
      setReport(r);
      setRows(
        r.transactions.map((t) => ({
          ...t,
          _include: t.dupe_of == null, // skip dupes by default
        })),
      );
      setPickedAccount(r.suggested_account_id ?? (accounts[0]?.id ?? null));
    } catch (e: any) {
      setErr(e?.message || "No se pudo procesar el PDF");
    } finally {
      setUploading(false);
    }
  }

  async function onCommit() {
    if (!pickedAccount || !report) return;
    setSaving(true);
    setErr(null);
    try {
      const picked: ParsedReceipt[] = rows
        .filter((r) => r._include)
        .map(({ _include, ...rest }) => ({
          ...rest,
          dupe_of: null, // force-save if user re-checked a dupe
        }));
      const res = await commitCartola({
        account_id: pickedAccount,
        transactions: picked,
        reconcile_to_closing_balance: reanchor && report.closing_balance != null,
        closing_balance: report.closing_balance,
      });
      alert(
        `Listo. Guardé ${res.saved_count} movimiento(s).` +
          (res.drift != null && Math.abs(res.drift) > 0.5
            ? `\nDiferencia al ajustar saldo: ${res.drift.toLocaleString("es-CL")}`
            : ""),
      );
      router.push("/transactions");
    } catch (e: any) {
      setErr(e?.message || "No se pudo guardar");
    } finally {
      setSaving(false);
    }
  }

  const currency = report?.currency || "CLP";
  const fmt = (n: number | null | undefined) =>
    n == null ? "—" : formatMoney(n, currency);

  return (
    <div className="max-w-5xl mx-auto space-y-6 pb-24 md:pb-0">
      <header>
        <h1 className="text-3xl font-semibold tracking-tight">Importar cartola</h1>
        <p className="text-slate-500">
          Sube tu cartola mensual o anual en PDF. Lucas la lee, detecta los
          movimientos que ya ingresaste (no los duplica) y te muestra si tu
          saldo en la app coincide con el del banco.
        </p>
      </header>

      {!report && (
        <div className="bg-white rounded-2xl p-6 shadow-soft">
          <label className="block border-2 border-dashed border-slate-300 rounded-xl p-8 text-center cursor-pointer hover:border-brand-400">
            <input
              type="file"
              accept="application/pdf,.pdf"
              className="hidden"
              onChange={(e) => setFile(e.target.files?.[0] || null)}
            />
            <UploadCloud className="w-8 h-8 text-slate-400 mx-auto mb-2" />
            <div className="text-slate-700 font-medium">
              {file ? file.name : "Arrastra tu cartola PDF o toca para subir"}
            </div>
            <div className="text-xs text-slate-500 mt-1">
              Soporta PDFs de Santander, BCI, Falabella, BancoEstado, Itaú…
            </div>
          </label>

          {err && (
            <div className="mt-4 flex items-center gap-2 text-sm text-red-600">
              <AlertCircle className="w-4 h-4" /> {err}
            </div>
          )}

          <button
            disabled={!file || uploading}
            onClick={onUpload}
            className="mt-4 btn-primary inline-flex items-center gap-2 disabled:opacity-50"
          >
            {uploading ? <Loader2 className="w-4 h-4 animate-spin" /> : <ArrowRight className="w-4 h-4" />}
            {uploading ? "Leyendo PDF…" : "Procesar cartola"}
          </button>
        </div>
      )}

      {report && (
        <>
          {/* Summary card */}
          <div className="bg-white rounded-2xl p-5 shadow-soft grid grid-cols-2 md:grid-cols-4 gap-4">
            <Stat label="Banco" value={report.bank || "—"} />
            <Stat label="Tipo" value={report.account_type || "—"} />
            <Stat label="Período" value={
              report.period_from && report.period_to
                ? `${report.period_from} → ${report.period_to}`
                : "—"
            } />
            <Stat label="Saldo final" value={fmt(report.closing_balance)} />
            <Stat label="Movimientos" value={String(report.transactions.length)} />
            <Stat label="Nuevos" value={String(report.new_count)} highlight />
            <Stat label="Ya en la app" value={String(report.duplicate_count)} />
            {report.drift != null && (
              <Stat
                label="Diferencia vs app"
                value={fmt(report.drift)}
                tone={Math.abs(report.drift) < 1 ? "good" : "warn"}
              />
            )}
          </div>

          {/* Account picker + reanchor */}
          <div className="bg-white rounded-2xl p-5 shadow-soft space-y-3">
            <div className="flex items-center gap-3">
              <label className="text-sm text-slate-600 w-40">Cuenta destino</label>
              <select
                value={pickedAccount ?? ""}
                onChange={(e) =>
                  setPickedAccount(e.target.value ? Number(e.target.value) : null)
                }
                className="flex-1 border border-slate-200 rounded-lg px-2 py-2 text-sm"
              >
                <option value="">— elegí una cuenta —</option>
                {accounts.map((a) => (
                  <option key={a.id} value={a.id}>{a.name}</option>
                ))}
              </select>
            </div>
            {report.closing_balance != null && (
              <label className="flex items-center gap-2 text-sm text-slate-700">
                <input
                  type="checkbox"
                  checked={reanchor}
                  onChange={(e) => setReanchor(e.target.checked)}
                />
                Ajustar el saldo de esta cuenta al saldo final de la cartola (
                {fmt(report.closing_balance)}).
              </label>
            )}
          </div>

          {/* Transaction review */}
          <div className="bg-white rounded-2xl shadow-soft overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 text-slate-600 text-xs uppercase">
                <tr>
                  <th className="p-3 text-left">✓</th>
                  <th className="p-3 text-left">Fecha</th>
                  <th className="p-3 text-left">Detalle</th>
                  <th className="p-3 text-left">Categoría</th>
                  <th className="p-3 text-right">Monto</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr
                    key={i}
                    className={`border-t border-slate-100 ${
                      r.dupe_of != null ? "bg-amber-50" : ""
                    }`}
                  >
                    <td className="p-3">
                      <input
                        type="checkbox"
                        checked={r._include}
                        onChange={(e) => {
                          const next = [...rows];
                          next[i] = { ...r, _include: e.target.checked };
                          setRows(next);
                        }}
                      />
                    </td>
                    <td className="p-3">{r.date}</td>
                    <td className="p-3">
                      <div className="font-medium">{r.merchant}</div>
                      {r.description && r.description !== r.merchant && (
                        <div className="text-xs text-slate-500">{r.description}</div>
                      )}
                      {r.dupe_of != null && (
                        <span className="inline-block mt-1 text-[10px] uppercase tracking-wide bg-amber-200 text-amber-800 px-1.5 py-0.5 rounded">
                          duplicado
                        </span>
                      )}
                      {r.is_cc_payment && (
                        <span className="inline-block mt-1 ml-1 text-[10px] uppercase tracking-wide bg-sky-100 text-sky-800 px-1.5 py-0.5 rounded">
                          pago tarjeta
                        </span>
                      )}
                    </td>
                    <td className="p-3 text-slate-600">{r.category}</td>
                    <td className={`p-3 text-right font-medium ${
                      r.is_income ? "text-emerald-600" : "text-slate-800"
                    }`}>
                      {r.is_income ? "+" : ""}{fmt(r.amount)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {err && (
            <div className="text-sm text-red-600 flex items-center gap-2">
              <AlertCircle className="w-4 h-4" /> {err}
            </div>
          )}

          <div className="flex gap-3 justify-end">
            <button
              onClick={() => { setReport(null); setRows([]); setFile(null); }}
              className="btn-ghost"
            >
              Descartar
            </button>
            <button
              onClick={onCommit}
              disabled={!pickedAccount || saving || rows.every((r) => !r._include)}
              className="btn-primary inline-flex items-center gap-2 disabled:opacity-50"
            >
              {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <CheckCircle2 className="w-4 h-4" />}
              Guardar {rows.filter((r) => r._include).length} movimiento(s)
            </button>
          </div>
        </>
      )}
    </div>
  );
}

function Stat({
  label, value, highlight, tone,
}: {
  label: string; value: string; highlight?: boolean; tone?: "good" | "warn";
}) {
  const toneClass =
    tone === "good" ? "text-emerald-600" :
    tone === "warn" ? "text-amber-600" :
    highlight ? "text-brand-600" : "text-slate-800";
  return (
    <div>
      <div className="text-[11px] uppercase tracking-wide text-slate-500">{label}</div>
      <div className={`text-lg font-semibold ${toneClass}`}>{value}</div>
    </div>
  );
}
