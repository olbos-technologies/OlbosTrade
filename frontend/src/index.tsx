import React, { Suspense, lazy } from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import "./index.css";
import { AuthProvider } from "./auth/AuthContext";
import AuthGate from "./auth/AuthGate";

// Route-level code splitting: the public landing page and the terminal
// (recharts, every page module) are large and mutually exclusive on first
// load, so each is only fetched when its route is actually visited.
const App = lazy(() => import("./App"));
const Landing = lazy(() => import("./pages/Landing"));
const RequestAccess = lazy(() => import("./pages/RequestAccess"));
const Claim = lazy(() => import("./pages/Claim"));

function RouteFallback() {
  return (
    <div style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", background: "#080e1a", color: "#94a3b8", fontFamily: "monospace", fontSize: 12 }}>
      Loading…
    </div>
  );
}

// The terminal (App.tsx) keeps its existing internal page-switching behaviour
// unchanged — it is only mounted at /terminal/* instead of at the site root.
//
// AuthGate wraps the terminal, not the landing page: the marketing surface is
// public by design. When AUTH_ENABLED is false on the backend (the default)
// the gate renders the terminal straight through, so a single-operator install
// sees no change at all.
//
// The gate is UX, not a security boundary — the server authenticates every
// route itself and would 401 a browser that skipped this entirely.
ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <Suspense fallback={<RouteFallback />}>
        <Routes>
          <Route path="/" element={<Landing />} />
          {/* Both public, and deliberately OUTSIDE AuthProvider. Someone
              asking for an account has no session to resolve, and someone
              redeeming a setup token has no account yet — mounting the
              provider would fire /api/auth/status and install the 401
              interceptor on two screens whose whole premise is not being
              signed in. The backend allowlists exactly these two paths. */}
          <Route path="/request-access" element={<RequestAccess />} />
          <Route path="/claim" element={<Claim />} />
          {/* AuthProvider sits INSIDE this route, not above the switch. Above
              it, its mount effect called /api/auth/status and installed the
              fetch interceptor on every visit to the public landing page,
              which has no authenticated work to do — a wasted request on the
              most-visited page of a default auth-disabled install. */}
          <Route
            path="/terminal/*"
            element={<AuthProvider><AuthGate><App /></AuthGate></AuthProvider>}
          />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Suspense>
    </BrowserRouter>
  </React.StrictMode>
);
