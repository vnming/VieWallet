/** Theme on <html data-theme>: "SYSTEM" follows the operating system (tokens.css). */
export type Theme = "SYSTEM" | "LIGHT" | "DARK";

const KEY = "vw.theme";

export function applyTheme(theme: Theme): void {
  const root = document.documentElement;
  if (theme === "SYSTEM") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", theme.toLowerCase());
  try {
    localStorage.setItem(KEY, theme);
  } catch {
    // storage disabled: the theme still applies for this page
  }
}

/** Before sign-in (and before /me answers) use the last theme seen on this device. */
export function storedTheme(): Theme {
  try {
    const value = localStorage.getItem(KEY);
    return value === "LIGHT" || value === "DARK" ? value : "SYSTEM";
  } catch {
    return "SYSTEM";
  }
}
