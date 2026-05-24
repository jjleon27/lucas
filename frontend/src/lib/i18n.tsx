"use client";
/**
 * Tiny home-grown i18n.
 * - Default locale: Spanish ("es"). Pick English with the language toggle.
 * - Stores the user choice in localStorage.
 * - No dependency on next-intl / i18next so the bundle stays small.
 *
 * Usage:
 *   const { t, locale, setLocale } = useT();
 *   t("dashboard.title")            // "Resumen"
 *   t("greet.hello", { name })     // interpolation
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  ReactNode,
} from "react";

export type Locale = "es" | "en" | "pt";

export const LOCALES: { code: Locale; label: string; flag: string }[] = [
  { code: "es", label: "Español", flag: "🇨🇱" },
  { code: "en", label: "English", flag: "🇺🇸" },
  { code: "pt", label: "Português", flag: "🇧🇷" },
];

// ---------- translation tables ----------
type Dict = Record<string, string>;

const es: Dict = {
  // Branding / tagline
  "app.tagline": "Tus lucas, bajo control.",
  "app.subtitle": "Tu asistente financiero con IA.",

  // Auth
  "auth.quickEntry": "Entrada rápida",
  "auth.emailPassword": "Email y contraseña",
  "auth.enterInstantly": "Entrar al toque →",
  "auth.noPassword": "Sin contraseña — solo tu email y listo.",
  "auth.login": "Ingresar",
  "auth.signup": "Crear cuenta",
  "auth.email": "Email",
  "auth.password": "Contraseña (mín. 8 caracteres)",
  "auth.continueEmail": "o continúa con email",
  "auth.terms": "Al continuar aceptas nuestros Términos. Nunca vendemos tus datos.",

  // Nav
  "nav.dashboard": "Resumen",
  "nav.chat": "Hablar con Lucas",
  "nav.upload": "Subir",
  "nav.transactions": "Movimientos",
  "nav.split": "Dividir cuenta",
  "nav.accounts": "Tarjetas y cuentas",
  "nav.logout": "Cerrar sesión",
  "nav.language": "Idioma",

  // Accounts
  "accounts.title": "Tarjetas y cuentas",
  "accounts.subtitle": "Llevá el saldo de cada tarjeta y cuenta. Subí pantallazos y la app las mantiene al día.",
  "accounts.add": "+ Agregar cuenta",
  "accounts.empty": "Aún no agregaste ninguna cuenta. Crea una para empezar a llevar tus saldos.",
  "accounts.name": "Nombre",
  "accounts.namePlaceholder": "Ej: Santander Débito",
  "accounts.bank": "Banco",
  "accounts.type": "Tipo",
  "accounts.type.debit": "Débito",
  "accounts.type.credit": "Crédito",
  "accounts.type.savings": "Ahorro",
  "accounts.type.wallet": "Billetera",
  "accounts.type.cash": "Efectivo",
  "accounts.creditLimit": "Cupo total",
  "accounts.anchorDate": "Fecha del saldo conocido",
  "accounts.anchorBalance": "Saldo conocido",
  "accounts.anchorHelp": "Pone el saldo que ves en tu banco hoy. La app suma/resta los movimientos a partir de esa fecha.",
  "accounts.anchorHelpCredit": "Pone lo que debes hoy en tu tarjeta. La app suma compras y resta pagos a partir de esa fecha.",
  "accounts.color": "Color",
  "accounts.save": "Guardar",
  "accounts.cancel": "Cancelar",
  "accounts.delete": "Eliminar",
  "accounts.confirmDelete": "¿Eliminar esta cuenta? Los movimientos quedan sin cuenta asignada.",
  "accounts.balance": "Saldo",
  "accounts.used": "Usado",
  "accounts.available": "Disponible",
  "accounts.limit": "Cupo",
  "accounts.pickAccount": "¿De qué cuenta es este pantallazo?",
  "accounts.pickAccountHint": "Sin cuenta no se puede llevar el saldo. Podés crear una rápido aquí.",
  "accounts.transfersPending": "{count} pago(s) de tarjeta sin enlazar",
  "accounts.reviewTransfers": "Revisar",

  // Dashboard
  "dashboard.title": "Resumen",
  "dashboard.currency": "Moneda",
  "dashboard.monthlyBudget": "Presupuesto mensual",
  "dashboard.spentThisMonth": "Gastado este mes",
  "dashboard.ofBudget": "de {budget} de presupuesto",
  "dashboard.noBudgetSet": "sin presupuesto",
  "dashboard.remaining": "Disponible",
  "dashboard.leftInBudget": "queda en el mes",
  "dashboard.safeDaily": "Gasto diario seguro",
  "dashboard.toStayOnTrack": "para mantenerte en línea",
  "dashboard.projectedEOM": "Proyección fin de mes",
  "dashboard.categoryBreakdown": "Gastos por categoría",
  "dashboard.noSpending": "Aún no hay gastos este mes.",
  "dashboard.quickActions": "Acciones rápidas",
  "dashboard.uploadScreenshot": "Subir un pantallazo",
  "dashboard.askLucas": "Preguntarle a Lucas",
  "dashboard.splitBill": "Dividir una cuenta",
  // Income / variable budget
  "dashboard.incomeSection": "Ingresos del mes",
  "dashboard.incomeActual": "Recibido",
  "dashboard.incomeTarget": "Meta del mes",
  "dashboard.incomeTargetPlaceholder": "¿Cuánto esperas ganar?",
  "dashboard.incomeProgress": "{pct}% recibido de la meta",
  "dashboard.safeDailyActual": "Gasto seguro HOY",
  "dashboard.safeDailyProjected": "Gasto seguro (meta)",
  "dashboard.safeDailyActualHint": "basado en lo que ya recibiste",
  "dashboard.safeDailyProjectedHint": "asumiendo que se cumple la meta",
  "dashboard.suggestFromHistory": "Sugerir según historial",
  "dashboard.historicalHint": "Promedio últimos 3 meses: {amount}",
  "dashboard.noIncomeHistory": "Sin historial de ingresos aún",
  "dashboard.daysLeft": "{n} días restantes",
  "dashboard.editTarget": "Editar meta",
  "dashboard.saveTarget": "Guardar",
  "dashboard.targetSaved": "¡Guardado!",

  // Status chips
  "status.good": "al día",
  "status.warning": "ojo",
  "status.danger": "excedido",

  // Upload
  "upload.title": "Subir un pantallazo",
  "upload.subtitle":
    "Recibo, confirmación bancaria o resumen de tarjeta — Lucas lo lee y crea los movimientos.",
  "upload.dropzone": "Arrastra imagen o PDF, o toca para subir",
  "upload.reading": "Leyendo archivo…",
  "upload.dropzoneHint":
    "Boletas, confirmaciones bancarias, cartolas o resúmenes de tarjeta — imagen o PDF. Lucas extrae los datos al tiro.",
  "upload.confirmTitle": "Confirma los datos",
  "upload.multiTitle": "Detecté {count} movimientos",
  "upload.multiSubtitle":
    "Destilda los que no quieras importar y edítalos si hace falta.",
  "upload.merchant": "Comercio",
  "upload.amount": "Monto",
  "upload.date": "Fecha",
  "upload.category": "Categoría",
  "upload.type": "Tipo",
  "upload.expense": "Gasto",
  "upload.income": "Ingreso",
  "upload.lineItems": "Productos",
  "upload.discard": "Descartar",
  "upload.save": "Guardar",
  "upload.saveAll": "Guardar {count}",
  "upload.saving": "Guardando…",
  "upload.uploadFailed": "No se pudo subir",
  "upload.saveFailed": "No se pudo guardar",
  "upload.preview": "Pantallazo",
  "upload.looksGood": "✓ Está correcto, guardar",
  "upload.needsReview": "Revisar",
  "upload.totalMatches": "Los productos suman el total ✓",
  "upload.totalMismatch": "Los productos no suman el total ({sum} vs {total})",
  "upload.itemsCount": "{count} productos",
  "upload.hideItems": "Ocultar productos",
  "upload.showItems": "Ver productos",
  "upload.smartCategory": "Categoría sugerida",

  // Transactions
  "tx.title": "Movimientos",
  "tx.add": "+ Agregar",
  "tx.empty": "Sin movimientos aún. Sube tu primer pantallazo para empezar.",
  "tx.date": "Fecha",
  "tx.merchant": "Comercio",
  "tx.category": "Categoría",
  "tx.amount": "Monto",
  "tx.loading": "Cargando…",
  "tx.pending.title": "Pagos de tarjeta sin enlazar",
  "tx.pending.subtitle": "Estos parecen pagos de tarjeta crédito, pero no encontramos su cargo en la cuenta débito. Enlázalos para que no inflen tu saldo.",
  "tx.pending.none": "¡Todo al día! No hay pagos de tarjeta pendientes por enlazar.",
  "tx.pending.clearFilter": "Ver todos los movimientos",
  "tx.pending.link": "Enlazar",
  "tx.pending.searching": "Buscando coincidencias…",
  "tx.pending.noMatch": "No encontramos un movimiento que calce. Puedes crear el cargo faltante manualmente desde Subir.",
  "tx.pending.pickMatch": "Elige el otro lado del movimiento:",
  "tx.pending.cancel": "Cancelar",
  "tx.pending.linked": "Enlazado ✓",
  "tx.edit": "Editar",
  "tx.delete": "Eliminar",
  "tx.deleteConfirm": "¿Eliminar este movimiento?",
  "tx.save": "Guardar",
  "tx.cancel": "Cancelar",
  "tx.duplicateWarning": "¡Atención! Este movimiento parece duplicado.",
  "tx.notes": "Notas",

  // Chat
  "chat.title": "Hablar con Lucas",
  "chat.placeholder": "Pregúntale sobre tus finanzas…",
  "chat.thinking": "Lucas está pensando…",
  "chat.welcome":
    "Hola, soy Lucas. Pregúntame lo que quieras sobre tus gastos — por ejemplo: \"¿cuánto gasté en comida este mes?\" o \"¿voy bien con mi presupuesto?\"",
  "chat.voiceUnsupported": "Tu navegador no soporta entrada por voz todavía.",

  // Split v2
  "split.title": "Dividir cuenta",
  "split.stepPeople": "Participantes",
  "split.stepItems": "Asignar gastos",
  "split.stepSettle": "Liquidar",
  "split.addPerson": "Nombre…",
  "split.youLabel": "Yo",
  "split.uploadReceipt": "Subir boleta",
  "split.orManual": "o ingresa monto manualmente",
  "split.manualAmount": "Monto total",
  "split.manualMerchant": "Descripción (restaurante, etc.)",
  "split.manualStart": "Comenzar división",
  "split.assignHint": "Toca los avatares en cada ítem para asignar quién paga",
  "split.splitEqual": "Partes iguales",
  "split.splitPercent": "Porcentaje",
  "split.splitAmount": "Monto exacto",
  "split.adjustSplit": "Ajustar división",
  "split.done": "Listo",
  "split.unassigned": "Sin asignar",
  "split.totals": "Resumen",
  "split.whoPayd": "¿Quién pagó la cuenta?",
  "split.paidByMe": "Yo pagué",
  "split.paidBySomeone": "Pagó {name}",
  "split.deductFromAccount": "Descontar de cuenta",
  "split.saveToLucas": "Guardar en Lucas",
  "split.settleSummary": "Liquidación",
  "split.owesYou": "{name} te debe",
  "split.youOwe": "Debes a {name}",
  "split.myShare": "Mi parte",
  "split.splitAnother": "Dividir otra cuenta",
  "split.progress": "Progreso",
  "split.items": "Ítems",
  "split.noItems": "Sin ítems — sube una boleta o ingresa el monto.",
  "split.complete": "¡Asignación completa!",
  "split.percentSum": "Los porcentajes deben sumar 100%",

  // Lucas FAB (floating action button)
  "fab.placeholder": "¿Qué quieres hacer? Escribe o habla…",
  "fab.hint": "Ej: «Gasté $5000 en almuerzo» · «Divide $30.000 entre Pedro y yo» · «¿Cuánto llevo gastado?»",
  "fab.listening": "Escuchando…",
  "fab.thinking": "Lucas está pensando…",
  "fab.label": "Hablar con Lucas",
  "fab.close": "Cerrar",
};

const en: Dict = {
  "app.tagline": "Your money, under control.",
  "app.subtitle": "Your AI finance assistant.",

  "auth.quickEntry": "Quick entry",
  "auth.emailPassword": "Email + password",
  "auth.enterInstantly": "Enter instantly →",
  "auth.noPassword": "No password needed — just your email to jump in.",
  "auth.login": "Log in",
  "auth.signup": "Sign up",
  "auth.email": "Email",
  "auth.password": "Password (min 8 chars)",
  "auth.continueEmail": "or continue with email",
  "auth.terms": "By continuing you agree to our Terms. We'll never sell your data.",

  "nav.dashboard": "Dashboard",
  "nav.chat": "Chat with Lucas",
  "nav.upload": "Upload",
  "nav.transactions": "Transactions",
  "nav.split": "Split a bill",
  "nav.accounts": "Cards & accounts",
  "nav.logout": "Log out",
  "nav.language": "Language",

  "accounts.title": "Cards & accounts",
  "accounts.subtitle": "Track the balance of each card and account. Upload screenshots and LUCAS keeps them in sync.",
  "accounts.add": "+ Add account",
  "accounts.empty": "You haven't added any accounts yet. Create one to start tracking balances.",
  "accounts.name": "Name",
  "accounts.namePlaceholder": "e.g. Santander Debit",
  "accounts.bank": "Bank",
  "accounts.type": "Type",
  "accounts.type.debit": "Debit",
  "accounts.type.credit": "Credit",
  "accounts.type.savings": "Savings",
  "accounts.type.wallet": "Wallet",
  "accounts.type.cash": "Cash",
  "accounts.creditLimit": "Credit limit",
  "accounts.anchorDate": "Date of known balance",
  "accounts.anchorBalance": "Known balance",
  "accounts.anchorHelp": "Enter the balance you see in your bank today. LUCAS adds/subtracts movements from that date forward.",
  "accounts.anchorHelpCredit": "Enter how much you owe today on this card. LUCAS adds purchases and subtracts payments from that date forward.",
  "accounts.color": "Color",
  "accounts.save": "Save",
  "accounts.cancel": "Cancel",
  "accounts.delete": "Delete",
  "accounts.confirmDelete": "Delete this account? Existing transactions will be unassigned.",
  "accounts.balance": "Balance",
  "accounts.used": "Used",
  "accounts.available": "Available",
  "accounts.limit": "Limit",
  "accounts.pickAccount": "Which account is this screenshot from?",
  "accounts.pickAccountHint": "Without an account we can't track balances. You can create one quickly here.",
  "accounts.transfersPending": "{count} card payment(s) need linking",
  "accounts.reviewTransfers": "Review",

  "dashboard.title": "Dashboard",
  "dashboard.currency": "Currency",
  "dashboard.monthlyBudget": "Monthly budget",
  "dashboard.spentThisMonth": "Spent this month",
  "dashboard.ofBudget": "of {budget} budget",
  "dashboard.noBudgetSet": "no budget set",
  "dashboard.remaining": "Remaining",
  "dashboard.leftInBudget": "left in budget",
  "dashboard.safeDaily": "Safe daily spend",
  "dashboard.toStayOnTrack": "to stay on track",
  "dashboard.projectedEOM": "Projected end-of-month",
  "dashboard.categoryBreakdown": "Category breakdown",
  "dashboard.noSpending": "No spending yet this month.",
  "dashboard.quickActions": "Quick actions",
  "dashboard.uploadScreenshot": "Upload a screenshot",
  "dashboard.askLucas": "Ask Lucas a question",
  "dashboard.splitBill": "Split a bill",
  // Income / variable budget
  "dashboard.incomeSection": "This month's income",
  "dashboard.incomeActual": "Received",
  "dashboard.incomeTarget": "Monthly target",
  "dashboard.incomeTargetPlaceholder": "How much do you expect to earn?",
  "dashboard.incomeProgress": "{pct}% of target received",
  "dashboard.safeDailyActual": "Safe spend TODAY",
  "dashboard.safeDailyProjected": "Safe spend (target)",
  "dashboard.safeDailyActualHint": "based on income already received",
  "dashboard.safeDailyProjectedHint": "assuming your full target comes in",
  "dashboard.suggestFromHistory": "Suggest from history",
  "dashboard.historicalHint": "3-month avg: {amount}",
  "dashboard.noIncomeHistory": "No income history yet",
  "dashboard.daysLeft": "{n} days left",
  "dashboard.editTarget": "Edit target",
  "dashboard.saveTarget": "Save",
  "dashboard.targetSaved": "Saved!",

  "status.good": "on track",
  "status.warning": "watch",
  "status.danger": "over",

  "upload.title": "Upload a screenshot",
  "upload.subtitle":
    "Bank confirmation, receipt, or statement — LUCAS reads it and creates the transactions.",
  "upload.dropzone": "Drop image or PDF, or tap to upload",
  "upload.reading": "Reading file…",
  "upload.dropzoneHint":
    "Receipts, bank statements, or card summaries — image or PDF. LUCAS extracts the details automatically.",
  "upload.confirmTitle": "Confirm the details",
  "upload.multiTitle": "I detected {count} transactions",
  "upload.multiSubtitle": "Uncheck any you don't want to import. Edit if needed.",
  "upload.merchant": "Merchant",
  "upload.amount": "Amount",
  "upload.date": "Date",
  "upload.category": "Category",
  "upload.type": "Type",
  "upload.expense": "Expense",
  "upload.income": "Income",
  "upload.lineItems": "Line items",
  "upload.discard": "Discard",
  "upload.save": "Save",
  "upload.saveAll": "Save {count}",
  "upload.saving": "Saving…",
  "upload.uploadFailed": "Upload failed",
  "upload.saveFailed": "Failed to save",
  "upload.preview": "Screenshot",
  "upload.looksGood": "✓ Looks good, save",
  "upload.needsReview": "Needs review",
  "upload.totalMatches": "Items add up to the total ✓",
  "upload.totalMismatch": "Items don't add up to the total ({sum} vs {total})",
  "upload.itemsCount": "{count} items",
  "upload.hideItems": "Hide items",
  "upload.showItems": "Show items",
  "upload.smartCategory": "Suggested category",

  "tx.title": "Transactions",
  "tx.add": "+ Add",
  "tx.empty": "No transactions yet. Upload your first screenshot to start.",
  "tx.date": "Date",
  "tx.merchant": "Merchant",
  "tx.category": "Category",
  "tx.amount": "Amount",
  "tx.loading": "Loading…",
  "tx.pending.title": "Unlinked credit-card payments",
  "tx.pending.subtitle": "These look like CC payments, but we couldn't find the matching charge on your debit account. Link them so they don't inflate your balance.",
  "tx.pending.none": "All clear — no pending transfers to link.",
  "tx.pending.clearFilter": "Show all transactions",
  "tx.pending.link": "Link",
  "tx.pending.searching": "Looking for matches…",
  "tx.pending.noMatch": "No matching transaction found. You can add the missing charge manually from Upload.",
  "tx.pending.pickMatch": "Pick the other side of the transfer:",
  "tx.pending.cancel": "Cancel",
  "tx.pending.linked": "Linked ✓",
  "tx.edit": "Edit",
  "tx.delete": "Delete",
  "tx.deleteConfirm": "Delete this transaction?",
  "tx.save": "Save",
  "tx.cancel": "Cancel",
  "tx.duplicateWarning": "Heads up — this looks like a duplicate!",
  "tx.notes": "Notes",

  "chat.title": "Chat with Lucas",
  "chat.placeholder": "Ask about your finances…",
  "chat.thinking": "Lucas is thinking…",
  "chat.welcome":
    "Hi, I'm Lucas. Ask me anything about your spending — e.g. \"how much did I spend on food this month?\" or \"am I on budget?\"",
  "chat.voiceUnsupported": "Voice input isn't supported in this browser yet.",

  // Split v2
  "split.title": "Split a bill",
  "split.stepPeople": "People",
  "split.stepItems": "Assign items",
  "split.stepSettle": "Settle up",
  "split.addPerson": "Name…",
  "split.youLabel": "Me",
  "split.uploadReceipt": "Upload receipt",
  "split.orManual": "or enter amount manually",
  "split.manualAmount": "Total amount",
  "split.manualMerchant": "Description (restaurant, etc.)",
  "split.manualStart": "Start splitting",
  "split.assignHint": "Tap the avatars on each item to assign who pays",
  "split.splitEqual": "Equal split",
  "split.splitPercent": "Percentage",
  "split.splitAmount": "Exact amount",
  "split.adjustSplit": "Adjust split",
  "split.done": "Done",
  "split.unassigned": "Unassigned",
  "split.totals": "Summary",
  "split.whoPayd": "Who paid?",
  "split.paidByMe": "I paid",
  "split.paidBySomeone": "{name} paid",
  "split.deductFromAccount": "Deduct from account",
  "split.saveToLucas": "Save to Lucas",
  "split.settleSummary": "Settlement",
  "split.owesYou": "{name} owes you",
  "split.youOwe": "You owe {name}",
  "split.myShare": "My share",
  "split.splitAnother": "Split another",
  "split.progress": "Progress",
  "split.items": "Items",
  "split.noItems": "No items — upload a receipt or enter the total.",
  "split.complete": "All items assigned!",
  "split.percentSum": "Percentages must add up to 100%",

  // Lucas FAB
  "fab.placeholder": "What do you want to do? Type or speak…",
  "fab.hint": "e.g. «I spent $50 on lunch» · «Split $120 with Pedro and María» · «How much have I spent?»",
  "fab.listening": "Listening…",
  "fab.thinking": "Lucas is thinking…",
  "fab.label": "Talk to Lucas",
  "fab.close": "Close",
};

const pt: Dict = {
  ...en, // pragmatic fallback — translate later
  "app.tagline": "Seu dinheiro, sob controle.",
  "app.subtitle": "Seu assistente financeiro com IA.",
  "auth.quickEntry": "Entrada rápida",
  "auth.enterInstantly": "Entrar agora →",
  "nav.dashboard": "Painel",
  "nav.upload": "Enviar",
  "nav.transactions": "Transações",
  "nav.split": "Dividir conta",
  "nav.chat": "Falar com Lucas",
};

const DICTS: Record<Locale, Dict> = { es, en, pt };

// ---------- context ----------
interface I18nCtx {
  locale: Locale;
  setLocale: (l: Locale) => void;
  t: (key: string, vars?: Record<string, string | number>) => string;
}

const Ctx = createContext<I18nCtx | null>(null);

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>("es");

  // Hydrate from localStorage on mount.
  useEffect(() => {
    try {
      const saved = window.localStorage.getItem("lucas_locale") as Locale | null;
      if (saved && DICTS[saved]) {
        setLocaleState(saved);
      }
      // If no saved preference, stay in Spanish (Chilean app default).
      // We don't auto-detect browser language to avoid defaulting to English
      // when the device is in English but the user is Chilean.
    } catch {
      /* ignore */
    }
  }, []);

  const setLocale = useCallback((l: Locale) => {
    setLocaleState(l);
    try {
      window.localStorage.setItem("lucas_locale", l);
    } catch {
      /* ignore */
    }
  }, []);

  const t = useCallback(
    (key: string, vars?: Record<string, string | number>) => {
      const dict = DICTS[locale] || DICTS.es;
      let str = dict[key] ?? DICTS.es[key] ?? key;
      if (vars) {
        for (const [k, v] of Object.entries(vars)) {
          str = str.replace(new RegExp(`\\{${k}\\}`, "g"), String(v));
        }
      }
      return str;
    },
    [locale],
  );

  const value = useMemo(() => ({ locale, setLocale, t }), [locale, setLocale, t]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useT(): I18nCtx {
  const v = useContext(Ctx);
  if (!v) {
    // Safe fallback for server render / tests.
    return {
      locale: "es",
      setLocale: () => {},
      t: (k) => DICTS.es[k] ?? k,
    };
  }
  return v;
}

/**
 * Format a money amount respecting the user's locale.
 * Chilean pesos (CLP) have no decimals; most others use 2.
 */
export function formatMoney(
  value: number,
  currency: string = "USD",
  locale?: Locale,
): string {
  const l = locale || ((typeof window !== "undefined" && (window.localStorage.getItem("lucas_locale") as Locale)) || "es");
  // Use comma-based thousands separator for all locales to be consistent.
  // es-MX and en-US both use "," for thousands and "." for decimals,
  // which is more familiar than Chile's dot-based notation (es-CL).
  const intlLocale = l === "pt" ? "pt-BR" : "en-US";
  const decimals = currency.toUpperCase() === "CLP" ? 0 : 2;
  try {
    return new Intl.NumberFormat(intlLocale, {
      style: "currency",
      currency,
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    }).format(value);
  } catch {
    return `${currency} ${value.toFixed(decimals)}`;
  }
}
