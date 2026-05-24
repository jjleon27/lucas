"use client";
import { useState, useRef } from "react";
import { UploadCloud } from "lucide-react";
import { useT } from "@/lib/i18n";

interface Props {
  onFile: (file: File) => void | Promise<void>;
  loading?: boolean;
}

export default function UploadZone({ onFile, loading }: Props) {
  const ref = useRef<HTMLInputElement>(null);
  const [drag, setDrag] = useState(false);
  const { t } = useT();

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        setDrag(true);
      }}
      onDragLeave={() => setDrag(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDrag(false);
        const f = e.dataTransfer.files?.[0];
        if (f) void onFile(f);
      }}
      onClick={() => ref.current?.click()}
      className={`card cursor-pointer text-center transition border-dashed ${
        drag ? "border-brand-500 bg-brand-50" : "border-slate-200"
      }`}
    >
      <input
        ref={ref}
        type="file"
        accept="image/*,application/pdf,.pdf"
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) void onFile(f);
        }}
      />
      <div className="flex flex-col items-center gap-3 py-8">
        <div className="rounded-full bg-brand-50 p-4">
          <UploadCloud className="w-8 h-8 text-brand-600" />
        </div>
        <div className="text-lg font-medium">
          {loading ? t("upload.reading") : t("upload.dropzone")}
        </div>
        <p className="text-sm text-slate-500 max-w-sm">{t("upload.dropzoneHint")}</p>
      </div>
    </div>
  );
}
