"use client";

import React, { useEffect, useState } from "react";
import { QueryClient, QueryClientProvider, useQueryClient } from "@tanstack/react-query";
import { quantApi } from "./apiClient";
import { PreferencesProvider, usePreferences } from "./preferences-context";

function WebSocketQuerySync() {
  const queryClient = useQueryClient();
  const { setWsConnected } = usePreferences();

  useEffect(() => {
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let activeSocket: WebSocket | null = null;
    let unmounted = false;

    const connect = () => {
      if (unmounted) return;
      activeSocket = quantApi.openEventSocket(
        (ev) => {
          if (ev.type === "connected" || ev.type === "pong") {
            setWsConnected(true);
            return;
          }
          if (ev.type === "run_progress" && ev.run_id && ev.env) {
            queryClient.setQueryData(
              ["run-status", String(ev.env), String(ev.run_id)],
              {
                run_id: String(ev.run_id),
                env: String(ev.env),
                strategy_id: String(ev.strategy_id ?? ""),
                version_id: String(ev.version_id ?? ""),
                status: String(ev.status ?? "RUNNING"),
                stage: String(ev.stage ?? "EVENT_REPLAY"),
                processed_bars: Number(ev.processed_bars ?? 0),
                total_bars: Number(ev.total_bars ?? 180),
                progress_pct: Number(ev.progress_pct ?? 0),
                elapsed_ms: Number(ev.elapsed_ms ?? 0),
                estimated_remaining_ms:
                  ev.estimated_remaining_ms === null || ev.estimated_remaining_ms === undefined
                    ? null
                    : Number(ev.estimated_remaining_ms),
                error_code: ev.error_code ? String(ev.error_code) : null,
                error_message: ev.error_message ? String(ev.error_message) : null,
              }
            );
            return;
          }
          if (
            (ev.type === "run_completed" ||
              ev.type === "run_canceled" ||
              ev.type === "run_failed") &&
            ev.run_id &&
            ev.env
          ) {
            void queryClient.invalidateQueries({
              queryKey: ["run-status", String(ev.env), String(ev.run_id)],
            });
          }
          void queryClient.invalidateQueries({ queryKey: ["system-overview"] });
          void queryClient.invalidateQueries({ queryKey: ["strategies"] });
          void queryClient.invalidateQueries({ queryKey: ["runs"] });
          void queryClient.invalidateQueries({ queryKey: ["risk-events"] });
          void queryClient.invalidateQueries({ queryKey: ["reconciliation-reports"] });
        },
        (connected) => {
          setWsConnected(connected);
          if (!connected && !unmounted) {
            reconnectTimer = setTimeout(connect, 3000);
          }
        }
      );
    };

    connect();
    return () => {
      unmounted = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (activeSocket) activeSocket.close();
    };
  }, [queryClient, setWsConnected]);

  return null;
}

export function AppProviders({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 4000,
            refetchOnWindowFocus: false,
            retry: 1,
          },
        },
      })
  );

  return (
    <QueryClientProvider client={queryClient}>
      <PreferencesProvider>
        <WebSocketQuerySync />
        {children}
      </PreferencesProvider>
    </QueryClientProvider>
  );
}
