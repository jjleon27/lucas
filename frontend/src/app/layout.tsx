import "./globals.css";
import type { Metadata, Viewport } from "next";
import Sidebar from "@/components/Sidebar";
import LucasFAB from "@/components/LucasFAB";
import { I18nProvider } from "@/lib/i18n";

export const metadata: Metadata = {
  title: "LUCAS — Tu asistente financiero con IA",
  description:
    "Tus lucas, bajo control. Sube pantallazos y Lucas los convierte en tus finanzas.",
  manifest: "/manifest.json",
};

export const viewport: Viewport = {
  themeColor: "#10b981",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="es">
      <body className="min-h-screen font-sans">
        <I18nProvider>
          <div className="flex min-h-screen">
            <Sidebar />
            <main className="flex-1 p-4 md:p-8 md:ml-64">{children}</main>
          </div>
          {/* Lucas floating action button — shown on every page for logged-in users */}
          <LucasFAB />
        </I18nProvider>
      </body>
    </html>
  );
}
