import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // drei's <Html> labels unmount a nested React root in cleanup, which React 19's Strict Mode double-check turns into a noisy
  // dev-only console error. Strict Mode is off so development stays quiet; production builds are unaffected either way.
  reactStrictMode: false,
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
