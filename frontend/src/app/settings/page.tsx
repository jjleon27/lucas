"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { getToken, me, updateMe } from "@/lib/api";

export default function SettingsPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [newEmail, setNewEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");

  useEffect(() => {
    if (!getToken()) { router.replace("/"); return; }
    me().then((u) => { setEmail(u.email); setNewEmail(u.email); }).catch(() => {});
  }, [router]);

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setMsg(""); setErr("");
    const patch: Record<string, string> = {};
    if (newEmail !== email) patch.email = newEmail;
    if (password) patch.password = password;
    if (Object.keys(patch).length === 0) { setMsg("Sin cambios."); return; }
    setSaving(true);
    try {
      await updateMe(patch);
      setEmail(newEmail);
      setPassword("");
      setMsg("Guardado correctamente.");
    } catch (e: any) {
      setErr(e.message || "Error al guardar");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="max-w-md mx-auto space-y-6 pb-24 md:pb-0">
      <h1 className="text-3xl font-semibold tracking-tight">Perfil</h1>

      <form className="card space-y-4" onSubmit={handleSave}>
        <label className="block">
          <span className="text-xs uppercase text-slate-500">Email</span>
          <input
            className="input mt-1"
            type="email"
            value={newEmail}
            onChange={(e) => setNewEmail(e.target.value)}
            required
          />
        </label>

        <label className="block">
          <span className="text-xs uppercase text-slate-500">Nueva contraseña</span>
          <div className="relative mt-1">
            <input
              className="input pr-10"
              type={showPassword ? "text" : "password"}
              placeholder="Dejar vacío para no cambiar"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              minLength={8}
            />
            <button
              type="button"
              tabIndex={-1}
              className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600 text-base leading-none"
              onClick={() => setShowPassword((v) => !v)}
            >
              {showPassword ? "🙈" : "👁️"}
            </button>
          </div>
        </label>

        {msg && <p className="text-sm text-emerald-600">{msg}</p>}
        {err && <p className="text-sm text-rose-600">{err}</p>}

        <button className="btn-primary w-full" disabled={saving}>
          {saving ? "Guardando…" : "Guardar cambios"}
        </button>
      </form>
    </div>
  );
}
