"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { useWorkspace } from "./workspace-context";

// The control plane lives at "/" because that is what the deployed Function
// URL serves: a visitor clicking the demo link should land on the integrity
// summary, not on a sub-page they have to discover.
const LINKS = [
  { href: "/", label: "Control Plane", exact: true },
  { href: "/attack", label: "Attack" },
  { href: "/timeline", label: "Timeline" },
  { href: "/memories", label: "Memories" },
  { href: "/rewind", label: "Rewind" },
  { href: "/benchmark", label: "Benchmark" },
  { href: "/proof", label: "Proof" },
];

export function NavBar() {
  const pathname = usePathname();
  const { workspaceId, setWorkspaceId } = useWorkspace();
  const [draft, setDraft] = useState(workspaceId);

  // workspaceId loads from localStorage inside WorkspaceProvider's own
  // effect, which runs AFTER this component's initial render — so the
  // useState(workspaceId) initializer above captures "" on first mount
  // (or on any full-page navigation, which remounts everything) and never
  // re-syncs on its own. Confirmed visually: the input showed the
  // placeholder even though the page below it was correctly using the
  // loaded workspaceId for real data fetches.
  useEffect(() => {
    setDraft(workspaceId);
  }, [workspaceId]);

  return (
    <header className="border-b border-border bg-panel">
      <div className="mx-auto flex max-w-6xl items-center gap-6 px-6 py-3">
        <Link href="/timeline" className="flex items-center gap-2">
          <span className="h-2 w-2 rounded-full bg-accent" />
          <span className="text-sm font-bold tracking-wide">PALIMPSEST</span>
        </Link>

        <nav className="flex items-center gap-1">
          {LINKS.map((link) => {
            // trailingSlash: true means "/" arrives as "/" and "/proof" as
            // "/proof/", so normalize before comparing. Without the exact
            // flag, startsWith("/") would light up every link at once.
            const path = pathname?.replace(/\/$/, "") || "/";
            const isActive = link.exact ? path === "/" : path.startsWith(link.href);
            return (
              <Link
                key={link.href}
                href={link.href}
                className={`rounded px-3 py-1.5 text-sm font-medium transition-colors ${
                  isActive ? "bg-panel-alt text-text" : "text-text-muted hover:text-text"
                }`}
              >
                {link.label}
              </Link>
            );
          })}
        </nav>

        <div className="ml-auto flex items-center gap-2">
          <span className="text-[11px] uppercase tracking-wide text-text-faint">workspace</span>
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={() => setWorkspaceId(draft.trim())}
            onKeyDown={(e) => {
              if (e.key === "Enter") setWorkspaceId(draft.trim());
            }}
            placeholder="paste workspace_id…"
            className="w-72 rounded border border-border bg-bg px-2 py-1 font-mono-data text-[12px] text-text placeholder:text-text-faint focus:border-accent focus:outline-none"
          />
        </div>
      </div>
    </header>
  );
}
