import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  experimental: {
    // Video uploads are proxied through Next.js to FastAPI. The default 10 MB
    // proxy buffer truncates ordinary video files and produces a socket reset.
    proxyClientMaxBodySize: "500mb",
  },
  async rewrites() {
    const backend = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8000";

    return [
      {
        source: "/api/:path*",
        destination: `${backend}/api/:path*`,
      },
      {
        source: "/media/:path*",
        destination: `${backend}/media/:path*`,
      },
    ];
  },
};

export default nextConfig;
