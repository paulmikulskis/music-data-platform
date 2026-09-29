import type { NextConfig } from "next";
import path from "node:path";
const config: NextConfig = {
  output: "standalone",
  outputFileTracingRoot: path.resolve(import.meta.dirname, "../.."),
  poweredByHeader: false,
  transpilePackages: ["@mdp/showcase-auth", "@mdp/contracts", "@mdp/data-sdk"],
  // Workspace packages use NodeNext .js specifiers for their TypeScript sources.
  webpack(config) {
    config.resolve.extensionAlias = { ".js": [".ts", ".tsx", ".js"] };
    return config;
  },
  async redirects() {
    return [
      { source: "/rising", destination: "/songs?view=rising", statusCode: 301 },
      { source: "/picks/:id/:path*", destination: "/songs/picks/:id/:path*", statusCode: 301 },
      { source: "/picks", destination: "/songs?view=picks", statusCode: 301 },
      { source: "/today", destination: "/", statusCode: 301 },
      { source: "/draft", destination: "/songs?view=friday", statusCode: 301 },
      {
        source: "/holdings",
        has: [
          {
            type: "query",
            key: "view",
            value: "(?<view>rights|sources|collection)",
          },
        ],
        destination: "/sources?view=:view",
        statusCode: 301,
      },
      { source: "/holdings", destination: "/stack", statusCode: 301 },
      { source: "/library", destination: "/search", statusCode: 301 },
    ];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "Referrer-Policy", value: "no-referrer" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Cache-Control", value: "no-store" },
        ],
      },
      ...["/fonts/:path*", "/brand/:path*"].map((source) => ({
        source,
        headers: [{ key: "Cache-Control", value: "public, max-age=86400" }],
      })),
    ];
  },
};
export default config;
