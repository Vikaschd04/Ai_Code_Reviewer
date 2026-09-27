export type Theme = "dark" | "light";

const KEY = "crp-theme";

export function storedTheme(): Theme | null {
  try {
    const value = window.localStorage.getItem(KEY);
    return value === "dark" || value === "light" ? value : null;
  } catch {
    return null;
  }
}

export function activeTheme(): Theme {
  return (
    storedTheme() ?? (window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark")
  );
}

export function applyTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme;
  try {
    window.localStorage.setItem(KEY, theme);
  } catch {
    // Storage may be unavailable (private mode); the theme still applies for this page view.
  }
}
