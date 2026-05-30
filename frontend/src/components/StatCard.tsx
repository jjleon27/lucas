import { useT } from "@/lib/i18n";

interface Props {
  label: string;
  value: string;
  hint?: string;
  tone?: "good" | "warning" | "danger" | "default";
  href?: string;
}

const toneMap = {
  good: "bg-brand-50 text-brand-700",
  warning: "bg-amber-50 text-amber-700",
  danger: "bg-rose-50 text-rose-700",
  default: "bg-slate-50 text-slate-700",
} as const;

export default function StatCard({ label, value, hint, tone = "default", href }: Props) {
  const { t } = useT();
  const inner = (
    <>
      <div className="flex items-center justify-between gap-1 min-w-0">
        <span className="text-xs uppercase tracking-wide text-slate-500 truncate">{label}</span>
        {tone !== "default" && (
          <span className={`text-xs px-1.5 py-0.5 rounded-full shrink-0 ${toneMap[tone]}`}>
            {t(`status.${tone}`)}
          </span>
        )}
        {href && (
          <span className="text-slate-300 shrink-0 text-xs">→</span>
        )}
      </div>
      <div className="mt-1.5 text-sm sm:text-base font-semibold tracking-tight truncate">{value}</div>
      {hint && <div className="mt-0.5 text-[11px] sm:text-xs text-slate-500 truncate">{hint}</div>}
    </>
  );

  if (href) {
    return (
      <a href={href} className="card overflow-hidden min-w-0 block hover:shadow-md transition-shadow active:scale-[0.98]">
        {inner}
      </a>
    );
  }
  return <div className="card overflow-hidden min-w-0">{inner}</div>;
}
