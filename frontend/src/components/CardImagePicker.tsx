"use client";
/**
 * CardImagePicker — lets users choose a visual identity for their card:
 *  1. Preset bank card designs (SVG, accurate colors/name)
 *  2. Upload a photo of the real card (physical or virtual screenshot)
 *
 * Emits onSelect(value) where value is either:
 *  - A preset key like "preset:santander_credit"
 *  - An image URL returned by the backend after upload
 */
import { useRef, useState } from "react";
import { Camera, Check } from "lucide-react";
import { resolveImageUrl } from "@/lib/api";

// ── Bank presets ─────────────────────────────────────────────────────────────
export interface CardPreset {
  key: string;
  label: string;
  bg: string;       // CSS gradient or color
  textColor: string;
  chipColor: string;
  logo: string;     // short display name on card
}

export const CARD_PRESETS: CardPreset[] = [
  {
    key: "santander_red",
    label: "Santander",
    bg: "linear-gradient(135deg, #EC0000 0%, #9B0000 100%)",
    textColor: "#fff",
    chipColor: "rgba(255,255,255,0.3)",
    logo: "SANTANDER",
  },
  {
    key: "bancoestado_orange",
    label: "BancoEstado",
    bg: "linear-gradient(135deg, #E85500 0%, #B34000 100%)",
    textColor: "#fff",
    chipColor: "rgba(255,255,255,0.3)",
    logo: "BANCOESTADO",
  },
  {
    key: "bci_blue",
    label: "BCI",
    bg: "linear-gradient(135deg, #0066CC 0%, #003D8F 100%)",
    textColor: "#fff",
    chipColor: "rgba(255,255,255,0.3)",
    logo: "BCI",
  },
  {
    key: "chile_red",
    label: "Banco de Chile",
    bg: "linear-gradient(135deg, #CC0000 0%, #800000 100%)",
    textColor: "#fff",
    chipColor: "rgba(255,200,0,0.8)",
    logo: "BANCO DE CHILE",
  },
  {
    key: "falabella_green",
    label: "Falabella / CMR",
    bg: "linear-gradient(135deg, #008C45 0%, #005A2B 100%)",
    textColor: "#fff",
    chipColor: "rgba(255,255,255,0.3)",
    logo: "CMR FALABELLA",
  },
  {
    key: "ripley_purple",
    label: "Ripley",
    bg: "linear-gradient(135deg, #5C2D91 0%, #3A1A5E 100%)",
    textColor: "#fff",
    chipColor: "rgba(255,255,255,0.3)",
    logo: "RIPLEY BANK",
  },
  {
    key: "itau_orange",
    label: "Itaú",
    bg: "linear-gradient(135deg, #FF6600 0%, #CC5200 100%)",
    textColor: "#fff",
    chipColor: "rgba(255,255,255,0.3)",
    logo: "ITAÚ",
  },
  {
    key: "scotiabank_red",
    label: "Scotiabank",
    bg: "linear-gradient(135deg, #CC0000 0%, #8B0000 100%)",
    textColor: "#fff",
    chipColor: "rgba(255,200,0,0.8)",
    logo: "SCOTIABANK",
  },
  {
    key: "mercadopago_blue",
    label: "Mercado Pago",
    bg: "linear-gradient(135deg, #009EE3 0%, #006FAA 100%)",
    textColor: "#fff",
    chipColor: "rgba(255,255,255,0.3)",
    logo: "MERCADO PAGO",
  },
  {
    key: "black_premium",
    label: "Tarjeta Black",
    bg: "linear-gradient(135deg, #1a1a1a 0%, #3a3a3a 100%)",
    textColor: "#e5c870",
    chipColor: "rgba(229,200,112,0.7)",
    logo: "PREMIUM",
  },
];

// ── Mini card SVG ─────────────────────────────────────────────────────────────
function MiniCard({ preset, selected }: { preset: CardPreset; selected: boolean }) {
  return (
    <div
      className={`relative rounded-xl overflow-hidden cursor-pointer transition-all ${
        selected ? "ring-2 ring-brand-500 scale-105" : "hover:scale-105 opacity-80 hover:opacity-100"
      }`}
      style={{ background: preset.bg, aspectRatio: "1.586 / 1", minWidth: 80 }}
    >
      {/* Chip */}
      <div
        className="absolute rounded-sm"
        style={{
          left: 8, top: 18, width: 18, height: 14,
          background: preset.chipColor,
          boxShadow: "inset 0 0 0 1px rgba(0,0,0,0.15)",
        }}
      />
      {/* Bank name */}
      <div
        className="absolute bottom-2 left-2 right-2 text-[7px] font-bold tracking-widest truncate"
        style={{ color: preset.textColor }}
      >
        {preset.logo}
      </div>
      {/* Contactless dots */}
      <div className="absolute top-2 right-2 flex gap-0.5">
        {[0, 1, 2].map((i) => (
          <div
            key={i}
            className="rounded-full"
            style={{
              width: 3, height: 3,
              background: preset.textColor,
              opacity: 0.4 + i * 0.15,
            }}
          />
        ))}
      </div>
      {selected && (
        <div className="absolute inset-0 flex items-center justify-center bg-black/30">
          <Check size={16} className="text-white" />
        </div>
      )}
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────
interface Props {
  value: string;                          // current card_image_url or preset key
  accountId?: number;                     // needed for photo upload
  onSelect: (url: string) => void;        // emits preset key or image URL
  onUpload?: (file: File) => Promise<string>;  // upload fn, returns new URL
}

export default function CardImagePicker({ value, accountId, onSelect, onUpload }: Props) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);

  async function handleFile(file: File) {
    if (!onUpload) return;
    setUploading(true);
    try {
      const url = await onUpload(file);
      onSelect(url);
    } catch (e) {
      alert("Error al subir: " + String(e));
    } finally {
      setUploading(false);
    }
  }

  const isPhotoUrl = value && !value.startsWith("preset:");
  const resolvedPhoto = isPhotoUrl ? resolveImageUrl(value) : null;

  return (
    <div className="space-y-3">
      {/* Photo upload button */}
      <div className="flex items-center gap-3">
        {resolvedPhoto ? (
          <div className="relative w-20 rounded-xl overflow-hidden" style={{ aspectRatio: "1.586 / 1" }}>
            <img src={resolvedPhoto} alt="Card" className="w-full h-full object-cover" />
            <button
              onClick={() => onSelect("")}
              className="absolute inset-0 bg-black/40 flex items-center justify-center opacity-0 hover:opacity-100 transition-opacity text-white text-xs"
            >
              Quitar
            </button>
          </div>
        ) : null}
        <button
          type="button"
          onClick={() => fileRef.current?.click()}
          className="btn-ghost flex items-center gap-2 text-sm"
          disabled={uploading}
        >
          <Camera size={15} />
          {uploading ? "Subiendo…" : "Subir foto de mi tarjeta"}
        </button>
        <input
          ref={fileRef}
          type="file"
          accept="image/*"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) handleFile(f);
          }}
        />
      </div>

      {/* Preset grid */}
      <p className="text-xs text-slate-500 uppercase tracking-wide">O elige un diseño</p>
      <div className="grid grid-cols-5 gap-2">
        {CARD_PRESETS.map((preset) => (
          <div key={preset.key} onClick={() => onSelect(`preset:${preset.key}`)}>
            <MiniCard
              preset={preset}
              selected={value === `preset:${preset.key}`}
            />
          </div>
        ))}
      </div>
    </div>
  );
}

/** Resolve a preset key or image URL into CSS background style props. */
export function resolveCardBackground(
  cardImageUrl: string,
  fallbackColor: string,
): React.CSSProperties {
  if (!cardImageUrl) {
    return { background: `linear-gradient(135deg, ${fallbackColor}, ${shade(fallbackColor, -25)})` };
  }
  if (cardImageUrl.startsWith("preset:")) {
    const key = cardImageUrl.replace("preset:", "");
    const preset = CARD_PRESETS.find((p) => p.key === key);
    if (preset) return { background: preset.bg };
  }
  // Photo URL — use as background image
  const url = resolveImageUrl(cardImageUrl);
  return {
    backgroundImage: `url(${url})`,
    backgroundSize: "cover",
    backgroundPosition: "center",
  };
}

export function resolveCardTextColor(cardImageUrl: string): string {
  if (cardImageUrl?.startsWith("preset:")) {
    const key = cardImageUrl.replace("preset:", "");
    const preset = CARD_PRESETS.find((p) => p.key === key);
    return preset?.textColor ?? "#fff";
  }
  return "#fff";
}

function shade(hex: string, pct: number): string {
  const n = parseInt(hex.replace("#", ""), 16);
  const r = Math.min(255, Math.max(0, (n >> 16) + pct));
  const g = Math.min(255, Math.max(0, ((n >> 8) & 0xff) + pct));
  const b = Math.min(255, Math.max(0, (n & 0xff) + pct));
  return `#${((1 << 24) | (r << 16) | (g << 8) | b).toString(16).slice(1)}`;
}
