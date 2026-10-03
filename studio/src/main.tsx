import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import "./fonts.css";
import { App } from "./App";
import { SessionGate } from "./SessionGate";
import "./styles.css";
import "./browser-overlays.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <SessionGate><App /></SessionGate>
    </BrowserRouter>
  </React.StrictMode>,
);
