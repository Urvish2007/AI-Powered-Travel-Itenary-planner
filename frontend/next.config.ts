import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Emits a self-contained server (.next/standalone) so the Docker image ships
  // only what it needs to run. Vercel ignores this setting.
  output: "standalone",

  images: {
    /**
     * Remote images are allow-listed rather than opened up.
     *
     * An unrestricted image host turns the optimizer into an open proxy that
     * anyone can point at any URL, at our bandwidth. `search` is deliberately
     * omitted for Unsplash because its CDN takes sizing through the query
     * string, which is exactly how `lib/images.ts` requests the right dimensions.
     */
    remotePatterns: [
      {
        protocol: "https",
        hostname: "images.unsplash.com",
        pathname: "/**",
      },
      // Licensed city photos from Wikimedia Commons (see fetch_city_photos.py).
      // Commons serves thumbnails from either host.
      {
        protocol: "https",
        hostname: "thumb.wikimedia.org",
        pathname: "/wikipedia/commons/**",
      },
      {
        protocol: "https",
        hostname: "upload.wikimedia.org",
        pathname: "/wikipedia/commons/**",
      },
      // Profile pictures from federated sign-in.
      {
        protocol: "https",
        hostname: "lh3.googleusercontent.com",
        pathname: "/**",
      },
    ],
    formats: ["image/avif", "image/webp"],
    // The widths the layout actually asks for: cards, half-width panels and
    // full-bleed heroes. Trimming the default list cuts wasted optimizer work.
    deviceSizes: [640, 828, 1080, 1200, 1920, 2048],
    imageSizes: [64, 96, 128, 256, 384],
    minimumCacheTTL: 60 * 60 * 24 * 7,
  },

  // Surfaced in the footer and the health check, so a deployed build can be
  // identified without guessing.
  env: {
    NEXT_PUBLIC_APP_VERSION: process.env.npm_package_version ?? "0.1.0",
  },

  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          {
            key: "Permissions-Policy",
            value: "camera=(), microphone=(), geolocation=(self)",
          },
        ],
      },
    ];
  },
};

export default nextConfig;
