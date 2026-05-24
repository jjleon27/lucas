"use client";
/**
 * Sticky preview of the uploaded screenshot.
 * - Desktop: sits in a sidebar so the user can compare while they edit.
 * - Mobile: collapses into a tap-to-expand thumbnail; full-screen lightbox on tap.
 */
import { useState } from "react";
import { Maximize2, X } from "lucide-react";
import { resolveImageUrl } from "@/lib/api";

interface Props {
  imageUrl: string;
}

export default function ImagePreview({ imageUrl }: Props) {
  const [open, setOpen] = useState(false);
  const url = resolveImageUrl(imageUrl);
  if (!url) return null;

  return (
    <>
      <div className="card p-2 lg:sticky lg:top-4 lg:self-start">
        <div className="relative">
          <img
            src={url}
            alt="Pantallazo subido"
            className="w-full rounded-lg max-h-[70vh] object-contain bg-slate-50 cursor-zoom-in"
            onClick={() => setOpen(true)}
          />
          <button
            type="button"
            onClick={() => setOpen(true)}
            className="absolute top-2 right-2 p-1.5 rounded-lg bg-black/50 text-white hover:bg-black/70"
            aria-label="Ampliar"
          >
            <Maximize2 className="w-4 h-4" />
          </button>
        </div>
      </div>

      {open && (
        <div
          className="fixed inset-0 z-50 bg-black/85 flex items-center justify-center p-4"
          onClick={() => setOpen(false)}
        >
          <button
            className="absolute top-4 right-4 p-2 rounded-full bg-white/20 text-white hover:bg-white/30"
            onClick={() => setOpen(false)}
            aria-label="Cerrar"
          >
            <X className="w-5 h-5" />
          </button>
          <img
            src={url}
            alt="Pantallazo subido"
            className="max-w-full max-h-full object-contain rounded-lg"
            onClick={(e) => e.stopPropagation()}
          />
        </div>
      )}
    </>
  );
}
