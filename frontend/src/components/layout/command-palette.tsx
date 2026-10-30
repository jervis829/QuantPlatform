"use client";

import React, { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Command } from "cmdk";
import { ArrowRight, FileCode2, Layers, PlayCircle, ReceiptText, Search } from "lucide-react";
import { useRouter } from "next/navigation";
import { quantApi } from "../../lib/apiClient";
import { usePreferences } from "../../lib/preferences-context";
import { EnvBadge } from "../quant/badges";
import { Dialog, DialogContent, DialogTitle } from "../ui/dialog";

export function CommandPalette() {
  const router = useRouter();
  const { apiKey, commandPaletteOpen, setCommandPaletteOpen } = usePreferences();
  const [search, setSearch] = useState("");

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setCommandPaletteOpen(!commandPaletteOpen);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [commandPaletteOpen, setCommandPaletteOpen]);

  const searchQuery = useQuery({
    queryKey: ["global-search", apiKey, search],
    queryFn: () => quantApi.globalSearch(apiKey, search),
    enabled: commandPaletteOpen,
  });

  const hits = searchQuery.data?.hits ?? [];

  const handleSelect = (href: string) => {
    setCommandPaletteOpen(false);
    setSearch("");
    router.push(href);
  };

  return (
    <Dialog open={commandPaletteOpen} onOpenChange={setCommandPaletteOpen}>
      <DialogContent className="max-w-xl overflow-hidden p-0 border-slate-700 bg-slate-950">
        <DialogTitle className="sr-only">全局命令与检索面板</DialogTitle>
        <Command shouldFilter={false} className="w-full">
          <div className="flex items-center border-b border-border px-3">
            <Search className="mr-2 h-4 w-4 shrink-0 text-slate-400" />
            <Command.Input
              value={search}
              onValueChange={setSearch}
              placeholder="输入策略名、页面名称或订单号快速跳转..."
              className="flex h-11 w-full bg-transparent py-3 text-xs text-slate-100 placeholder:text-slate-500 focus:outline-none"
            />
          </div>
          <Command.List className="max-h-80 overflow-y-auto p-2">
            {hits.length === 0 && (
              <Command.Empty className="py-6 text-center text-xs text-muted-foreground">
                未找到匹配项（试试输入策略名、页面名或订单号）
              </Command.Empty>
            )}

            {["nav", "strategy", "run", "order"].map((groupKind) => {
              const groupItems = hits.filter((h) => h.kind === groupKind);
              if (groupItems.length === 0) return null;
              const groupTitle =
                groupKind === "nav"
                  ? "功能页面"
                  : groupKind === "strategy"
                    ? "我的策略"
                    : groupKind === "run"
                      ? "运行记录"
                      : "订单记录";
              return (
                <Command.Group
                  key={groupKind}
                  heading={groupTitle}
                  className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-slate-500 [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1"
                >
                  {groupItems.map((hit) => (
                    <Command.Item
                      key={`${hit.kind}_${hit.id}`}
                      value={`${hit.kind}_${hit.id}_${hit.title}`}
                      onSelect={() => handleSelect(hit.href)}
                      className="flex cursor-pointer items-center justify-between gap-2 rounded-md px-2.5 py-2 text-xs text-slate-200 aria-selected:bg-blue-600/25 aria-selected:text-white"
                    >
                      <div className="flex items-center gap-2.5 min-w-0">
                        {hit.kind === "strategy" ? (
                          <FileCode2 className="h-4 w-4 text-sky-400 shrink-0" />
                        ) : hit.kind === "run" ? (
                          <PlayCircle className="h-4 w-4 text-emerald-400 shrink-0" />
                        ) : hit.kind === "order" ? (
                          <ReceiptText className="h-4 w-4 text-amber-400 shrink-0" />
                        ) : (
                          <Layers className="h-4 w-4 text-slate-400 shrink-0" />
                        )}
                        <div className="min-w-0">
                          <div className="truncate font-medium">{hit.title}</div>
                          <div className="truncate text-[11px] font-normal text-slate-400">
                            {hit.subtitle}
                          </div>
                        </div>
                      </div>
                      <div className="flex items-center gap-2 shrink-0">
                        {hit.env && <EnvBadge env={hit.env} />}
                        <ArrowRight className="h-3.5 w-3.5 text-slate-500" />
                      </div>
                    </Command.Item>
                  ))}
                </Command.Group>
              );
            })}
          </Command.List>
        </Command>
      </DialogContent>
    </Dialog>
  );
}
