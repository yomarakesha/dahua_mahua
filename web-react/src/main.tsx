import React from "react";
import ReactDOM from "react-dom/client";
import { HashRouter } from "react-router-dom";
import {
  QueryClient,
  QueryClientProvider,
  QueryCache,
  MutationCache,
} from "@tanstack/react-query";
import App from "./App";
import { BrandingProvider } from "./lib/branding";
import ErrorBoundary from "./components/ErrorBoundary";
import { installConsoleCapture, recordEvent, shipLogs } from "./lib/diagnostics";
import "./i18n";
import "./index.css";

// ── Observability: capture console.warn/error into the diagnostics ring ────────
installConsoleCapture();

// ── Global uncaught-error capture → record + ship (crash-safe beacon) ──────────
if (typeof window !== "undefined") {
  window.onerror = (message, source, lineno, colno, error) => {
    recordEvent(
      "window.onerror",
      `${String(message)} @ ${source ?? "?"}:${lineno ?? 0}:${colno ?? 0}${
        error?.stack ? ` | ${error.stack}` : ""
      }`,
    );
    shipLogs("uncaught");
    return false; // don't suppress the browser's default logging
  };
  window.addEventListener("unhandledrejection", (ev) => {
    const r = (ev as PromiseRejectionEvent).reason;
    const msg =
      r instanceof Error ? `${r.name}: ${r.message}${r.stack ? ` | ${r.stack}` : ""}` : String(r);
    recordEvent("unhandledrejection", msg);
    shipLogs("uncaught");
  });
}

const queryClient = new QueryClient({
  // Record (do NOT ship) query/mutation errors with their key so the ring has
  // the API-failure trail when something else later trips a crash/ship.
  queryCache: new QueryCache({
    onError: (error, query) => {
      recordEvent(
        "query-error",
        `${JSON.stringify(query.queryKey)} → ${
          error instanceof Error ? error.message : String(error)
        }`,
      );
    },
  }),
  mutationCache: new MutationCache({
    onError: (error, _vars, _ctx, mutation) => {
      const key = mutation.options.mutationKey
        ? JSON.stringify(mutation.options.mutationKey)
        : "mutation";
      recordEvent(
        "mutation-error",
        `${key} → ${error instanceof Error ? error.message : String(error)}`,
      );
    },
  }),
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
      staleTime: 5_000,
    },
  },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <BrandingProvider>
          <HashRouter>
            <App />
          </HashRouter>
        </BrandingProvider>
      </QueryClientProvider>
    </ErrorBoundary>
  </React.StrictMode>,
);
