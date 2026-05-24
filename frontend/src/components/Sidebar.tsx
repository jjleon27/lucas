"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { LayoutDashboard, Upload, Receipt, Scissors, LogOut, MessageCircle, Globe, CreditCard, Inbox } from "lucide-react";
import { useEffect, useState } from "react";
import { clearToken, getToken, listPendingTransactions } from "@/lib/api";
import { LOCALES, useT } from "@/lib/i18n";

export default function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const [authed, setAuthed] = useState(false);
  const [pendingCount, setPendingCount] = useState(0);
  const { t, locale, setLocale } = useT();
  const [pickerOpen, setPickerOpen] = useState(false);

  useEffect(() => {
    const hasToken = !!getToken();
    setAuthed(hasToken);
    if (hasToken) {
      listPendingTransactions()
        .then((txs) => setPendingCount(txs.length))
        .catch(() => {});
    }
  }, [pathname]);

  if (pathname === "/" || pathname === "/login") return null;

  const nav = [
    { href: "/dashboard", label: t("nav.dashboard"), icon: LayoutDashboard },
    { href: "/upload", label: t("nav.upload"), icon: Upload },
    { href: "/accounts", label: t("nav.accounts"), icon: CreditCard },
    { href: "/transactions", label: t("nav.transactions"), icon: Receipt },
    { href: "/review", label: "Revisar", icon: Inbox, badge: pendingCount > 0 ? pendingCount : undefined },
    { href: "/chat", label: t("nav.chat"), icon: MessageCircle },
    { href: "/split", label: t("nav.split"), icon: Scissors },
  ];

  const currentFlag = LOCALES.find((l) => l.code === locale)?.flag ?? "🌐";

  return (
    <aside className="fixed inset-x-0 bottom-0 z-40 bg-white border-t border-slate-200 md:top-0 md:left-0 md:bottom-0 md:w-64 md:border-t-0 md:border-r">
      <div className="hidden md:block p-6">
        <div className="text-2xl font-semibold tracking-tight">
          <span className="text-brand-600">lucas</span>.
        </div>
        <p className="text-xs text-slate-500 mt-1">{t("app.tagline")}</p>
      </div>

      <nav className="flex md:flex-col justify-around md:justify-start md:px-3">
        {nav.map(({ href, label, icon: Icon, badge }) => {
          const active = pathname?.startsWith(href);
          return (
            <Link
              key={href}
              href={href}
              className={`relative flex md:w-full flex-col md:flex-row items-center gap-1 md:gap-3 px-3 py-3 md:rounded-xl text-xs md:text-sm ${
                active ? "text-brand-700 md:bg-brand-50" : "text-slate-600 hover:text-slate-900"
              }`}
            >
              <span className="relative">
                <Icon className="w-5 h-5" />
                {badge != null && (
                  <span className="absolute -top-1 -right-1.5 bg-rose-500 text-white text-[9px] font-bold rounded-full w-4 h-4 flex items-center justify-center leading-none">
                    {badge > 9 ? "9+" : badge}
                  </span>
                )}
              </span>
              <span className="md:inline">{label}</span>
            </Link>
          );
        })}
      </nav>

      {/* Language + logout (desktop) */}
      <div className="hidden md:block absolute bottom-4 left-3 right-3 space-y-2">
        <div className="relative">
          <button
            onClick={() => setPickerOpen((v) => !v)}
            className="w-full flex items-center gap-2 px-3 py-2 rounded-xl text-sm text-slate-500 hover:bg-slate-100"
          >
            <Globe className="w-4 h-4" />
            <span>{currentFlag}</span>
            <span>{LOCALES.find((l) => l.code === locale)?.label}</span>
          </button>
          {pickerOpen && (
            <div className="absolute bottom-full mb-2 left-0 right-0 bg-white border border-slate-200 rounded-xl shadow-soft overflow-hidden">
              {LOCALES.map((l) => (
                <button
                  key={l.code}
                  onClick={() => {
                    setLocale(l.code);
                    setPickerOpen(false);
                  }}
                  className={`w-full flex items-center gap-2 px-3 py-2 text-sm hover:bg-slate-50 ${
                    l.code === locale ? "text-brand-700 font-medium" : "text-slate-700"
                  }`}
                >
                  <span>{l.flag}</span>
                  <span>{l.label}</span>
                </button>
              ))}
            </div>
          )}
        </div>

        {authed && (
          <button
            onClick={() => {
              clearToken();
              router.push("/");
            }}
            className="w-full flex items-center gap-2 px-3 py-2 rounded-xl text-sm text-slate-500 hover:bg-slate-100"
          >
            <LogOut className="w-4 h-4" /> {t("nav.logout")}
          </button>
        )}
      </div>

      {/* Mobile: small language toggle in the nav bar */}
      <div className="md:hidden absolute top-2 right-3">
        <select
          value={locale}
          onChange={(e) => setLocale(e.target.value as any)}
          className="text-xs bg-transparent text-slate-500 outline-none"
        >
          {LOCALES.map((l) => (
            <option key={l.code} value={l.code}>
              {l.flag} {l.code.toUpperCase()}
            </option>
          ))}
        </select>
      </div>
    </aside>
  );
}
