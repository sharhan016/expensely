import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  distDir: process.env.NEXT_DIST_DIR ?? ".next",
  async rewrites() {
    const apiBaseUrl = process.env.API_BASE_URL ?? "http://127.0.0.1:8000";
    return [{ source: "/api/:path*", destination: `${apiBaseUrl}/:path*` }];
  },
};

export default nextConfig;
