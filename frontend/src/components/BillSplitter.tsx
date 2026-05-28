"use client";
/**
 * BillSplitter v2 — multi-person, per-item split rules, color-coded avatars.
 *
 * Each item card shows colored avatar chips for every participant.
 * Tapping an avatar toggles that person on/off for that item.
 * When 2+ people share an item, an "Ajustar" row lets you pick
 * equal / % / exact-amount per person.
 */
import { useState, useMemo } from "react";
import { Plus, Trash2, ChevronDown, ChevronUp, Check, Pencil, X } from "lucide-react";
import NumericInput from "@/components/NumericInput";
import {
  Person,
  ReceiptItemV2,
  AssigneeIn,
  AssigneeOut,
  SplitResultV2,
} from "@/lib/api";
import { useT, formatMoney } from "@/lib/i18n";

const PALETTE = [
  "#ef4444", "#f97316", "#eab308", "#10b981",
  "#06b6d4", "#6366f1", "#a855f7", "#ec4899",
];

// Initials from a name (up to 2 chars)
function initials(name: string): string {
  const parts = name.trim().split(/\s+/);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return name.slice(0, 2).toUpperCase();
}

// ─── Avatar chip ────────────────────────────────────────────
function Avatar({
  person,
  active,
  size = "md",
  onClick,
}: {
  person: Person;
  active: boolean;
  size?: "sm" | "md";
  onClick?: () => void;
}) {
  const dim = size === "sm" ? "w-6 h-6 text-[10px]" : "w-8 h-8 text-xs";
  return (
    <button
      type="button"
      title={person.name}
      onClick={onClick}
      className={`${dim} rounded-full font-semibold flex items-center justify-center border-2 transition-all select-none ${
        active
          ? "ring-2 ring-offset-1 scale-110 shadow"
          : "opacity-40 hover:opacity-70"
      }`}
      style={{
        backgroundColor: active ? person.color : `${person.color}33`,
        borderColor: person.color,
        color: active ? "#fff" : person.color,
        // @ts-ignore
        "--tw-ring-color": person.color,
      }}
    >
      {initials(person.name)}
    </button>
  );
}

// ─── Per-item split adjuster ────────────────────────────────
function SplitAdjuster({
  item,
  assignedPeople,
  currency,
  onSave,
}: {
  item: ReceiptItemV2;
  assignedPeople: Person[];
  currency: string;
  onSave: (assignees: AssigneeIn[]) => void;
}) {
  const { t } = useT();
  type Mode = "equal" | "percent" | "amount";
  const [mode, setMode] = useState<Mode>(
    (item.assignees[0]?.split_type as Mode) ?? "equal",
  );

  // Build initial values from existing assignees
  const [values, setValues] = useState<Record<number, number>>(() => {
    const init: Record<number, number> = {};
    for (const p of assignedPeople) {
      const existing = item.assignees.find((a) => a.person_id === p.id);
      init[p.id] =
        existing && existing.value != null ? existing.value : 0;
    }
    return init;
  });

  const lineTotal = item.line_total;
  const n = assignedPeople.length;

  // Preview amounts
  const preview = useMemo(() => {
    const out: Record<number, number> = {};
    if (mode === "equal") {
      const each = lineTotal / n;
      assignedPeople.forEach((p) => (out[p.id] = each));
    } else if (mode === "percent") {
      let allocated = 0;
      assignedPeople.forEach((p, i) => {
        const pct = values[p.id] ?? 0;
        const amt = i === n - 1 ? lineTotal - allocated : (lineTotal * pct) / 100;
        out[p.id] = amt;
        allocated += i === n - 1 ? amt : (lineTotal * pct) / 100;
      });
    } else {
      let allocated = 0;
      assignedPeople.forEach((p, i) => {
        const amt =
          i === n - 1
            ? lineTotal - allocated
            : values[p.id] ?? 0;
        out[p.id] = amt;
        allocated += i < n - 1 ? (values[p.id] ?? 0) : 0;
      });
    }
    return out;
  }, [mode, values, assignedPeople, lineTotal, n]);

  const percentSum = useMemo(() => {
    if (mode !== "percent") return 100;
    return assignedPeople.reduce(
      (s, p, i) =>
        i < n - 1 ? s + (values[p.id] ?? 0) : s,
      0,
    );
  }, [mode, values, assignedPeople, n]);

  const isValid =
    mode === "equal" ||
    (mode === "percent" && percentSum <= 100) ||
    mode === "amount";

  function save() {
    const assignees: AssigneeIn[] = assignedPeople.map((p) => ({
      person_id: p.id,
      split_type: mode,
      value: mode === "equal" ? null : values[p.id] ?? 0,
    }));
    onSave(assignees);
  }

  const tabs: { key: Mode; label: string }[] = [
    { key: "equal", label: t("split.splitEqual") },
    { key: "percent", label: t("split.splitPercent") },
    { key: "amount", label: t("split.splitAmount") },
  ];

  return (
    <div className="mt-3 pt-3 border-t border-slate-100 space-y-3">
      {/* Mode tabs */}
      <div className="flex gap-1 bg-slate-100 rounded-xl p-1 text-xs">
        {tabs.map((tab) => (
          <button
            key={tab.key}
            type="button"
            onClick={() => setMode(tab.key)}
            className={`flex-1 py-1.5 rounded-lg font-medium transition ${
              mode === tab.key
                ? "bg-white shadow text-slate-900"
                : "text-slate-500 hover:text-slate-700"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Per-person inputs */}
      <ul className="space-y-2">
        {assignedPeople.map((p, i) => {
          const isLast = i === n - 1;
          return (
            <li key={p.id} className="flex items-center gap-2">
              <Avatar person={p} active size="sm" />
              <span className="text-sm font-medium flex-1 min-w-0 truncate">
                {p.name}
              </span>
              {mode === "equal" ? (
                <span className="font-mono text-sm text-slate-600">
                  {formatMoney(preview[p.id] ?? 0, currency)}
                </span>
              ) : isLast ? (
                <span className="font-mono text-sm text-slate-400 italic">
                  {formatMoney(preview[p.id] ?? 0, currency)} (resto)
                </span>
              ) : (
                <div className="flex items-center gap-1">
                  <NumericInput
                    className="input w-20 text-right text-sm py-1"
                    placeholder={mode === "percent" ? "50" : "5000"}
                    value={values[p.id] ?? 0}
                    onChange={(v) => setValues((prev) => ({ ...prev, [p.id]: v }))}
                    allowDecimals={mode === "amount"}
                  />
                  <span className="text-xs text-slate-500">
                    {mode === "percent" ? "%" : currency}
                  </span>
                </div>
              )}
            </li>
          );
        })}
      </ul>

      {mode === "percent" && percentSum > 100 && (
        <p className="text-xs text-rose-500">{t("split.percentSum")}</p>
      )}

      <button
        type="button"
        disabled={!isValid}
        onClick={save}
        className="btn-primary w-full text-sm py-2 disabled:opacity-50"
      >
        <Check className="w-4 h-4 inline mr-1" />
        {t("split.done")}
      </button>
    </div>
  );
}

// ─── Item card ───────────────────────────────────────────────
function ItemCard({
  item,
  people,
  currency,
  onTogglePerson,
  onSaveAdjust,
  onUpdateItem,
  onDeleteItem,
  onAssignAll,
}: {
  item: ReceiptItemV2;
  people: Person[];
  currency: string;
  onTogglePerson: (itemId: number, personId: number) => void;
  onSaveAdjust: (itemId: number, assignees: AssigneeIn[]) => void;
  onUpdateItem: (itemId: number, patch: { name?: string; price?: number }) => void;
  onDeleteItem: (itemId: number) => void;
  onAssignAll: (itemId: number, personIds: number[]) => void;
}) {
  const { t } = useT();
  const [adjustOpen, setAdjustOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editName, setEditName] = useState(item.name);
  const [editPrice, setEditPrice] = useState(item.line_total / Math.max(item.quantity, 1));

  const assignedIds = new Set(item.assignees.map((a) => a.person_id));
  const assignedPeople = people.filter((p) => assignedIds.has(p.id));
  const hasMultiple = assignedPeople.length >= 2;

  // Background gradient using the colors of all assigned people
  const assignedColors = assignedPeople.map((p) => p.color);
  const borderColor =
    assignedColors.length === 1
      ? assignedColors[0]
      : assignedColors.length > 1
        ? assignedColors[0]
        : "#e2e8f0";
  const bgColor =
    assignedColors.length >= 1 ? `${borderColor}12` : "transparent";

  return (
    <li
      className="rounded-2xl border px-4 py-3 transition-all"
      style={{ borderColor, backgroundColor: bgColor }}
    >
      {/* Top row: name + price */}
      {editing ? (
        <div className="flex items-center gap-2">
          <input
            className="input flex-1 text-sm py-1 px-2"
            value={editName}
            onChange={(e) => setEditName(e.target.value)}
            autoFocus
          />
          <NumericInput
            className="input w-28 font-mono text-sm py-1 px-2"
            value={editPrice}
            onChange={setEditPrice}
            allowDecimals
            placeholder="0"
          />
          <button
            type="button"
            className="text-emerald-600 hover:text-emerald-700"
            onClick={() => {
              onUpdateItem(item.id, { name: editName, price: editPrice });
              setEditing(false);
            }}
          >
            <Check className="w-4 h-4" />
          </button>
          <button
            type="button"
            className="text-slate-400 hover:text-slate-600"
            onClick={() => { setEditName(item.name); setEditPrice(item.line_total / Math.max(item.quantity, 1)); setEditing(false); }}
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      ) : (
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0 flex items-center gap-1.5">
            <span className="font-medium text-sm">{item.name}</span>
            {/propina|tip/i.test(item.name) && (
              <span className="text-[9px] font-bold uppercase tracking-wide text-amber-600 bg-amber-50 px-1.5 py-0.5 rounded-full">
                propina
              </span>
            )}
            {item.quantity > 1 && (
              <span className="ml-0.5 text-xs text-slate-400">×{item.quantity}</span>
            )}
          </div>
          <div className="flex items-center gap-1.5 shrink-0">
            <span className="font-mono text-sm">{formatMoney(item.line_total, currency)}</span>
            <button
              type="button"
              className="text-slate-300 hover:text-indigo-500 transition"
              onClick={() => setEditing(true)}
              title="Editar"
            >
              <Pencil className="w-3.5 h-3.5" />
            </button>
            <button
              type="button"
              className="text-slate-300 hover:text-rose-500 transition"
              onClick={() => onDeleteItem(item.id)}
              title="Eliminar"
            >
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>
      )}

      {/* Person toggle row */}
      <div className="mt-2.5 flex items-center gap-1.5 flex-wrap">
        {people.map((p) => (
          <Avatar
            key={p.id}
            person={p}
            active={assignedIds.has(p.id)}
            size="sm"
            onClick={() => onTogglePerson(item.id, p.id)}
          />
        ))}

        {/* TODOS button — assign all people in one API call */}
        {assignedIds.size < people.length && people.length > 1 && (
          <button
            type="button"
            onClick={() => onAssignAll(item.id, people.map((p) => p.id))}
            className="text-[10px] font-bold px-2 py-0.5 rounded-full border border-slate-300 text-slate-500 hover:bg-slate-100 hover:text-slate-700 transition"
          >
            TODOS
          </button>
        )}

        {/* Adjust button — only when 2+ people share this item */}
        {hasMultiple && (
          <button
            type="button"
            onClick={() => setAdjustOpen((v) => !v)}
            className="ml-auto flex items-center gap-1 text-xs text-slate-500 hover:text-slate-700 px-2 py-1 rounded-lg hover:bg-slate-100"
          >
            {t("split.adjustSplit")}
            {adjustOpen ? (
              <ChevronUp className="w-3 h-3" />
            ) : (
              <ChevronDown className="w-3 h-3" />
            )}
          </button>
        )}
      </div>

      {/* Per-assignee preview dots (mini amounts) */}
      {assignedPeople.length > 0 && !adjustOpen && (
        <div className="mt-1.5 flex flex-wrap gap-2">
          {item.assignees.map((a) => {
            const p = people.find((x) => x.id === a.person_id);
            if (!p) return null;
            return (
              <span
                key={a.person_id}
                className="text-[10px] font-mono px-1.5 py-0.5 rounded-full"
                style={{ backgroundColor: `${p.color}22`, color: p.color }}
              >
                {p.name.split(" ")[0]}: {formatMoney(a.computed_amount, currency)}
              </span>
            );
          })}
        </div>
      )}

      {/* Inline split adjuster */}
      {adjustOpen && hasMultiple && (
        <SplitAdjuster
          item={item}
          assignedPeople={assignedPeople}
          currency={currency}
          onSave={(assignees) => {
            onSaveAdjust(item.id, assignees);
            setAdjustOpen(false);
          }}
        />
      )}
    </li>
  );
}

// ─── Main component ──────────────────────────────────────────
interface Props {
  result: SplitResultV2;
  people: Person[];
  currency: string;
  onTogglePerson: (itemId: number, personId: number) => void;
  onSaveAdjust: (itemId: number, assignees: AssigneeIn[]) => void;
  onAddPerson: (name: string, color: string) => void;
  onRemovePerson: (id: number) => void;
  onUpdateItem: (itemId: number, patch: { name?: string; price?: number }) => void;
  onDeleteItem: (itemId: number) => void;
  onAddItem: (name: string, price: number) => void;
  onAssignAll: (itemId: number, personIds: number[]) => void;
}

export default function BillSplitter({
  result,
  people,
  currency,
  onTogglePerson,
  onSaveAdjust,
  onAddPerson,
  onRemovePerson,
  onUpdateItem,
  onDeleteItem,
  onAddItem,
  onAssignAll,
}: Props) {
  const { t } = useT();
  const [newName, setNewName] = useState("");
  const [addItemName, setAddItemName] = useState("");
  const [addItemPrice, setAddItemPrice] = useState(0);
  const [viewMode, setViewMode] = useState<"boleta" | "agrupado">("boleta");

  const completion = result.completion_pct;
  const isComplete = completion >= 100 && result.unassigned_total === 0;

  // Summary breakdown
  const subtotal = result.items
    .filter((it) => !/propina|tip|^iva|descuento|dscto|dcto|rebaja/i.test(it.name.trim()))
    .reduce((s, it) => s + it.line_total, 0);
  const descuento = result.items
    .filter((it) => /descuento|dscto|dcto|rebaja/i.test(it.name))
    .reduce((s, it) => s + it.line_total, 0);
  const propina = result.items
    .filter((it) => /propina|tip/i.test(it.name))
    .reduce((s, it) => s + it.line_total, 0);
  const iva = result.items
    .filter((it) => /^iva/i.test(it.name.trim()))
    .reduce((s, it) => s + it.line_total, 0);
  // Ordered items: boleta = DB order, agrupado = sorted by name (same items together)
  const displayItems = useMemo(() => {
    if (viewMode === "boleta") return result.items;
    return [...result.items].sort((a, b) => a.name.localeCompare(b.name, "es", { sensitivity: "base" }));
  }, [viewMode, result.items]);

  return (
    <div className="space-y-6">
      {/* ── People bar ── */}
      <div className="card">
        <h3 className="text-sm font-semibold text-slate-500 uppercase tracking-wide mb-3">
          {t("split.stepPeople")}
        </h3>
        <div className="flex flex-wrap gap-2 mb-3">
          {people.map((p) => (
            <span
              key={p.id}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-full text-sm font-medium border"
              style={{
                backgroundColor: `${p.color}18`,
                borderColor: `${p.color}55`,
                color: p.color,
              }}
            >
              <span
                className="w-5 h-5 rounded-full text-[10px] font-bold flex items-center justify-center text-white"
                style={{ backgroundColor: p.color }}
              >
                {initials(p.name)}
              </span>
              {p.name}
              {!p.is_me && (
                <button
                  type="button"
                  onClick={() => onRemovePerson(p.id)}
                  className="opacity-40 hover:opacity-80 ml-0.5"
                >
                  <Trash2 className="w-3 h-3" />
                </button>
              )}
            </span>
          ))}
        </div>
        <div className="flex gap-2">
          <input
            className="input flex-1"
            placeholder={t("split.addPerson")}
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && newName.trim()) {
                onAddPerson(newName.trim(), PALETTE[people.length % PALETTE.length]);
                setNewName("");
              }
            }}
          />
          <button
            type="button"
            className="btn-primary px-3"
            onClick={() => {
              if (!newName.trim()) return;
              onAddPerson(newName.trim(), PALETTE[people.length % PALETTE.length]);
              setNewName("");
            }}
          >
            <Plus className="w-4 h-4" />
          </button>
        </div>
        <p className="text-xs text-slate-400 mt-2">{t("split.assignHint")}</p>
      </div>

      {/* ── Items list ── */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-slate-500 uppercase tracking-wide">
            {t("split.stepItems")}
          </h3>
          <div className="flex items-center gap-2">
            {/* View mode toggle */}
            {result.items.length > 1 && (
              <div className="flex text-[11px] rounded-lg bg-slate-100 p-0.5">
                {(["boleta", "agrupado"] as const).map((mode) => (
                  <button
                    key={mode}
                    type="button"
                    className={`px-2.5 py-1 rounded-md transition capitalize ${
                      viewMode === mode ? "bg-white shadow-soft font-medium text-slate-800" : "text-slate-500"
                    }`}
                    onClick={() => setViewMode(mode)}
                  >
                    {mode === "boleta" ? "Boleta" : "Agrupado"}
                  </button>
                ))}
              </div>
            )}
            <span className="text-xs text-slate-400">
              {formatMoney(result.total_amount, currency)}
            </span>
          </div>
        </div>

        <ul className="space-y-2">
          {displayItems.map((item, idx) => {
            // In agrupado mode, add a visual separator between different item groups
            const prevItem = idx > 0 ? displayItems[idx - 1] : null;
            const isNewGroup =
              viewMode === "agrupado" &&
              prevItem &&
              prevItem.name.toLowerCase() !== item.name.toLowerCase();
            return (
              <div key={item.id}>
                {isNewGroup && <div className="h-px bg-slate-100 my-1" />}
                <ItemCard
                  item={item}
                  people={people}
                  currency={currency}
                  onTogglePerson={onTogglePerson}
                  onSaveAdjust={onSaveAdjust}
                  onUpdateItem={onUpdateItem}
                  onDeleteItem={onDeleteItem}
                  onAssignAll={onAssignAll}
                />
              </div>
            );
          })}
          {result.items.length === 0 && (
            <li className="text-sm text-slate-400 text-center py-6">
              {t("split.noItems")}
            </li>
          )}
        </ul>

        {/* Agregar ítem manual */}
        <div className="mt-3 flex gap-2">
          <input
            className="input flex-1 text-sm"
            placeholder="Nombre del ítem"
            value={addItemName}
            onChange={(e) => setAddItemName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && addItemName.trim() && addItemPrice > 0) {
                onAddItem(addItemName.trim(), addItemPrice);
                setAddItemName(""); setAddItemPrice(0);
              }
            }}
          />
          <NumericInput
            className="input w-28 font-mono text-sm"
            placeholder="Precio"
            value={addItemPrice}
            onChange={setAddItemPrice}
            allowDecimals
          />
          <button
            type="button"
            className="btn-primary px-3"
            disabled={!addItemName.trim() || addItemPrice <= 0}
            onClick={() => {
              onAddItem(addItemName.trim(), addItemPrice);
              setAddItemName(""); setAddItemPrice(0);
            }}
          >
            <Plus className="w-4 h-4" />
          </button>
        </div>

        {/* Progress bar */}
        {result.items.length > 0 && (
          <div className="mt-4">
            <div className="flex justify-between text-xs text-slate-400 mb-1">
              <span>{isComplete ? t("split.complete") : t("split.progress")}</span>
              <span>{completion.toFixed(0)}%</span>
            </div>
            <div className="w-full bg-slate-100 rounded-full h-1.5 overflow-hidden">
              <div
                className={`h-1.5 rounded-full transition-all ${
                  isComplete ? "bg-emerald-500" : "bg-indigo-500"
                }`}
                style={{ width: `${completion}%` }}
              />
            </div>
            {/* Unassigned warning */}
            {result.unassigned_total > 0 && (
              <p className="mt-2 text-xs text-amber-600 font-medium">
                ⚠️ {formatMoney(result.unassigned_total, currency)} sin asignar —
                el monto total <strong>no calza</strong> aún. Toca los avatares para asignar todos los ítems.
              </p>
            )}
          </div>
        )}
      </div>

      {/* ── Bill summary breakdown ── */}
      {result.items.length > 0 && (
        <div className="card space-y-2">
          <h3 className="text-sm font-semibold text-slate-500 uppercase tracking-wide mb-1">
            Resumen
          </h3>
          <div className="flex justify-between text-sm">
            <span className="text-slate-600">Subtotal</span>
            <span className="font-mono">{formatMoney(subtotal, currency)}</span>
          </div>
          {descuento !== 0 && (
            <div className="flex justify-between text-sm">
              <span className="text-emerald-600">Descuento</span>
              <span className="font-mono text-emerald-600">{formatMoney(descuento, currency)}</span>
            </div>
          )}
          {propina !== 0 && (
            <div className="flex justify-between text-sm">
              <span className="text-slate-600">Propina</span>
              <span className="font-mono">{formatMoney(propina, currency)}</span>
            </div>
          )}
          {iva !== 0 && (
            <div className="flex justify-between text-sm">
              <span className="text-slate-600">IVA (19%)</span>
              <span className="font-mono">{formatMoney(iva, currency)}</span>
            </div>
          )}
          <div className="flex justify-between text-sm font-semibold pt-2 border-t border-slate-100">
            <span>Total</span>
            <span className="font-mono">{formatMoney(result.total_amount, currency)}</span>
          </div>
        </div>
      )}

      {/* ── Per-person summary ── */}
      {result.people.length > 0 && (
        <div className="card">
          <h3 className="text-sm font-semibold text-slate-500 uppercase tracking-wide mb-3">
            {t("split.totals")}
          </h3>
          <ul className="space-y-2">
            {result.people.map((p) => (
              <li key={p.person_id} className="flex items-center justify-between">
                <span className="flex items-center gap-2 text-sm">
                  <span
                    className="w-2.5 h-2.5 rounded-full"
                    style={{ backgroundColor: p.person_color }}
                  />
                  <span className="font-medium">{p.person_name}</span>
                  {p.is_me && (
                    <span className="text-xs text-slate-400">(tú)</span>
                  )}
                </span>
                <span className="font-mono text-sm">
                  {formatMoney(p.total, currency)}
                </span>
              </li>
            ))}
            {result.unassigned_total > 0 && (
              <li className="flex items-center justify-between text-slate-400 text-sm pt-2 border-t">
                <span>{t("split.unassigned")}</span>
                <span className="font-mono">
                  {formatMoney(result.unassigned_total, currency)}
                </span>
              </li>
            )}
          </ul>
        </div>
      )}
    </div>
  );
}
