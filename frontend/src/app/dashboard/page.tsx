"use client";
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  PieChart, Pie, Cell, ResponsiveContainer, Tooltip,
} from "recharts";
import StatCard from "@/components/StatCard";
import BudgetPanel from "@/components/BudgetPanel";
import { DashboardData, User, getDashboard, getToken, me, updateMe } from "@/lib/api";
import { useT, formatMoney } from "@/lib/i18n";
import FixedItemsPanel from "@/components/FixedItemsPanel";

const CAT_COLORS = [
  "#10b981", "#6366f1", "#f97316", "#ef4444",
  "#a855f7", "#06b6d4", "#eab308", "#ec4899",
];

const CURRENCIES: { code: string; label: string }[] = [
  { code: "CLP", label: "🇨🇱 CLP — Peso chileno" },
  { code: "USD", label: "🇺🇸 USD — US Dollar" },
  { code: "EUR", label: "🇪🇺 EUR — Euro" },
  { code: "BRL", label: "🇧🇷 BRL — Real" },
  { code: "MXN", label: "🇲🇽 MXN — Peso mexicano" },
  { code: "ARS", label: "🇦🇷 ARS — Peso argentino" },
  { code: "PEN", label: "🇵🇪 PEN — Sol" },
  { code: "COP", label: "🇨🇴 COP — Peso colombiano" },
  { code: "GBP", label: "🇬🇧 GBP — Pound" },
];

export default function DashboardPage() {
  const router = useRouter();
  const { t, locale } = useT();
  const [data, setData] = useState<DashboardData | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [currency, setCurrency] = useState<string>("CLP");

  const loadData = useCallback(async () => {
    try {
      const [d, u] = await Promise.all([getDashboard(), me()]);
      setData(d);
      setUser(u);
      const curr = u.settings?.currency || (locale === "es" ? "CLP" : "USD");
      setCurrency(curr);
    } catch {
      router.replace("/");
    } finally {
      setLoading(false);
    }
  }, [router, locale]);

  useEffect(() => {
    if (!getToken()) { router.replace("/"); return; }
    loadData();
  }, [router, loadData]);

  async function saveCurrency(code: string) {
    setCurrency(code);
    if (typeof window !== "undefined") window.localStorage.setItem("lucas_currency", code);
    try {
      await updateMe({ settings: { currency: code } as any });
      setData(await getDashboard());
    } catch { /* ignore */ }
  }

  if (loading || !data) return <div className="p-8 text-slate-500">{t("tx.loading")}</div>;

  const tone: "good" | "warning" | "danger" = data.status as any;
  const fmt = (v: number) => formatMoney(v, currency);
  const LOCALE_BCP47: Record<string, string> = { es: "es-CL", en: "en-US", pt: "pt-BR" };
  const monthLabel = new Date().toLocaleDateString(
    LOCALE_BCP47[locale] || "es-CL",
    { month: "long", year: "numeric" },
  );

  return (
    <div className="max-w-6xl mx-auto space-y-6 pb-24 md:pb-0">
      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <header>
        <h1 className="text-3xl font-semibold tracking-tight">{t("dashboard.title")}</h1>
        <p className="text-slate-500 capitalize">{monthLabel}</p>
      </header>

      {/* ── Alerts ─────────────────────────────────────────────────────────── */}
      {data.alerts.length > 0 && (
        <div className="space-y-2">
          {data.alerts.map((a, i) => (
            <div key={i} className="card text-sm">{a}</div>
          ))}
        </div>
      )}

      {(data.pending_review_count ?? 0) > 0 && (
        <a
          href="/review"
          className="block bg-rose-50 border border-rose-200 text-rose-800 rounded-2xl px-4 py-3 text-sm hover:bg-rose-100 flex items-center justify-between"
        >
          <span>
            📬 Tienes <strong>{data.pending_review_count}</strong> gasto{data.pending_review_count !== 1 ? "s" : ""} del banco por revisar
          </span>
          <span className="font-semibold">Revisar →</span>
        </a>
      )}

      {data.pending_transfers > 0 && (
        <a
          href="/transactions?transfers=pending"
          className="block bg-sky-50 border border-sky-200 text-sky-800 rounded-2xl px-4 py-3 text-sm hover:bg-sky-100"
        >
          💳 Tienes <strong>{data.pending_transfers}</strong> pago(s) de tarjeta
          sin enlazar con su cargo en la cuenta débito. Revísalos para que no
          inflen tu saldo. →
        </a>
      )}

      {/* ── Stat cards ─────────────────────────────────────────────────────── */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-2 sm:gap-4">
        <StatCard
          label={t("dashboard.spentThisMonth")}
          value={fmt(data.total_spent)}
          hint={
            data.income_target > 0
              ? t("dashboard.ofBudget", { budget: fmt(data.income_target) })
              : t("dashboard.noBudgetSet")
          }
          tone={tone}
          href="/transactions?type=expense"
        />
        <StatCard
          label={t("dashboard.incomeActual")}
          value={fmt(data.income_actual)}
          hint={
            data.income_target > 0
              ? `${Math.round((data.income_actual / data.income_target) * 100)}% de meta`
              : "ingresos este mes"
          }
          href="/transactions?type=income"
        />
        {(() => {
          const balance = data.income_actual - data.total_spent;
          const balanceTone = balance >= 0 ? "good" : "danger";
          return (
            <StatCard
              label="Balance del mes"
              value={(balance >= 0 ? "+" : "") + fmt(balance)}
              hint={balance >= 0 ? "en verde este mes" : "gastaste más de lo recibido"}
              tone={balanceTone}
            />
          );
        })()}
        <StatCard
          label={t("dashboard.projectedEOM")}
          value={fmt(data.predicted_end_of_month)}
          hint={`a ${data.days_remaining} días del fin de mes`}
          tone={tone}
        />
      </div>

      {/* ── Main content grid ──────────────────────────────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">

        {/* Category chart */}
        <div className="card lg:col-span-2">
          <h3 className="text-base font-semibold mb-4">{t("dashboard.categoryBreakdown")}</h3>
          {data.by_category.length === 0 ? (
            <p className="text-sm text-slate-500">{t("dashboard.noSpending")}</p>
          ) : (
            <div className="flex flex-col md:flex-row gap-6 items-center">
              <div className="w-full md:w-1/2 h-52">
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie
                      data={data.by_category}
                      dataKey="total"
                      nameKey="category"
                      innerRadius={50}
                      outerRadius={80}
                      paddingAngle={2}
                      cursor="pointer"
                      onClick={(d: any) => router.push(`/transactions?category=${encodeURIComponent(d.category)}`)}
                    >
                      {data.by_category.map((_, i) => (
                        <Cell key={i} fill={CAT_COLORS[i % CAT_COLORS.length]} />
                      ))}
                    </Pie>
                    <Tooltip formatter={(v: number) => fmt(v)} />
                  </PieChart>
                </ResponsiveContainer>
              </div>
              <ul className="flex-1 w-full space-y-2 text-sm">
                {data.by_category.map((c, i) => (
                  <li key={c.category}>
                    <a
                      href={`/transactions?category=${encodeURIComponent(c.category)}`}
                      className="flex justify-between items-center py-0.5 rounded-lg hover:bg-slate-50 px-1 -mx-1 transition-colors"
                    >
                      <span className="flex items-center gap-2">
                        <span
                          className="w-2.5 h-2.5 rounded-full shrink-0"
                          style={{ backgroundColor: CAT_COLORS[i % CAT_COLORS.length] }}
                        />
                        {c.category}
                      </span>
                      <span className="font-mono text-slate-600">{fmt(c.total)}</span>
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>

        {/* Right column: Budget panel + fixed items + quick actions */}
        <div className="space-y-4">
          {/* Budget / income panel */}
          <BudgetPanel data={data} currency={currency} onSaved={loadData} />

          {/* Fixed income & expenses */}
          {user && (
            <FixedItemsPanel
              user={user}
              currency={currency}
              onUpdated={(u) => { setUser(u); getDashboard().then(setData).catch(() => {}); }}
            />
          )}

          {/* Quick actions */}
          <div className="card">
            <h3 className="text-base font-semibold mb-3">{t("dashboard.quickActions")}</h3>
            <div className="space-y-2">
              <a href="/upload" className="btn-primary w-full">{t("dashboard.uploadScreenshot")}</a>
              <a href="/cartola" className="btn-ghost w-full">Importar cartola (PDF)</a>
              <a href="/chat" className="btn-ghost w-full">{t("dashboard.askLucas")}</a>
              <a href="/split" className="btn-ghost w-full">{t("dashboard.splitBill")}</a>
            </div>
          </div>
        </div>
      </div>

    </div>
  );
}
