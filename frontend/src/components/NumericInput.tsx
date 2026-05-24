"use client";
/**
 * NumericInput — a text input that:
 *  - Formats the displayed value with thousands-separator commas as you type
 *  - Strips leading zeros
 *  - Supports an optional decimal part
 *  - Calls onChange with the raw numeric value (number)
 *
 * Usage:
 *   <NumericInput value={amount} onChange={setAmount} className="input" />
 */
import { useRef, useState, useEffect, ChangeEvent } from "react";

interface Props {
  value: number;
  onChange: (v: number) => void;
  onBlur?: () => void;
  onEnter?: () => void;
  className?: string;
  placeholder?: string;
  allowDecimals?: boolean;
  min?: number;
}

function formatDisplay(raw: string, allowDecimals: boolean): string {
  // Split on decimal
  const [intPart, ...rest] = raw.split(".");
  const decPart = rest.join(".");

  // Add commas to integer part
  const withCommas = intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ",");

  if (allowDecimals && raw.includes(".")) {
    return withCommas + "." + decPart;
  }
  return withCommas;
}

export default function NumericInput({
  value,
  onChange,
  onBlur,
  onEnter,
  className = "",
  placeholder = "0",
  allowDecimals = false,
  min = 0,
}: Props) {
  // Track raw string so user can type freely; sync from prop when not focused
  const [display, setDisplay] = useState(() =>
    value === 0 ? "" : formatDisplay(String(value), allowDecimals),
  );
  const focused = useRef(false);

  // When prop changes from outside (e.g. form reset), update display
  useEffect(() => {
    if (!focused.current) {
      setDisplay(value === 0 ? "" : formatDisplay(String(value), allowDecimals));
    }
  }, [value, allowDecimals]);

  function handleChange(e: ChangeEvent<HTMLInputElement>) {
    let raw = e.target.value;

    // Only keep digits (and optionally one decimal point)
    if (allowDecimals) {
      raw = raw.replace(/[^\d.]/g, "");
      // Allow only one dot
      const parts = raw.split(".");
      if (parts.length > 2) raw = parts[0] + "." + parts.slice(1).join("");
    } else {
      raw = raw.replace(/\D/g, "");
    }

    // Strip leading zeros (but preserve empty string and "0.")
    if (!raw.startsWith("0.") && raw.length > 1) {
      raw = raw.replace(/^0+/, "");
    }

    setDisplay(formatDisplay(raw, allowDecimals));

    const num = allowDecimals ? parseFloat(raw) : parseInt(raw, 10);
    onChange(isNaN(num) ? 0 : Math.max(min, num));
  }

  function handleFocus() {
    focused.current = true;
    // Remove commas for easier editing
    setDisplay(display.replace(/,/g, ""));
  }

  function handleBlur() {
    focused.current = false;
    // Re-format with commas on blur
    const raw = display.replace(/,/g, "");
    if (!raw || raw === "0") {
      setDisplay("");
      onChange(0);
    } else {
      setDisplay(formatDisplay(raw, allowDecimals));
    }
    onBlur?.();
  }

  return (
    <input
      type="text"
      inputMode="numeric"
      className={className}
      value={display}
      placeholder={placeholder}
      onChange={handleChange}
      onFocus={handleFocus}
      onBlur={handleBlur}
      onKeyDown={(e) => e.key === "Enter" && onEnter?.()}
    />
  );
}
