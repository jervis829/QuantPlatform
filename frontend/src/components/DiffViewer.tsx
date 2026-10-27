"use client";

import React from "react";

export function DiffViewer({ diffText }: { diffText: string }) {
  if (!diffText) {
    return <pre style={{ color: "#94a3b8" }}>无差异（初始空版本）。</pre>;
  }
  const lines = diffText.split("\n");
  return (
    <pre
      style={{
        background: "#020617",
        padding: "12px",
        borderRadius: "6px",
        overflowX: "auto",
        fontSize: "12px",
        lineHeight: 1.5,
        border: "1px solid #1e293b",
      }}
    >
      {lines.map((line, idx) => {
        let color = "#cbd5e1";
        let bg = "transparent";
        if (line.startsWith("+") && !line.startsWith("+++")) {
          color = "#4ade80";
          bg = "rgba(34, 197, 94, 0.12)";
        } else if (line.startsWith("-") && !line.startsWith("---")) {
          color = "#f87171";
          bg = "rgba(239, 68, 68, 0.12)";
        } else if (line.startsWith("@@")) {
          color = "#38bdf8";
        }
        return (
          <div key={idx} style={{ color, background: bg, padding: "0 4px" }}>
            {line}
          </div>
        );
      })}
    </pre>
  );
}
