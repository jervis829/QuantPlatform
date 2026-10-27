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
