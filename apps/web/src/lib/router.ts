import { useEffect, useState } from "react";

/** Hash routes: #/, #/projects, #/projects/:id, #/snapshots/:id, #/scans/:id, #/findings/:id,
 * #/ai-runs/:id, #/fixes/:id, #/reviews/:id, #/github, #/operations */
export type Route =
  | { name: "dashboard" }
  | { name: "projects" }
  | { name: "project"; id: string; tab: string }
  | { name: "snapshot"; id: string; tab: string }
  | { name: "scan"; id: string; tab: string }
  | { name: "finding"; id: string }
  | { name: "ai-run"; id: string }
  | { name: "fix"; id: string }
  | { name: "review"; id: string }
  | { name: "github"; params: Record<string, string> }
  | { name: "operations" }
  | { name: "not-found" };

const UUID = "[0-9a-fA-F-]{36}";

export function parseRoute(hash: string): Route {
  const [path = "", query = ""] = hash.replace(/^#/, "").split("?");
  const params = new URLSearchParams(query);
  const tab = params.get("tab") ?? "";
  const clean = path.replace(/\/+$/, "") || "/";
  if (clean === "/") return { name: "dashboard" };
  if (clean === "/projects") return { name: "projects" };
  if (clean === "/operations") return { name: "operations" };
  if (clean === "/github") return { name: "github", params: Object.fromEntries(params) };
  const match = new RegExp(
    `^/(projects|snapshots|scans|findings|ai-runs|fixes|reviews)/(${UUID})$`,
  ).exec(clean);
  if (match?.[2]) {
    const id = match[2];
    switch (match[1]) {
      case "projects":
        return { name: "project", id, tab: tab || "overview" };
      case "snapshots":
        return { name: "snapshot", id, tab: tab || "scope" };
      case "scans":
        return { name: "scan", id, tab: tab || "findings" };
      case "ai-runs":
        return { name: "ai-run", id };
      case "fixes":
        return { name: "fix", id };
      case "reviews":
        return { name: "review", id };
      default:
        return { name: "finding", id };
    }
  }
  return { name: "not-found" };
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseRoute(window.location.hash));
  useEffect(() => {
    const update = () => {
      setRoute(parseRoute(window.location.hash));
    };
    window.addEventListener("hashchange", update);
    return () => {
      window.removeEventListener("hashchange", update);
    };
  }, []);
  return route;
}

export function navigate(hash: string): void {
  window.location.hash = hash;
}
