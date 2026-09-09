"use client";
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { FolderKanban, Plus, Pencil, Trash2, Archive, ChevronDown, ChevronUp } from "lucide-react";
import {
  Project, ProjectInput, ProjectSummary, Transaction,
  getToken, listProjects, createProject, updateProject, deleteProject,
  getProjectSummary, getProjectTransactions,
} from "@/lib/api";
import { formatMoney } from "@/lib/i18n";

const COLORS = ["#6366f1", "#10b981", "#f97316", "#ef4444", "#a855f7", "#06b6d4", "#eab308", "#ec4899"];

function BudgetBar({ spent, budget, color }: { spent: number; budget: number; color: string }) {
  if (!budget || budget <= 0) {
    return <p className="text-xs text-slate-500 mt-1">Sin presupuesto · gastado {formatMoney(spent, "CLP")}</p>;
  }
  const pct = Math.min(spent / budget, 1);
  const over = spent > budget;
  return (
    <div className="mt-2">
      <div className="h-2 w-full rounded-full bg-slate-200 overflow-hidden">
        <div
          className="h-full rounded-full transition-all"
          style={{ width: `${pct * 100}%`, background: over ? "#ef4444" : color }}
        />
      </div>
      <p className={`text-xs mt-1 ${over ? "text-red-600 font-medium" : "text-slate-500"}`}>
        {formatMoney(spent, "CLP")} de {formatMoney(budget, "CLP")}
        {" · "}
        {over
          ? `excedido en ${formatMoney(spent - budget, "CLP")}`
          : `quedan ${formatMoney(budget - spent, "CLP")}`}
      </p>
    </div>
  );
}

export default function ProjectsPage() {
  const router = useRouter();
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [showArchived, setShowArchived] = useState(false);
  const [editing, setEditing] = useState<(ProjectInput & { id?: number }) | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [openId, setOpenId] = useState<number | null>(null);
  const [detail, setDetail] = useState<{ summary: ProjectSummary; txs: Transaction[] } | null>(null);

  const load = useCallback(() => {
    listProjects(showArchived).then(setProjects).catch(() => router.replace("/"));
  }, [showArchived, router]);

  useEffect(() => {
    if (!getToken()) { router.replace("/"); return; }
    load();
  }, [load, router]);

  async function openDetail(p: Project) {
    if (openId === p.id) { setOpenId(null); setDetail(null); return; }
    setOpenId(p.id);
    setDetail(null);
    const [summary, txs] = await Promise.all([
      getProjectSummary(p.id),
      getProjectTransactions(p.id),
    ]);
    setDetail({ summary, txs });
  }

  async function save() {
    if (!editing || !editing.name.trim()) { setErr("Ponle un nombre"); return; }
    setBusy(true); setErr("");
    try {
      if (editing.id) {
        await updateProject(editing.id, {
          name: editing.name, budget: editing.budget ?? 0,
          start_date: editing.start_date || null, end_date: editing.end_date || null,
          color: editing.color,
        });
      } else {
        await createProject({
          name: editing.name, budget: editing.budget ?? 0,
          start_date: editing.start_date || null, end_date: editing.end_date || null,
          color: editing.color,
        });
      }
      setEditing(null);
      load();
    } catch (e) { setErr(String(e)); }
    finally { setBusy(false); }
  }

  async function toggleArchive(p: Project) {
    await updateProject(p.id, { archived: !p.archived });
    load();
  }

  async function remove(p: Project) {
    if (!confirm(`¿Borrar "${p.name}"? Las transacciones NO se borran, quedan sin proyecto.`)) return;
    try { await deleteProject(p.id); load(); }
    catch (e) { alert(String(e)); }
  }

  return (
    <div className="max-w-2xl mx-auto">
      <header className="flex items-center justify-between mb-5">
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <FolderKanban className="w-6 h-6 text-indigo-600" /> Proyectos
        </h1>
        <button
          className="btn-primary flex items-center gap-1.5 text-sm"
          onClick={() => setEditing({
            name: "", budget: 0, color: COLORS[Math.floor(Math.random() * COLORS.length)],
            start_date: new Date().toISOString().slice(0, 10), end_date: null,
          })}
        >
          <Plus className="w-4 h-4" /> Nuevo
        </button>
      </header>

      <p className="text-sm text-slate-500 mb-4">
        Agrupa gastos de distintas categorías bajo un mismo contexto (ej. "Viaje a Buenos Aires",
        "Remodelación cocina") y controla su presupuesto.
      </p>

      {editing && (
        <div className="card p-4 mb-4 space-y-3 border border-indigo-200">
          <input
            className="input w-full" placeholder="Nombre del proyecto" autoFocus
            value={editing.name}
            onChange={(e) => setEditing({ ...editing, name: e.target.value })}
          />
          <div className="grid grid-cols-2 gap-3">
            <label className="block">
              <span className="text-xs text-slate-500 uppercase">Presupuesto (opcional)</span>
              <input
                className="input mt-0.5 w-full" type="number" inputMode="numeric" placeholder="0"
                value={editing.budget || ""}
                onChange={(e) => setEditing({ ...editing, budget: Number(e.target.value) || 0 })}
              />
            </label>
            <label className="block">
              <span className="text-xs text-slate-500 uppercase">Color</span>
              <div className="flex gap-1.5 mt-1.5 flex-wrap">
                {COLORS.map((c) => (
                  <button key={c} type="button"
                    className={`w-6 h-6 rounded-full ${editing.color === c ? "ring-2 ring-offset-1 ring-slate-400" : ""}`}
                    style={{ background: c }}
                    onClick={() => setEditing({ ...editing, color: c })}
                  />
                ))}
              </div>
            </label>
            <label className="block">
              <span className="text-xs text-slate-500 uppercase">Desde</span>
              <input className="input mt-0.5 w-full" type="date"
                value={editing.start_date || ""}
                onChange={(e) => setEditing({ ...editing, start_date: e.target.value })}
              />
            </label>
            <label className="block">
              <span className="text-xs text-slate-500 uppercase">Hasta</span>
              <input className="input mt-0.5 w-full" type="date"
                value={editing.end_date || ""}
                onChange={(e) => setEditing({ ...editing, end_date: e.target.value })}
              />
            </label>
          </div>
          {err && <p className="text-sm text-red-600">{err}</p>}
          <div className="flex gap-2">
            <button className="btn-primary" disabled={busy} onClick={save}>
              {busy ? "Guardando…" : "Guardar"}
            </button>
            <button className="btn-ghost" onClick={() => { setEditing(null); setErr(""); }}>Cancelar</button>
          </div>
        </div>
      )}

      {projects === null ? (
        <p className="text-slate-400">Cargando…</p>
      ) : projects.length === 0 ? (
        <p className="text-slate-400">Aún no tienes proyectos.</p>
      ) : (
        <ul className="space-y-3">
          {projects.map((p) => (
            <li key={p.id} className="card p-4">
              <div className="flex items-start justify-between gap-3">
                <button className="flex-1 text-left" onClick={() => openDetail(p)}>
                  <div className="flex items-center gap-2">
                    <span className="w-3 h-3 rounded-full shrink-0" style={{ background: p.color }} />
                    <span className="font-semibold">{p.name}</span>
                    {p.archived && (
                      <span className="text-[10px] uppercase text-slate-400 border border-slate-300 rounded px-1">archivado</span>
                    )}
                    <span className="text-xs text-slate-400">{p.tx_count} mov.</span>
                    {openId === p.id
                      ? <ChevronUp className="w-4 h-4 text-slate-400" />
                      : <ChevronDown className="w-4 h-4 text-slate-400" />}
                  </div>
                  <BudgetBar spent={p.spent} budget={p.budget} color={p.color} />
                </button>
                <div className="flex gap-1 shrink-0">
                  <button className="p-1.5 rounded-lg hover:bg-slate-100 text-slate-500" title="Editar"
                    onClick={() => setEditing({
                      id: p.id, name: p.name, budget: p.budget, color: p.color,
                      start_date: p.start_date, end_date: p.end_date,
                    })}>
                    <Pencil className="w-4 h-4" />
                  </button>
                  <button className="p-1.5 rounded-lg hover:bg-slate-100 text-slate-500" title={p.archived ? "Desarchivar" : "Archivar"}
                    onClick={() => toggleArchive(p)}>
                    <Archive className="w-4 h-4" />
                  </button>
                  <button className="p-1.5 rounded-lg hover:bg-red-50 text-red-500" title="Borrar" onClick={() => remove(p)}>
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              </div>

              {openId === p.id && (
                <div className="mt-3 pt-3 border-t border-slate-100">
                  {!detail ? (
                    <p className="text-sm text-slate-400">Cargando detalle…</p>
                  ) : (
                    <>
                      {detail.summary.by_category.length > 0 && (
                        <div className="mb-3">
                          <p className="text-xs uppercase text-slate-400 mb-1">Gasto por categoría</p>
                          <ul className="text-sm space-y-0.5">
                            {detail.summary.by_category.map((c) => (
                              <li key={c.category} className="flex justify-between">
                                <span>{c.category}</span>
                                <span className="font-mono">{formatMoney(c.amount, "CLP")}</span>
                              </li>
                            ))}
                          </ul>
                        </div>
                      )}
                      <p className="text-xs uppercase text-slate-400 mb-1">Movimientos ({detail.txs.length})</p>
                      <ul className="text-sm divide-y divide-slate-100">
                        {detail.txs.map((tx) => (
                          <li key={tx.id} className="flex justify-between py-1.5">
                            <span className="truncate">
                              <span className="text-slate-400 mr-2">{tx.date}</span>
                              {tx.merchant || tx.category}
                            </span>
                            <span className={`font-mono shrink-0 ${tx.is_income ? "text-emerald-600" : ""}`}>
                              {tx.is_income ? "+" : "-"}{formatMoney(Math.abs(tx.amount), tx.currency)}
                            </span>
                          </li>
                        ))}
                        {detail.txs.length === 0 && (
                          <li className="py-1.5 text-slate-400">
                            Sin movimientos. Asigna transacciones a este proyecto desde la pantalla de movimientos.
                          </li>
                        )}
                      </ul>
                    </>
                  )}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}

      <button
        className="mt-4 text-sm text-slate-500 underline"
        onClick={() => setShowArchived((v) => !v)}
      >
        {showArchived ? "Ocultar archivados" : "Ver archivados"}
      </button>
    </div>
  );
}
