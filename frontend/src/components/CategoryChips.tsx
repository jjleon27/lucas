"use client";
/**
 * Horizontal scrollable chips for category selection.
 * Replaces a dropdown — picking a category is now one tap.
 */
import { CATEGORIES, CATEGORY_META, type Category } from "@/lib/categories";
import { useT } from "@/lib/i18n";

interface Props {
  value: string;
  onChange: (c: Category) => void;
}

export default function CategoryChips({ value, onChange }: Props) {
  const { locale } = useT();
  return (
    <div className="-mx-1 flex gap-1.5 overflow-x-auto pb-1 snap-x snap-mandatory">
      {CATEGORIES.map((c) => {
        const meta = CATEGORY_META[c];
        const active = value === c;
        const label = meta[locale] || meta.en;
        return (
          <button
            type="button"
            key={c}
            onClick={() => onChange(c)}
            className={`snap-start shrink-0 px-3 py-1.5 rounded-full border text-sm flex items-center gap-1.5 transition ${
              active
                ? "bg-brand-600 text-white border-brand-600 shadow-soft"
                : "bg-white text-slate-700 border-slate-200 hover:border-brand-400"
            }`}
          >
            <span aria-hidden>{meta.icon}</span>
            <span>{label}</span>
          </button>
        );
      })}
    </div>
  );
}
