/**
 * Category hints + icons.
 *
 * Two layers of "smart defaults" so users rarely have to pick a category:
 *   1. History  — if the user has already saved a transaction at this merchant,
 *      reuse the most common category they assigned to it.
 *   2. Built-in regex bank — well-known Chilean / LatAm merchants get a
 *      default category out of the box (Líder → Groceries, Uber → Transport,
 *      Netflix → Subscriptions, etc.).
 *
 * Only call `learnFromHistory(transactions)` once after listTransactions().
 */

import type { Transaction } from "./api";

export const CATEGORIES = [
  "Food", "Groceries", "Transport", "Shopping",
  "Entertainment", "Bills", "Health", "Travel",
  "Subscriptions", "Other",
] as const;

export type Category = typeof CATEGORIES[number];

/** Visual icon + Spanish label per category. */
export const CATEGORY_META: Record<Category, { icon: string; es: string; en: string; pt: string }> = {
  Food:          { icon: "🍔", es: "Comida",        en: "Food",          pt: "Comida" },
  Groceries:     { icon: "🛒", es: "Supermercado",  en: "Groceries",     pt: "Mercado" },
  Transport:     { icon: "🚗", es: "Transporte",    en: "Transport",     pt: "Transporte" },
  Shopping:      { icon: "🛍️", es: "Compras",       en: "Shopping",      pt: "Compras" },
  Entertainment: { icon: "🎬", es: "Entretención",  en: "Entertainment", pt: "Lazer" },
  Bills:         { icon: "📄", es: "Cuentas",       en: "Bills",         pt: "Contas" },
  Health:        { icon: "💊", es: "Salud",         en: "Health",        pt: "Saúde" },
  Travel:        { icon: "✈️", es: "Viajes",        en: "Travel",        pt: "Viagens" },
  Subscriptions: { icon: "📺", es: "Suscripciones", en: "Subscriptions", pt: "Assinaturas" },
  Other:         { icon: "•",  es: "Otro",          en: "Other",         pt: "Outro" },
};

/** Built-in merchant rules — first match wins. */
const MERCHANT_RULES: Array<[RegExp, Category]> = [
  // Supermercados (CL/LatAm)
  [/\b(l[ií]der|jumbo|tottus|santa\s?isabel|unimarc|ekono|acuenta|walmart|carrefour|p[aã]o\s?de\s?a[çc][uú]car)\b/i, "Groceries"],
  // Comida rápida / restaurantes
  [/\b(mc\s?donald|kfc|burger|pizza|domin|subway|starbucks|juan\s?valdez|tel\s?pizza|papa\s?john|sushi|doggis|bambu|melt|chilango|fuente\s?alemana|emporio)\b/i, "Food"],
  // Suscripciones
  [/\b(netflix|spotify|disney|hbo|max|amazon\s?prime|youtube\s?premium|apple\s?music|apple\.com\/bill|icloud|google\s?one|dropbox|chatgpt|openai|claude|anthropic|github|figma|notion|adobe|canva)\b/i, "Subscriptions"],
  // Salud
  [/\b(farmacia(s)?\s?(ahumada|cruz\s?verde|salcobrand|knop|dr\.?\s?simi|del\s?dr)|isapre|fonasa|hospital|cl[ií]nica)\b/i, "Health"],
  // Transporte / combustible
  [/\b(uber|cabify|didi|beat|metro|metrobus|transantiago|red\s?metropolitana|bip|copec|shell|petrobras|enex|esso|terpel|ypf)\b/i, "Transport"],
  // Tiendas / retail
  [/\b(falabella|ripley|paris|hites|abc\s?din|sodimac|easy|construmart|h&m|zara|forever\s?21|nike|adidas|north\s?face)\b/i, "Shopping"],
  // Cuentas / utilities
  [/\b(aguas\s?andinas|enel|metrogas|cge|movistar|entel|claro|wom|vtr|gtd|mundo|directv|dgt)\b/i, "Bills"],
  // Entretención
  [/\b(cinemark|cineplanet|hoyts|cine\s?hoyts|cine\s?colon|estadio|teatro|spotify|prime\s?video|playstation|steam|nintendo|xbox)\b/i, "Entertainment"],
  // Viajes
  [/\b(latam|sky\s?airline|jetsmart|aerol[ií]neas|airbnb|booking\.com|despegar|expedia|hotels\.com|hertz|avis|sixt|trivago|kayak)\b/i, "Travel"],
];

/** History map merchant_name (lowercase, normalised) → category */
let historyMap: Map<string, Category> = new Map();

function normMerchant(s: string): string {
  return (s || "").toLowerCase().trim().replace(/\s+/g, " ");
}

/** Build the merchant → category map from past saved transactions. */
export function learnFromHistory(txs: Transaction[]): void {
  const counter = new Map<string, Map<Category, number>>();
  for (const tx of txs) {
    const key = normMerchant(tx.merchant);
    if (!key || !CATEGORIES.includes(tx.category as Category)) continue;
    const inner = counter.get(key) ?? new Map<Category, number>();
    inner.set(tx.category as Category, (inner.get(tx.category as Category) ?? 0) + 1);
    counter.set(key, inner);
  }
  historyMap = new Map();
  for (const [merchant, byCat] of counter.entries()) {
    let best: [Category, number] | null = null;
    for (const [cat, n] of byCat.entries()) {
      if (!best || n > best[1]) best = [cat, n];
    }
    if (best) historyMap.set(merchant, best[0]);
  }
}

/**
 * Suggest a category for a merchant. Returns null if no confident guess.
 * Order: history > built-in rules.
 */
export function suggestCategory(merchant: string): Category | null {
  const key = normMerchant(merchant);
  if (!key) return null;
  if (historyMap.has(key)) return historyMap.get(key)!;
  for (const [re, cat] of MERCHANT_RULES) {
    if (re.test(merchant)) return cat;
  }
  return null;
}
