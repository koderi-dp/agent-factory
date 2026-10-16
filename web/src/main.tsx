import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "sonner";
import { queryClient } from "./lib/query";
import { App } from "./App";
import "./styles.css";
import "./factory.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
      <Toaster theme="dark" className="pylon-toaster" toastOptions={{ className: "pylon-toast" }} />
    </QueryClientProvider>
  </React.StrictMode>,
);
