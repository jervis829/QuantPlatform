"use client";

import React from "react";
import { usePreferences } from "../../lib/preferences-context";
import { KillSwitchModal } from "../quant/confirm-dialog";
import { CommandPalette } from "./command-palette";
import { GlobalStatusBar } from "./global-status-bar";
import { NotificationCenter } from "./notification-center";
import { SidebarNav } from "./sidebar-nav";

export function AppShell({ children }: { children: React.ReactNode }) {
  const { killSwitchModalOpen, setKillSwitchModalOpen } = usePreferences();

  return (
    <div className="flex min-h-screen w-full bg-background text-foreground">
      <SidebarNav />
      <div className="flex min-w-0 flex-1 flex-col">
        <GlobalStatusBar />
        <main className="flex-1 p-4 lg:p-5 max-w-[1600px] w-full mx-auto">
          {children}
        </main>
      </div>

      <CommandPalette />
      <NotificationCenter />
      <KillSwitchModal
        open={killSwitchModalOpen}
        onOpenChange={setKillSwitchModalOpen}
      />
    </div>
  );
}
