"use client";

import React, { useState } from "react";
import { Check, CheckCircle2, Copy, FileCode2, ShieldCheck, XCircle } from "lucide-react";
import { formatUtcTimestamp } from "../../lib/formatters";
import { cn } from "../../lib/utils";
import type { StrategyVersionResponse } from "../../types/openapi.generated";
import { ApprovalBadge } from "../quant/badges";
import { Button } from "../ui/button";

export interface StrategyCodeAndVersionPanelProps {
  versions: StrategyVersionResponse[];
  selectedVersion: StrategyVersionResponse;
  onSelectVersion: (v: StrategyVersionResponse) => void;
  canApprove: boolean;
  isApproving?: boolean;
  onApprove: (versionId: string, note: string) => Promise<void> | void;
  onReject: (versionId: string, note: string) => Promise<void> | void;
}

export function StrategyCodeAndVersionPanel({
  versions,
  selectedVersion,
  onSelectVersion,
  canApprove,
  isApproving = false,
  onApprove,
  onReject,
}: StrategyCodeAndVersionPanelProps) {
  const [approvalNote, setApprovalNote] = useState("");
  const [copied, setCopied] = useState(false);

  const handleCopyCode = async () => {
    if (typeof navigator !== "undefined" && navigator.clipboard) {
      await navigator.clipboard.writeText(selectedVersion.code);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    }
  };

  const diffLines = (selectedVersion.diff_text || "").split("\n");
  const codeLines = selectedVersion.code.split("\n");

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
      {/* Left 4 Cols: Version History Timeline */}
      <div className="rounded-lg border border-border bg-card p-3.5 space-y-2.5 lg:col-span-4">
        <div className="flex items-center justify-between border-b border-border pb-2">
          <h3 className="text-xs font-semibold text-white">
            版本演进历史 ({versions.length})
          </h3>
          <span className="font-mono text-[10px] text-slate-400">不可变快照 + 哈希链审计</span>
        </div>

        <div className="max-h-[600px] space-y-2 overflow-y-auto pr-1">
          {[...versions]
            .sort((a, b) => b.version - a.version)
            .map((ver) => {
              const isSelected = ver.version_id === selectedVersion.version_id;
              return (
                <button
                  key={ver.version_id}
                  type="button"
                  onClick={() => onSelectVersion(ver)}
                  className={cn(
                    "w-full rounded-md border p-2.5 text-left transition-colors",
                    isSelected
                      ? "border-blue-500/70 bg-blue-950/25"
                      : "border-slate-800 bg-slate-900/50 hover:border-slate-700"
                  )}
                >
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex items-center gap-1.5">
                      <span className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[11px] font-bold text-white">
                        v{ver.version}
                      </span>
                      <span className="font-mono text-[10px] text-slate-400">
                        {ver.source_type}
                      </span>
                    </div>
                    <ApprovalBadge status={ver.approval_status} />
                  </div>

                  <div className="mt-1.5 flex items-center justify-between font-mono text-[10px] text-slate-400">
                    <span>作者: {ver.created_by}</span>
                    <span>{formatUtcTimestamp(ver.created_at_ms, false)}</span>
                  </div>

                  {ver.approved_by && (
                    <div className="mt-1 font-mono text-[10px] text-emerald-400">
                      审批人: {ver.approved_by}
                    </div>
                  )}

                  {ver.generation_prompt && (
                    <div className="mt-1.5 truncate rounded bg-slate-950/90 px-2 py-1 text-[10px] text-violet-300">
                      NL: {ver.generation_prompt}
                    </div>
                  )}
                </button>
              );
            })}
        </div>
      </div>

      {/* Right 8 Cols: Manual Approval Gate + Version Diff + Read-Only Numbered Code */}
      <div className="space-y-4 lg:col-span-8">
        {/* Manual Operator Approval Gate (Red Lines 7 & 8) */}
        <div
          className={cn(
            "rounded-lg border p-3.5",
            selectedVersion.approval_status === "APPROVED"
              ? "border-emerald-500/35 bg-emerald-950/15"
              : selectedVersion.approval_status === "REJECTED"
                ? "border-rose-500/35 bg-rose-950/15"
                : "border-amber-500/45 bg-amber-950/20"
          )}
        >
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="space-y-1">
              <div className="flex items-center gap-2">
                <ShieldCheck className="h-4 w-4 text-sky-400" />
                <span className="text-xs font-semibold text-white">
                  人工审批门禁 · {selectedVersion.version_id}
                </span>
                <ApprovalBadge status={selectedVersion.approval_status} />
              </div>
              <p className="text-[11px] text-slate-300">
                红线 7/8：未经人工审批通过的版本仅允许运行回测，严禁进入影子模拟或实盘交易。
              </p>
            </div>

            {canApprove ? (
              <div className="flex flex-wrap items-center gap-2">
                <input
                  type="text"
                  value={approvalNote}
                  onChange={(e) => setApprovalNote(e.target.value)}
                  placeholder="填写审批备注（写入防篡改审计链）..."
                  className="h-8 w-56 rounded border border-slate-700 bg-slate-950 px-2.5 text-xs text-white placeholder:text-slate-500 focus:border-blue-500 focus:outline-none"
                />
                <Button
                  size="sm"
                  variant="default"
                  className="bg-emerald-600 hover:bg-emerald-500"
                  disabled={isApproving || selectedVersion.approval_status === "APPROVED"}
                  onClick={() =>
                    onApprove(
                      selectedVersion.version_id,
                      approvalNote || `Approved v${selectedVersion.version} after code & diff review`
                    )
                  }
                >
                  <CheckCircle2 className="h-3.5 w-3.5" />
                  <span>人工审批通过</span>
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  className="border-rose-500/50 text-rose-300 hover:bg-rose-950/50"
                  disabled={isApproving || selectedVersion.approval_status === "REJECTED"}
                  onClick={() =>
                    onReject(
                      selectedVersion.version_id,
                      approvalNote || `Rejected v${selectedVersion.version}`
                    )
                  }
                >
                  <XCircle className="h-3.5 w-3.5" />
                  <span>驳回版本</span>
                </Button>
              </div>
            ) : (
              <span className="rounded border border-slate-700 bg-slate-900 px-2.5 py-1 text-[11px] text-slate-300">
                需 OPERATOR 或 ADMIN 角色执行人工审批
              </span>
            )}
          </div>

          {selectedVersion.backtest_gate && (
            <div className="mt-2.5 flex flex-wrap items-center gap-3 border-t border-slate-800/80 pt-2 font-mono text-[11px] text-slate-300">
              <span className="text-sky-300 font-semibold">自动沙箱与回测门禁结果:</span>
              {Object.entries(selectedVersion.backtest_gate).map(([k, v]) => (
                <span key={k}>
                  {k}: <strong className="text-white">{String(v)}</strong>
                </span>
              ))}
            </div>
          )}
        </div>

        {/* Version Change Diff */}
        <div className="rounded-lg border border-border bg-card overflow-hidden">
          <div className="border-b border-border bg-slate-900/80 px-3.5 py-2 text-xs font-semibold text-slate-200">
            版本变更对比 Diff（v{selectedVersion.version} 相对上一版本）
          </div>
          <div className="max-h-56 overflow-auto bg-slate-950 p-3 font-mono text-[11px] leading-relaxed">
            {!selectedVersion.diff_text || selectedVersion.diff_text.trim() === "" ? (
              <div className="text-slate-500">初始版本（v1），无前置版本变更对比。</div>
            ) : (
              diffLines.map((line, idx) => {
                let lineCls = "text-slate-300";
                if (line.startsWith("+") && !line.startsWith("+++")) {
                  lineCls = "bg-emerald-950/50 text-emerald-300";
                } else if (line.startsWith("-") && !line.startsWith("---")) {
                  lineCls = "bg-rose-950/50 text-rose-300";
                } else if (line.startsWith("@@")) {
                  lineCls = "text-sky-400";
                }
                return (
                  <div key={idx} className={cn("whitespace-pre px-1.5", lineCls)}>
                    {line || " "}
                  </div>
                );
              })
            )}
          </div>
        </div>

        {/* Read-Only Source Code Viewer with Line Numbers & SHA-256 Hash */}
        <div className="rounded-lg border border-border bg-card overflow-hidden">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border bg-slate-900/80 px-3.5 py-2">
            <div className="flex items-center gap-2 text-xs font-semibold text-slate-200">
              <FileCode2 className="h-4 w-4 text-sky-400" />
              <span>只读策略源码</span>
              <code className="rounded bg-slate-950 px-2 py-0.5 font-mono text-[10px] text-slate-400">
                SHA256: {selectedVersion.code_hash.slice(0, 20)}...
              </code>
            </div>
            <Button type="button" size="sm" variant="outline" onClick={handleCopyCode}>
              {copied ? (
                <>
                  <Check className="h-3.5 w-3.5 text-emerald-400" />
                  <span>已复制</span>
                </>
              ) : (
                <>
                  <Copy className="h-3.5 w-3.5" />
                  <span>复制源码</span>
                </>
              )}
            </Button>
          </div>
          <div className="max-h-[420px] overflow-auto bg-[#070b14] py-2.5 font-mono text-xs leading-5">
            {codeLines.map((line, i) => (
              <div
                key={i}
                className="flex hover:bg-slate-900/70 px-3"
              >
                <span className="w-9 shrink-0 select-none pr-3 text-right text-[11px] text-slate-600">
                  {i + 1}
                </span>
                <span className="whitespace-pre text-slate-200">{line || " "}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
