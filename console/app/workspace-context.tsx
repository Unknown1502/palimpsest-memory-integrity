"use client";

/**
 * workspace-context.tsx — which workspace_id the console is currently
 * pointed at. Persisted to localStorage (falls back to
 * NEXT_PUBLIC_WORKSPACE_ID) so re-seeding the demo (a new workspace_id
 * every run — see demo/seed.py) doesn't require a rebuild, just pasting
 * the new id into the switcher in the header.
 */

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

const STORAGE_KEY = "palimpsest_workspace_id";

interface WorkspaceContextValue {
  workspaceId: string;
  setWorkspaceId: (id: string) => void;
}

const WorkspaceContext = createContext<WorkspaceContextValue | null>(null);

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [workspaceId, setWorkspaceIdState] = useState<string>("");

  useEffect(() => {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    setWorkspaceIdState(stored ?? process.env.NEXT_PUBLIC_WORKSPACE_ID ?? "");
  }, []);

  const setWorkspaceId = (id: string) => {
    setWorkspaceIdState(id);
    window.localStorage.setItem(STORAGE_KEY, id);
  };

  return (
    <WorkspaceContext.Provider value={{ workspaceId, setWorkspaceId }}>
      {children}
    </WorkspaceContext.Provider>
  );
}

export function useWorkspace(): WorkspaceContextValue {
  const ctx = useContext(WorkspaceContext);
  if (!ctx) throw new Error("useWorkspace must be used within WorkspaceProvider");
  return ctx;
}
