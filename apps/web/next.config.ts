import type { NextConfig } from "next";

// In development the FastAPI app runs on its own port; the browser always calls same-origin `/api/...`.
// On Vercel, Services route `/api/*` to the Python backend, so no rewrite is needed there.
const apiUrl = process.env.CTRACE_API_URL ?? (process.env.NODE_ENV === "development" ? "http://localhost:8000" : undefined);

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  async rewrites() {
    return apiUrl ? [{ source: "/api/:path*", destination: `${apiUrl}/:path*` }] : [];
  },
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "X-Frame-Options", value: "SAMEORIGIN" },
          { key: "Permissions-Policy", value: "camera=(self), microphone=(), geolocation=()" },
        ],
      },
    ];
  },
};

export default nextConfig;
