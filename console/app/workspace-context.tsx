"use client";

/**
 * workspace-context.tsx — which workspace_id the console is currently
 * pointed at. Persisted to localStorage (falls back to
 * NEXT_PUBLIC_WORKSPACE_ID) so re-seeding the demo (a new workspace_id
 * every run — see demo/seed.py) doesn't require a rebuild, just pasting
 * the new id into the switcher in the header.
 *
 * If neither is set, the console asks the API which workspace to show
 * (GET /demo-workspace) rather than sitting on an empty state telling the
 * visitor to run `python -m demo.seed` — correct advice for a developer
 * running locally, useless to anyone opening the deployed demo link. The
 * discovered id is NOT written to localStorage: it's a fallback for this
 * visit, not a choice the user made, so a later reseed is picked up
 * automatically instead of being pinned forever by the first page view.
 */

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { fetchDemoWorkspace } from "./api-client";

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
    const explicit = stored ?? process.env.NEXT_PUBLIC_WORKSPACE_ID ?? "";
    if (explicit) {
      setWorkspaceIdState(explicit);
      return;
    }
    let cancelled = false;
    fetchDemoWorkspace()
      .then((id) => {
        if (!cancelled && id) setWorkspaceIdState(id);
      })
      .catch(() => {
        /* leave empty — the views render their own "no workspace" state */
      });
    return () => {
      cancelled = true;
    };
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
