"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  LayoutDashboard, Upload, Receipt, Scissors, LogOut,
  MessageCircle, Globe, CreditCard, Inbox, UserCircle,
  MoreHorizontal, X, type LucideIcon,
} from "lucide-react";

interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
  badge?: number;
}
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
  const [moreOpen, setMoreOpen] = useState(false);

  useEffect(() => {
    const hasToken = !!getToken();
    setAuthed(hasToken);
    if (hasToken) {
      listPendingTransactions()
        .then((txs) => setPendingCount(txs.length))
        .catch(() => {});
    }
  }, [pathname]);

  useEffect(() => { setMoreOpen(false); }, [pathname]);

  if (pathname === "/" || pathname === "/login") return null;

  // Mobile: 4 primary tabs
  const primaryNav: NavItem[] = [
    { href: "/dashboard",    label: t("nav.dashboard"),    icon: LayoutDashboard },
    { href: "/upload",       label: t("nav.upload"),       icon: Upload },
    { href: "/transactions", label: t("nav.transactions"), icon: Receipt },
    { href: "/accounts",     label: t("nav.accounts"),     icon: CreditCard },
  ];

  // Mobile: overflow items inside "Más" sheet
  const moreNav: NavItem[] = [
    { href: "/split",    label: t("nav.split"),  icon: Scissors },
    { href: "/review",   label: "Revisar",        icon: Inbox,        badge: pendingCount > 0 ? pendingCount : undefined },
    { href: "/chat",     label: t("nav.chat"),   icon: MessageCircle },
    { href: "/settings", label: "Perfil",         icon: UserCircle },
  ];

  // Desktop: all items in sidebar
  const allNav = [...primaryNav, ...moreNav];

  const currentFlag = LOCALES.find((l) => l.code === locale)?.flag ?? "🌐";
  const moreActive = moreNav.some((item) => pathname?.startsWith(item.href));
  const moreBadge = moreNav.reduce((sum, item) => sum + (item.badge ?? 0), 0);

  return (
    <>
      <aside className="fixed inset-x-0 bottom-0 z-40 bg-white border-t border-slate-200 md:top-0 md:left-0 md:bottom-0 md:w-64 md:border-t-0 md:border-r">
        {/* Desktop: logo */}
        <div className="hidden md:block p-6">
          <div className="text-2xl font-semibold tracking-tight">
            <span className="text-brand-600">lucas</span>.
          </div>
          <p className="text-xs text-slate-500 mt-1">{t("app.tagline")}</p>
        </div>

        {/* Desktop nav: all items */}
        <nav className="hidden md:flex md:flex-col md:px-3">
          {allNav.map(({ href, label, icon: Icon, badge }) => {
            const active = pathname?.startsWith(href);
            return (
              <Link
                key={href}
                href={href}
                className={`relative flex w-full flex-row items-center gap-3 px-3 py-3 rounded-xl text-sm ${
                  active ? "text-brand-700 bg-brand-50" : "text-slate-500 hover:text-slate-900"
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
                <span>{label}</span>
              </Link>
            );
          })}
        </nav>

        {/* Mobile nav: 4 primary tabs + "Más" */}
        <nav className="md:hidden flex items-stretch h-16">
          {primaryNav.map(({ href, label, icon: Icon }) => {
            const active = pathname?.startsWith(href);
            return (
              <Link
                key={href}
                href={href}
                className={`flex flex-col items-center justify-center gap-1 flex-1 ${
                  active ? "text-brand-700" : "text-slate-400 hover:text-slate-600"
                }`}
              >
                <Icon className="w-6 h-6" />
                <span className="text-[10px] font-medium leading-none">{label}</span>
              </Link>
            );
          })}

          {/* Más button */}
          <button
            onClick={() => setMoreOpen(true)}
            className={`flex flex-col items-center justify-center gap-1 flex-1 ${
              moreActive ? "text-brand-700" : "text-slate-400 hover:text-slate-600"
            }`}
          >
            <span className="relative">
              <MoreHorizontal className="w-6 h-6" />
              {moreBadge > 0 && (
                <span className="absolute -top-1 -right-1.5 bg-rose-500 text-white text-[9px] font-bold rounded-full w-4 h-4 flex items-center justify-center leading-none">
                  {moreBadge > 9 ? "9+" : moreBadge}
                </span>
              )}
            </span>
            <span className="text-[10px] font-medium leading-none">Más</span>
          </button>
        </nav>


        {/* Desktop: language + logout */}
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
                    onClick={() => { setLocale(l.code); setPickerOpen(false); }}
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
              onClick={() => { clearToken(); router.push("/"); }}
              className="w-full flex items-center gap-2 px-3 py-2 rounded-xl text-sm text-slate-500 hover:bg-slate-100"
            >
              <LogOut className="w-4 h-4" /> {t("nav.logout")}
            </button>
          )}
        </div>
      </aside>

      {/* "Más" bottom sheet — mobile only */}
      {moreOpen && (
        <>
          <div
            className="md:hidden fixed inset-0 z-50 bg-black/30"
            onClick={() => setMoreOpen(false)}
          />
          <div className="md:hidden fixed bottom-0 inset-x-0 z-50 bg-white rounded-t-3xl shadow-xl">
            <div className="flex items-center justify-between px-5 pt-5 pb-3 border-b border-slate-100">
              <span className="font-semibold text-slate-800">Más opciones</span>
              <button
                onClick={() => setMoreOpen(false)}
                className="text-slate-400 hover:text-slate-600 p-1"
              >
                <X className="w-5 h-5" />
              </button>
            </div>
            <div className="px-4 py-3 space-y-1 pb-10">
              {moreNav.map(({ href, label, icon: Icon, badge }) => {
                const active = pathname?.startsWith(href);
                return (
                  <Link
                    key={href}
                    href={href}
                    className={`flex items-center gap-4 px-4 py-3.5 rounded-2xl text-sm font-medium ${
                      active ? "bg-brand-50 text-brand-700" : "text-slate-700 hover:bg-slate-50"
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
                    <span>{label}</span>
                  </Link>
                );
              })}

              {/* Language selector */}
              <div className="mt-1 pt-3 border-t border-slate-100">
                <div className="flex items-center gap-3 px-4 py-2">
                  <Globe className="w-5 h-5 text-slate-400 shrink-0" />
                  <div className="flex gap-2 flex-wrap">
                    {LOCALES.map((l) => (
                      <button
                        key={l.code}
                        onClick={() => { setLocale(l.code); setMoreOpen(false); }}
                        className={`flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-sm font-medium transition ${
                          l.code === locale
                            ? "bg-brand-50 text-brand-700"
                            : "text-slate-500 hover:bg-slate-50"
                        }`}
                      >
                        <span>{l.flag}</span>
                        <span>{l.label}</span>
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          </div>
        </>
      )}
    </>
  );
}
