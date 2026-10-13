import type { NextConfig } from "next";

const API = process.env.API_BASE_URL ?? "http://localhost:8000";

const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "no-referrer" },
];

const config: NextConfig = {
  poweredByHeader: false,
  reactStrictMode: true,
  output: "standalone",
  // Same-origin API: cookies (HttpOnly session + CSRF) work without CORS.
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${API}/api/:path*` },
      { source: "/u/:token", destination: `${API}/u/:token` },
    ];
  },
  async headers() {
    return [
      { source: "/:path*", headers: securityHeaders },
      // Dashboards and auth pages are never indexed. Only /privacy/* is public and indexable.
      ...["/operator/:path*", "/operator", "/client/:path*", "/client", "/login", "/set-password", "/forgot-password"].map((source) => ({
        source,
        headers: [{ key: "X-Robots-Tag", value: "noindex, nofollow" }],
      })),
    ];
  },
};

export default config;
