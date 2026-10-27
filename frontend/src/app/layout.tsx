import "./globals.css";
import type { Metadata } from "next";
import React from "react";
import { AppShell } from "../components/layout/app-shell";
import { AppProviders } from "../lib/providers";

export const metadata: Metadata = {
  title: "QuantPlatform — 三环境同构量化交易管理台",
  description: "Backtest / Shadow / Live Isomorphic Crypto Quant Trading Platform",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-CN" className="dark">
      <body className="min-h-screen bg-background text-foreground antialiased">
        <AppProviders>
          <AppShell>{children}</AppShell>
        </AppProviders>
      </body>
    </html>
  );
}
