import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { activeTheme, applyTheme } from "./lib/theme";
import "./styles.css";

applyTheme(activeTheme());

const root = document.getElementById("root");
if (!root) throw new Error("missing #root element");

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
