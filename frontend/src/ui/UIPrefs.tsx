import React, { createContext, useContext, useState } from "react";

/**
 * Global (in-memory) preferences for small UI toggles that should persist
 * across screens for the duration of a session. Currently:
 *  - sourcesExpanded: whether the "Fonti" collapsible on a feud detail page
 *    is open. Sharing this across feud pages avoids re-collapsing every time
 *    the user navigates between feuds.
 *
 * State is intentionally NOT persisted to storage — resetting on cold app
 * launch is acceptable UX.
 */
type UIPrefs = {
  sourcesExpanded: boolean;
  setSourcesExpanded: (v: boolean) => void;
};

const UIPrefsContext = createContext<UIPrefs | undefined>(undefined);

export function UIPrefsProvider({ children }: { children: React.ReactNode }) {
  const [sourcesExpanded, setSourcesExpanded] = useState(false);
  return (
    <UIPrefsContext.Provider value={{ sourcesExpanded, setSourcesExpanded }}>
      {children}
    </UIPrefsContext.Provider>
  );
}

export function useUIPrefs() {
  const ctx = useContext(UIPrefsContext);
  if (!ctx) throw new Error("useUIPrefs must be inside UIPrefsProvider");
  return ctx;
}
