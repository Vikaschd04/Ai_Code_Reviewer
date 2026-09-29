import { createContext, useContext } from "react";

import type { AuthOptions, Principal } from "../api/client";

export interface SessionInfo {
  principal: Principal | null;
  options: AuthOptions | null;
}

export const SessionContext = createContext<SessionInfo>({ principal: null, options: null });

export function useSession(): SessionInfo {
  return useContext(SessionContext);
}

/** Developer-only affordances (local runner, CLI hints) appear only in local development. */
export function isLocalDevelopment(options: AuthOptions | null): boolean {
  return options?.environment === "local";
}

/** The first workspace where the signed-in user may create projects. */
export function writableWorkspace(principal: Principal | null): string | null {
  return principal?.workspaces.find((w) => w.role !== "viewer")?.workspace_id ?? null;
}
