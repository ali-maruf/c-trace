import type { Metadata, Viewport } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import { Providers } from "@/components/providers";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter", display: "swap" });
const mono = JetBrains_Mono({ subsets: ["latin"], variable: "--font-jetbrains", display: "swap" });

export const metadata: Metadata = {
  title: { default: "C-Trace — Trace every tonne.", template: "%s · C-Trace" },
  description: "Verifiable carbon MRV for factories: from paper bills to buyer-ready, bank-ready reports, every tonne traceable to its source.",
};

export const viewport: Viewport = { themeColor: "#0F766E", width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${inter.variable} ${mono.variable}`}>
      <body className="min-h-screen bg-white text-ink">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
