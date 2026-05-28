import { useT } from "@/lib/i18n";

interface Props {
  label: string;
  value: string;
  hint?: string;
  tone?: "good" | "warning" | "danger" | "default";
}

const toneMap = {
  good: "bg-brand-50 text-brand-700",
  warning: "bg-amber-50 text-amber-700",
  danger: "bg-rose-50 text-rose-700",
  default: "bg-slate-50 text-slate-700",
} as const;

export default function StatCard({ label, value, hint, tone = "default" }: Props) {
  const { t } = useT();
  return (
    <div className="card">
      <div className="flex items-center justify-between">
        <span className="text-xs uppercase tracking-wide text-slate-500">{label}</span>
        {tone !== "default" && (
          <span className={`text-xs px-2 py-0.5 rounded-full ${toneMap[tone]}`}>
            {t(`status.${tone}`)}
          </span>
        )}
      </div>
      <div className="mt-2 text-xl font-semibold tracking-tight truncate min-w-0">{value}</div>
      {hint && <div className="mt-1 text-xs text-slate-500">{hint}</div>}
    </div>
  );
}
