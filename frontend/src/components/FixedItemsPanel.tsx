"use client";
/**
 * Panel de compromisos fijos del mes (gastos e ingresos recurrentes).
 * - Muestra cada ítem con su día de cobro/depósito esperado
 * - Permite confirmar (✓) o rechazar (✗) para el mes actual
 * - Los estados se persisten en user.settings.fixed_confirmations
 */
import { useState } from "react";
import { FixedItem, User, updateMe } from "@/lib/api";
import { formatMoney } from "@/lib/i18n";
import { Check, X, Plus, Trash2, ChevronDown, ChevronUp } from "lucide-react";

type ConfirmState = "confirmed" | "rejected" | null;

function monthKey(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

function itemKey(item: FixedItem): string {
  return `${item.name}__${item.day}`;
}

function todayDay(): number {
  return new Date().getDate();
}

interface ItemRowProps {
  item: FixedItem;
  state: "confirmed" | "rejected" | null;
  today: number;
  saving: boolean;
  fmt: (v: number) => string;
  onConfirm: (item: FixedItem, s: "confirmed" | "rejected") => void;
  onRemove: (item: FixedItem) => void;
}

function ItemRow({ item, state, today, saving, fmt, onConfirm, onRemove }: ItemRowProps) {
  const overdue = item.day <= today && !state;
  const upcoming = item.day > today;
  return (
    <div className={`flex items-center gap-3 py-2 px-3 rounded-xl ${
      state === "confirmed" ? "bg-green-50"
      : state === "rejected" ? "bg-slate-50 opacity-60"
      : overdue ? "bg-amber-50 border border-amber-200"
      : "bg-white border border-slate-100"
    }`}>
      <div className={`text-sm font-mono w-6 text-center ${overdue && !state ? "text-amber-600 font-bold" : "text-slate-400"}`}>
        {item.day}
      </div>
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium truncate">{item.name}</div>
        <div className={`text-xs ${item.is_income ? "text-green-600" : "text-rose-600"}`}>
          {item.is_income ? "+" : "-"}{fmt(item.amount)}
          {upcoming && <span className="text-slate-400 ml-1">· en {item.day - today} días</span>}
          {overdue && !state && <span className="text-amber-600 ml-1 font-medium">· vencido</span>}
        </div>
      </div>
      {state === "confirmed" && <Check className="w-4 h-4 text-green-600 shrink-0" />}
      {state === "rejected" && <X className="w-4 h-4 text-slate-400 shrink-0" />}
      {!state && (
        <div className="flex gap-1 shrink-0">
          <button
            onClick={() => onConfirm(item, "confirmed")}
            disabled={saving}
            className="p-1.5 rounded-lg bg-green-100 hover:bg-green-200 text-green-700"
            title="Confirmar — sí ocurrió"
          >
            <Check className="w-3.5 h-3.5" />
          </button>
          <button
            onClick={() => onConfirm(item, "rejected")}
            disabled={saving}
            className="p-1.5 rounded-lg bg-slate-100 hover:bg-slate-200 text-slate-500"
            title="No ocurrió este mes"
          >
            <X className="w-3.5 h-3.5" />
          </button>
          <button
            onClick={() => onRemove(item)}
            disabled={saving}
            className="p-1.5 rounded-lg hover:bg-rose-50 text-slate-300 hover:text-rose-400"
            title="Eliminar ítem"
          >
            <Trash2 className="w-3 h-3" />
          </button>
        </div>
      )}
    </div>
  );
}

interface Props {
  user: User;
  currency: string;
  onUpdated: (user: User) => void;
}

export default function FixedItemsPanel({ user, currency, onUpdated }: Props) {
  const [open, setOpen] = useState(false);
  const [adding, setAdding] = useState(false);
  const [saving, setSaving] = useState(false);
  const [newItem, setNewItem] = useState<Partial<FixedItem>>({ day: 1, is_income: false, amount: 0, name: "" });

  const mk = monthKey();
  const allItems: FixedItem[] = [
    ...(user.settings.fixed_incomes ?? []).map((it) => ({ ...it, is_income: true })),
    ...(user.settings.fixed_expenses ?? []).map((it) => ({ ...it, is_income: false })),
  ].sort((a, b) => a.day - b.day);

  const confirmations: Record<string, "confirmed" | "rejected"> =
    (user.settings.fixed_confirmations as any)?.[mk] ?? {};

  const pending = allItems.filter((it) => !confirmations[itemKey(it)]);
  const done = allItems.filter((it) => confirmations[itemKey(it)]);

  const fmt = (v: number) => formatMoney(v, currency);

  async function setConfirmation(item: FixedItem, state: "confirmed" | "rejected") {
    setSaving(true);
    try {
      const existing = (user.settings.fixed_confirmations as any) ?? {};
      const monthData = { ...(existing[mk] ?? {}), [itemKey(item)]: state };
      const updated = await updateMe({
        settings: { fixed_confirmations: { ...existing, [mk]: monthData } } as any,
      });
      onUpdated(updated);
    } catch (e: any) {
      alert(e?.message || "No se pudo guardar");
    } finally {
      setSaving(false);
    }
  }

  async function removeItem(item: FixedItem) {
    if (!confirm(`¿Eliminar "${item.name}"?`)) return;
    setSaving(true);
    try {
      const key = item.is_income ? "fixed_incomes" : "fixed_expenses";
      const current = (item.is_income ? user.settings.fixed_incomes : user.settings.fixed_expenses) ?? [];
      const next = current.filter((it) => it.name !== item.name || it.day !== item.day);
      const updated = await updateMe({ settings: { [key]: next } as any });
      onUpdated(updated);
    } catch (e: any) {
      alert(e?.message || "No se pudo eliminar");
    } finally {
      setSaving(false);
    }
  }

  async function addItem() {
    if (!newItem.name?.trim() || !newItem.amount || !newItem.day) return;
    setSaving(true);
    try {
      const key = newItem.is_income ? "fixed_incomes" : "fixed_expenses";
      const current = (newItem.is_income ? user.settings.fixed_incomes : user.settings.fixed_expenses) ?? [];
      const item: FixedItem = {
        name: newItem.name!.trim(),
        amount: Math.abs(newItem.amount!),
        day: newItem.day!,
        is_income: newItem.is_income ?? false,
      };
      const updated = await updateMe({ settings: { [key]: [...current, item] } as any });
      onUpdated(updated);
      setNewItem({ day: 1, is_income: false, amount: 0, name: "" });
      setAdding(false);
    } catch (e: any) {
      alert(e?.message || "No se pudo agregar");
    } finally {
      setSaving(false);
    }
  }

  const today = todayDay();

  return (
    <div className="card">
      <button
        className="w-full flex items-center justify-between"
        onClick={() => setOpen((v) => !v)}
      >
        <div className="text-left">
          <h3 className="text-base font-semibold">
            📅 Compromisos del mes
          </h3>
          {!open && pending.length > 0 && (
            <p className="text-xs text-slate-500">
              {pending.filter((it) => it.day <= today).length > 0
                ? `${pending.filter((it) => it.day <= today).length} pendiente(s) por confirmar`
                : `${allItems.length} ítems · ${done.length} confirmados`}
            </p>
          )}
        </div>
        {open ? <ChevronUp className="w-4 h-4 text-slate-400" /> : <ChevronDown className="w-4 h-4 text-slate-400" />}
      </button>

      {open && (
        <div className="mt-3 space-y-2">
          {allItems.length === 0 && !adding && (
            <p className="text-sm text-slate-400 text-center py-2">
              Sin ítems. Agrega ingresos y gastos fijos del mes.
            </p>
          )}

          {/* Pending items */}
          {pending.map((it) => (
            <ItemRow
              key={itemKey(it)}
              item={it}
              state={confirmations[itemKey(it)] ?? null}
              today={today}
              saving={saving}
              fmt={fmt}
              onConfirm={setConfirmation}
              onRemove={removeItem}
            />
          ))}

          {/* Done items (collapsible) */}
          {done.length > 0 && (
            <details className="group">
              <summary className="text-xs text-slate-400 cursor-pointer hover:text-slate-600 list-none flex items-center gap-1 mt-1">
                <ChevronDown className="w-3 h-3 group-open:rotate-180 transition-transform" />
                {done.length} confirmado(s) este mes
              </summary>
              <div className="mt-1 space-y-1.5">
                {done.map((it) => (
                  <ItemRow
                    key={itemKey(it)}
                    item={it}
                    state={confirmations[itemKey(it)] ?? null}
                    today={today}
                    saving={saving}
                    fmt={fmt}
                    onConfirm={setConfirmation}
                    onRemove={removeItem}
                  />
                ))}
              </div>
            </details>
          )}

          {/* Add new item */}
          {adding ? (
            <div className="border border-slate-200 rounded-xl p-3 space-y-2 mt-2">
              <input
                className="input text-sm"
                placeholder="Nombre (ej: Arriendo, Sueldo)"
                value={newItem.name}
                onChange={(e) => setNewItem((s) => ({ ...s, name: e.target.value }))}
              />
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="text-xs text-slate-500 block mb-1">Monto</label>
                  <input
                    className="input text-sm"
                    type="number"
                    min="0"
                    placeholder="0"
                    value={newItem.amount || ""}
                    onChange={(e) => setNewItem((s) => ({ ...s, amount: Number(e.target.value) }))}
                  />
                </div>
                <div>
                  <label className="text-xs text-slate-500 block mb-1">Día del mes</label>
                  <input
                    className="input text-sm"
                    type="number"
                    min="1"
                    max="31"
                    value={newItem.day}
                    onChange={(e) => setNewItem((s) => ({ ...s, day: Math.min(31, Math.max(1, Number(e.target.value))) }))}
                  />
                </div>
              </div>
              <div className="flex rounded-xl bg-slate-100 p-1 text-sm">
                <button
                  className={`flex-1 py-1.5 rounded-lg ${!newItem.is_income ? "bg-white shadow-sm" : "text-slate-500"}`}
                  onClick={() => setNewItem((s) => ({ ...s, is_income: false }))}
                >
                  Gasto
                </button>
                <button
                  className={`flex-1 py-1.5 rounded-lg ${newItem.is_income ? "bg-white shadow-sm text-brand-700" : "text-slate-500"}`}
                  onClick={() => setNewItem((s) => ({ ...s, is_income: true }))}
                >
                  Ingreso
                </button>
              </div>
              <div className="flex gap-2">
                <button className="btn-ghost flex-1 text-sm" onClick={() => setAdding(false)}>Cancelar</button>
                <button className="btn-primary flex-1 text-sm" onClick={addItem} disabled={saving || !newItem.name?.trim()}>
                  Guardar
                </button>
              </div>
            </div>
          ) : (
            <button
              className="w-full flex items-center justify-center gap-2 text-sm text-slate-400 hover:text-slate-600 py-2 rounded-xl border border-dashed border-slate-200 hover:border-slate-300 mt-1"
              onClick={() => setAdding(true)}
            >
              <Plus className="w-4 h-4" /> Agregar ítem fijo
            </button>
          )}
        </div>
      )}
    </div>
  );
}
