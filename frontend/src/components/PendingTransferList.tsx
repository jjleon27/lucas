"use client";
/**
 * Shows the list of credit-card payments (or `is_transfer=true` rows) that
 * still don't have a linked sibling. For each row, the user can click
 * "Enlazar" to open a small picker of candidate matches (pulled from the
 * backend's wider-window suggest endpoint) and pair them in one click.
 *
 * When the filter is empty we show a friendly "all clear" card.
 */
import { useState } from "react";
import {
  Account,
  Transaction,
  linkTransfer,
  suggestTransferMatches,
} from "@/lib/api";
import { useT, formatMoney } from "@/lib/i18n";

interface Props {
  txs: Transaction[];
  accounts: Account[];
  onLinked: () => void;
}

export default function PendingTransferList({ txs, accounts, onLinked }: Props) {
  const { t } = useT();
  const [openFor, setOpenFor] = useState<number | null>(null);
  const [candidates, setCandidates] = useState<Transaction[]>([]);
  const [loading, setLoading] = useState(false);
  const [linking, setLinking] = useState<number | null>(null);

  const accName = (id: number | null) =>
    accounts.find((a) => a.id === id)?.name ?? "—";

  async function openPicker(tx: Transaction) {
    setOpenFor(tx.id);
    setCandidates([]);
    setLoading(true);
    try {
      const matches = await suggestTransferMatches(tx.id);
      setCandidates(matches);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }

  async function confirmLink(txId: number, otherId: number) {
    setLinking(otherId);
    try {
      await linkTransfer(txId, otherId);
      setOpenFor(null);
      setCandidates([]);
      onLinked();
    } catch (e) {
      console.error(e);
      alert(`No se pudo enlazar: ${String(e)}`);
    } finally {
      setLinking(null);
    }
  }

  if (txs.length === 0) {
    return (
      <div className="card text-center text-slate-500">
        {t("tx.pending.none")}
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {txs.map((tx) => {
        const open = openFor === tx.id;
        return (
          <div key={tx.id} className="card">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="font-medium truncate">{tx.merchant || "—"}</span>
                  <span className="inline-block px-2 py-0.5 rounded-full bg-sky-100 text-sky-800 text-xs">
                    pago tarjeta
                  </span>
                </div>
                <div className="text-xs text-slate-500 mt-0.5">
                  {tx.date} · {accName(tx.account_id)}
                </div>
              </div>
              <div className="flex items-center gap-3">
                <span className="font-mono text-sm">
                  {formatMoney(Math.abs(tx.amount), tx.currency)}
                </span>
                {!open && (
                  <button
                    className="btn-ghost text-sm"
                    onClick={() => openPicker(tx)}
                  >
                    🔗 {t("tx.pending.link")}
                  </button>
                )}
              </div>
            </div>

            {open && (
              <div className="mt-4 border-t border-slate-200 pt-4">
                {loading ? (
                  <div className="text-sm text-slate-500">
                    {t("tx.pending.searching")}
                  </div>
                ) : candidates.length === 0 ? (
                  <div className="space-y-3">
                    <p className="text-sm text-slate-500">
                      {t("tx.pending.noMatch")}
                    </p>
                    <button
                      className="btn-ghost text-sm"
                      onClick={() => setOpenFor(null)}
                    >
                      {t("tx.pending.cancel")}
                    </button>
                  </div>
                ) : (
                  <>
                    <p className="text-sm text-slate-600 mb-2">
                      {t("tx.pending.pickMatch")}
                    </p>
                    <ul className="space-y-1.5">
                      {candidates.map((c) => (
                        <li
                          key={c.id}
                          className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 rounded-xl border border-slate-200 hover:bg-slate-50"
                        >
                          <div className="min-w-0">
                            <div className="text-sm truncate">
                              {c.merchant || "—"}
                            </div>
                            <div className="text-xs text-slate-500">
                              {c.date} · {accName(c.account_id)}
                            </div>
                          </div>
                          <div className="flex items-center gap-2">
                            <span className="font-mono text-sm">
                              {formatMoney(Math.abs(c.amount), c.currency)}
                            </span>
                            <button
                              className="btn-primary text-xs px-3 py-1"
                              disabled={linking === c.id}
                              onClick={() => confirmLink(tx.id, c.id)}
                            >
                              {linking === c.id ? "…" : t("tx.pending.link")}
                            </button>
                          </div>
                        </li>
                      ))}
                    </ul>
                    <button
                      className="mt-3 text-xs text-slate-500 hover:underline"
                      onClick={() => setOpenFor(null)}
                    >
                      {t("tx.pending.cancel")}
                    </button>
                  </>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
