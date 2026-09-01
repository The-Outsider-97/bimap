"use client";

import { useTheme } from "@/hooks/useTheme";

export function ThemeToggle() {
  const { theme, toggleTheme } = useTheme();

  return (
    <button
      className="theme-toggle"
      type="button"
      role="switch"
      aria-checked={theme === "dark"}
      aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
      onClick={toggleTheme}
    >
      <span aria-hidden="true">☀</span>

      <span className="theme-toggle__track">
        <span className="theme-toggle__thumb" />
      </span>

      <span aria-hidden="true">◐</span>
    </button>
  );
}
