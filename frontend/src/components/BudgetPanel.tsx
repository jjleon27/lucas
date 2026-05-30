"use client";
/**
 * BudgetPanel — the income + fixed expenses + variable budget panel on the dashboard.
 *
 * Logic:
 *   income_target   = what you expect to earn this month (editable)
 *   fixed_total     = sum of recurring fixed expenses (rent, phone, gym…)
 *   variable_budget = income_target - fixed_total  → what you can freely spend
 *
 *   safe_spend_actual    = (income_received - fixed_total - spent) / days_remaining
 *   safe_spend_projected = (variable_budget - spent) / days_remaining
 */
import { useState } from "react";
import { Plus, Trash2, ChevronDown, ChevronUp, Check, Lightbulb } from "lucide-react";
import type { DashboardData } from "@/lib/api";
import { updateMe } from "@/lib/api";
import { useT, formatMoney } from "@/lib/i18n";
import NumericInput from "@/components/NumericInput";

interface FixedItem {
  name: string;
  amount: number;
  day?: number;
}

interface Props {
  data: DashboardData;
  currency: string;
  onSaved: () => void;     // refresh dashboard
}

export default function BudgetPanel({ data, currency, onSaved }: Props) {
  const { t } = useT();

  // ── Local edit state ──────────────────────────────────────────────────────
  const [incomeTarget, setIncomeTarget] = useState(data.income_target);
  const [fixedIncomes, setFixedIncomes] = useState<FixedItem[]>(
    (data.fixed_incomes || []).map((fi) => ({ name: fi.name, amount: fi.amount })),
  );
  const [fixedExpenses, setFixedExpenses] = useState<FixedItem[]>(
    (data.fixed_expenses || []).map((fe) => ({ name: fe.name, amount: fe.amount })),
  );
  const [newIncomeName, setNewIncomeName] = useState("");
  const [newIncomeAmount, setNewIncomeAmount] = useState(0);
  const [newName, setNewName] = useState("");
  const [newAmount, setNewAmount] = useState(0);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [expanded, setExpanded] = useState(true);

  // ── Derived values (preview before save) ────────────────────────────────
  const fixedIncomeTotal = fixedIncomes.reduce((s, fi) => s + fi.amount, 0);
  const effectiveTarget = incomeTarget > 0 ? incomeTarget : fixedIncomeTotal;
  const fixedTotal = fixedExpenses.reduce((s, fe) => s + fe.amount, 0);
  const variableBudget = Math.max(effectiveTarget - fixedTotal, 0);
  const spent = data.total_spent;
  const dr = Math.max(data.days_remaining, 1);
  const safeActual = Math.max(data.income_actual - fixedTotal - spent, 0) / dr;
  const safeProjected = Math.max(variableBudget - spent, 0) / dr;

  const incomeReceivedPct =
    effectiveTarget > 0 ? Math.min(100, Math.round((data.income_actual / effectiveTarget) * 100)) : 0;

  function addFixedIncome() {
    if (!newIncomeName.trim() || newIncomeAmount <= 0) return;
    const updated = [...fixedIncomes, { name: newIncomeName.trim(), amount: newIncomeAmount, day: 1 }];
    setFixedIncomes(updated);
    setNewIncomeName("");
    setNewIncomeAmount(0);
    // Auto-update income target to match sum of fixed incomes if no manual target set
    if (incomeTarget === 0) {
      setIncomeTarget(updated.reduce((s, fi) => s + fi.amount, 0));
    }
  }

  function removeFixedIncome(i: number) {
    setFixedIncomes((prev) => prev.filter((_, idx) => idx !== i));
  }

  // ── Save to backend ───────────────────────────────────────────────────────
  async function save() {
    setSaving(true);
    try {
      const targetToSave = incomeTarget > 0 ? incomeTarget : fixedIncomeTotal;
      await updateMe({
        settings: {
          income_target: targetToSave,
          fixed_incomes: fixedIncomes,
          fixed_expenses: fixedExpenses,
        } as any,
      });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
      onSaved();
    } catch (e: any) {
      alert(e?.message || "No se pudo guardar el presupuesto");
    } finally {
      setSaving(false);
    }
  }

  function addFixed() {
    if (!newName.trim() || newAmount <= 0) return;
    setFixedExpenses((prev) => [...prev, { name: newName.trim(), amount: newAmount, day: 1 }]);
    setNewName("");
    setNewAmount(0);
  }

  function removeFixed(i: number) {
    setFixedExpenses((prev) => prev.filter((_, idx) => idx !== i));
  }

  const fmt = (v: number) => formatMoney(v, currency);

  return (
    <div className="card space-y-4">
      {/* Header */}
      <button
        onClick={() => setExpanded((x) => !x)}
        className="flex items-center justify-between w-full text-left"
      >
        <h3 className="text-base font-semibold">{t("dashboard.incomeSection")}</h3>
        {expanded ? <ChevronUp size={16} className="text-slate-400" /> : <ChevronDown size={16} className="text-slate-400" />}
      </button>

      {expanded && (
        <>
          {/* ── Fixed incomes ─────────────────────────────────────────────── */}
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium text-slate-700">💼 Ingresos fijos del mes</span>
              {fixedIncomeTotal > 0 && (
                <span className="text-sm font-mono text-emerald-600">+{fmt(fixedIncomeTotal)}</span>
              )}
            </div>
            <p className="text-xs text-slate-400 -mt-1">Sueldo, pensión, beca, arriendo recibido, etc.</p>

            {fixedIncomes.length > 0 && (
              <ul className="space-y-1">
                {fixedIncomes.map((fi, i) => (
                  <li key={i} className="flex items-center gap-2 text-sm">
                    <span className="flex-1 text-slate-600 truncate">{fi.name}</span>
                    <span className="font-mono text-emerald-600 shrink-0">+{fmt(fi.amount)}</span>
                    <button onClick={() => removeFixedIncome(i)} className="text-slate-300 hover:text-rose-500 transition-colors">
                      <Trash2 size={13} />
                    </button>
                  </li>
                ))}
              </ul>
            )}

            <div className="flex gap-2">
              <input
                className="input flex-1 text-sm"
                placeholder="Sueldo, Beca, Pension…"
                value={newIncomeName}
                onChange={(e) => setNewIncomeName(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && addFixedIncome()}
              />
              <NumericInput
                className="input w-28 font-mono text-sm"
                value={newIncomeAmount}
                onChange={setNewIncomeAmount}
                placeholder="0"
              />
              <button
                onClick={addFixedIncome}
                className="btn-ghost p-2"
                disabled={!newIncomeName.trim() || newIncomeAmount <= 0}
                title="Agregar ingreso fijo"
              >
                <Plus size={16} />
              </button>
            </div>
          </div>

          {/* ── Income target ─────────────────────────────────────────────── */}
          <div className="space-y-2">
            <div className="flex items-center justify-between gap-3">
              <label className="text-sm font-medium text-slate-700">
                🎯 {t("dashboard.incomeTarget")}
              </label>
              {data.historical_avg_income > 0 && (
                <button
                  type="button"
                  className="flex items-center gap-1 text-xs text-brand-600 hover:text-brand-700"
                  onClick={() => setIncomeTarget(data.historical_avg_income)}
                  title={t("dashboard.suggestFromHistory")}
                >
                  <Lightbulb size={12} />
                  {t("dashboard.historicalHint", { amount: fmt(data.historical_avg_income) })}
                </button>
              )}
            </div>
            <NumericInput
              className="input font-mono"
              value={incomeTarget}
              onChange={setIncomeTarget}
              placeholder={
                fixedIncomeTotal > 0
                  ? `${fmt(fixedIncomeTotal)} (de ingresos fijos)`
                  : t("dashboard.incomeTargetPlaceholder")
              }
            />
            {fixedIncomeTotal > 0 && incomeTarget === 0 && (
              <button
                type="button"
                className="text-xs text-brand-600 hover:underline"
                onClick={() => setIncomeTarget(fixedIncomeTotal)}
              >
                Usar suma de ingresos fijos: {fmt(fixedIncomeTotal)}
              </button>
            )}

            {/* Progress bar: received vs target */}
            {incomeTarget > 0 && (
              <div className="space-y-1">
                <div className="flex justify-between text-xs text-slate-500">
                  <span>
                    {t("dashboard.incomeActual")}: <strong className="text-emerald-600">{fmt(data.income_actual)}</strong>
                  </span>
                  <span>{t("dashboard.incomeProgress", { pct: incomeReceivedPct })}</span>
                </div>
                <div className="h-2 rounded-full bg-slate-100 overflow-hidden">
                  <div
                    className="h-full rounded-full bg-emerald-500 transition-all"
                    style={{ width: `${incomeReceivedPct}%` }}
                  />
                </div>
              </div>
            )}
          </div>

          {/* ── Fixed expenses ────────────────────────────────────────────── */}
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium text-slate-700">Gastos fijos del mes</span>
              {fixedTotal > 0 && (
                <span className="text-sm font-mono text-slate-500">−{fmt(fixedTotal)}</span>
              )}
            </div>

            {fixedExpenses.length > 0 && (
              <ul className="space-y-1">
                {fixedExpenses.map((fe, i) => (
                  <li key={i} className="flex items-center gap-2 text-sm">
                    <span className="flex-1 text-slate-600 truncate">{fe.name}</span>
                    <span className="font-mono text-slate-500 shrink-0">{fmt(fe.amount)}</span>
                    <button
                      onClick={() => removeFixed(i)}
                      className="text-slate-300 hover:text-rose-500 transition-colors"
                    >
                      <Trash2 size={13} />
                    </button>
                  </li>
                ))}
              </ul>
            )}

            {/* Add new fixed expense */}
            <div className="flex gap-2">
              <input
                className="input flex-1 text-sm"
                placeholder="Arriendo, Gym, Celular…"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && addFixed()}
              />
              <NumericInput
                className="input w-28 font-mono text-sm"
                value={newAmount}
                onChange={setNewAmount}
                placeholder="0"
              />
              <button
                onClick={addFixed}
                className="btn-ghost p-2"
                disabled={!newName.trim() || newAmount <= 0}
                title="Agregar"
              >
                <Plus size={16} />
              </button>
            </div>
          </div>

          {/* ── Variable budget summary ───────────────────────────────────── */}
          {incomeTarget > 0 && (
            <div className="rounded-2xl bg-slate-50 border border-slate-100 p-3 space-y-3">
              <div className="flex justify-between text-sm">
                <span className="text-slate-500">Para gastos variables</span>
                <span className="font-mono font-semibold">{fmt(variableBudget)}</span>
              </div>
              {spent > 0 && (
                <div className="flex justify-between text-sm">
                  <span className="text-slate-500">Gastado este mes</span>
                  <span className="font-mono text-rose-600">−{fmt(spent)}</span>
                </div>
              )}
              <div className="border-t border-slate-200 pt-2 flex justify-between text-sm font-medium">
                <span>Queda</span>
                <span className={`font-mono ${Math.max(variableBudget - spent, 0) > 0 ? "text-emerald-600" : "text-rose-600"}`}>
                  {fmt(Math.max(variableBudget - spent, 0))}
                </span>
              </div>

              {/* Two safe-daily cards */}
              <div className="grid grid-cols-2 gap-2 pt-1">
                <div className="rounded-xl bg-white border border-slate-200 p-2.5 text-center">
                  <p className="text-[10px] uppercase tracking-wide text-slate-400 mb-1">
                    {t("dashboard.safeDailyActual")}
                  </p>
                  <p className="text-lg font-mono font-semibold text-slate-800">
                    {fmt(Math.round(safeActual))}
                  </p>
                  <p className="text-[10px] text-slate-400 mt-0.5">
                    {t("dashboard.safeDailyActualHint")}
                  </p>
                </div>
                <div className="rounded-xl bg-emerald-50 border border-emerald-100 p-2.5 text-center">
                  <p className="text-[10px] uppercase tracking-wide text-emerald-600 mb-1">
                    {t("dashboard.safeDailyProjected")}
                  </p>
                  <p className="text-lg font-mono font-semibold text-emerald-800">
                    {fmt(Math.round(safeProjected))}
                  </p>
                  <p className="text-[10px] text-emerald-600 mt-0.5">
                    {t("dashboard.safeDailyProjectedHint")}
                  </p>
                </div>
              </div>

              <p className="text-[10px] text-slate-400 text-center">
                {t("dashboard.daysLeft", { n: data.days_remaining })}
              </p>
            </div>
          )}

          {/* ── Save button ───────────────────────────────────────────────── */}
          <button
            className="btn-primary w-full flex items-center justify-center gap-2"
            onClick={save}
            disabled={saving}
          >
            {saved ? (
              <><Check size={15} /> {t("dashboard.targetSaved")}</>
            ) : saving ? (
              "…"
            ) : (
              t("dashboard.saveTarget")
            )}
          </button>
        </>
      )}
    </div>
  );
}
