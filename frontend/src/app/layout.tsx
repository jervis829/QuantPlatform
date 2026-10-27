import React from "react";

export const metadata = {
  title: "QuantPlatform — Multi-Strategy Crypto Trading Console",
  description: "Self-hosted Backtest / Shadow / Live Crypto Trading & Risk Control Plane",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-CN">
      <body
        style={{
          margin: 0,
          fontFamily:
            "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, monospace",
          background: "#090d16",
          color: "#e2e8f0",
        }}
      >
        {children}
      </body>
    </html>
  );
}
