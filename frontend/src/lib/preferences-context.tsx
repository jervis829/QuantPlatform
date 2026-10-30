"use client";

import React, { createContext, useContext, useEffect, useState } from "react";
import type { PnlPolarity, TableDensity } from "./tokens";

export type RbacRoleLabel = "ADMIN" | "OPERATOR" | "RESEARCHER" | "VIEWER";

interface PreferencesContextValue {
  apiKey: string;
  setApiKey: (key: string) => void;
  roleLabel: RbacRoleLabel;
  pnlPolarity: PnlPolarity;
  setPnlPolarity: (p: PnlPolarity) => void;
  tableDensity: TableDensity;
  setTableDensity: (d: TableDensity) => void;
  alertDrawerOpen: boolean;
  setAlertDrawerOpen: (open: boolean) => void;
  commandPaletteOpen: boolean;
  setCommandPaletteOpen: (open: boolean) => void;
  killSwitchModalOpen: boolean;
  setKillSwitchModalOpen: (open: boolean) => void;
  wsConnected: boolean;
  setWsConnected: (connected: boolean) => void;
}

const PreferencesContext = createContext<PreferencesContextValue | null>(null);

export function inferRoleFromDevKey(key: string): RbacRoleLabel {
  if (key.includes("viewer")) return "VIEWER";
  if (key.includes("researcher")) return "RESEARCHER";
  if (key.includes("operator")) return "OPERATOR";
  return "ADMIN";
}

export function PreferencesProvider({ children }: { children: React.ReactNode }) {
  const [apiKey, setApiKeyState] = useState<string>("admin-dev-token");
  const [pnlPolarity, setPnlPolarityState] = useState<PnlPolarity>("green-up");
  const [tableDensity, setTableDensityState] = useState<TableDensity>("standard");
  const [alertDrawerOpen, setAlertDrawerOpen] = useState(false);
  const [commandPaletteOpen, setCommandPaletteOpen] = useState(false);
  const [killSwitchModalOpen, setKillSwitchModalOpen] = useState(false);
  const [wsConnected, setWsConnected] = useState(true);

  useEffect(() => {
    try {
      const savedKey = window.localStorage.getItem("qp_api_key");
      const savedPol = window.localStorage.getItem("qp_pnl_polarity") as PnlPolarity | null;
      const savedDen = window.localStorage.getItem("qp_table_density") as TableDensity | null;
      if (savedKey) setApiKeyState(savedKey);
      if (savedPol === "green-up" || savedPol === "red-up") setPnlPolarityState(savedPol);
      if (savedDen === "compact" || savedDen === "standard" || savedDen === "comfortable") {
        setTableDensityState(savedDen);
      }
    } catch {
      // ignore storage errors
    }
  }, []);

  useEffect(() => {
    document.documentElement.setAttribute("data-pnl-polarity", pnlPolarity);
  }, [pnlPolarity]);

  const setApiKey = (k: string) => {
    setApiKeyState(k);
    try {
      window.localStorage.setItem("qp_api_key", k);
    } catch {
      // ignore
    }
  };

  const setPnlPolarity = (p: PnlPolarity) => {
    setPnlPolarityState(p);
    try {
      window.localStorage.setItem("qp_pnl_polarity", p);
    } catch {
      // ignore
    }
  };

  const setTableDensity = (d: TableDensity) => {
    setTableDensityState(d);
    try {
      window.localStorage.setItem("qp_table_density", d);
    } catch {
      // ignore
    }
  };

  return (
    <PreferencesContext.Provider
      value={{
        apiKey,
        setApiKey,
        roleLabel: inferRoleFromDevKey(apiKey),
        pnlPolarity,
        setPnlPolarity,
        tableDensity,
        setTableDensity,
        alertDrawerOpen,
        setAlertDrawerOpen,
        commandPaletteOpen,
        setCommandPaletteOpen,
        killSwitchModalOpen,
        setKillSwitchModalOpen,
        wsConnected,
        setWsConnected,
      }}
    >
      {children}
    </PreferencesContext.Provider>
  );
}

export function usePreferences(): PreferencesContextValue {
  const ctx = useContext(PreferencesContext);
  if (!ctx) {
    throw new Error("usePreferences must be used inside PreferencesProvider");
  }
  return ctx;
}
