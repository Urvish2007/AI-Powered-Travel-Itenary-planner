import type { NextConfig } from "next";

/** The origin of a URL, or null when it is unset or not a URL. */
function originOf(url: string | undefined): string | null {
  if (!url) return null;
  try {
    return new URL(url).origin;
  } catch {
    return null;
  }
}

const isDev = process.env.NODE_ENV === "development";
const apiOrigin = originOf(process.env.NEXT_PUBLIC_API_URL);

/**
 * Content-Security-Policy, sent in **Report-Only** mode: the browser reports
 * what would have been blocked (in the console) and blocks nothing. Promote it
 * to `Content-Security-Policy` once a release shows no violations.
 *
 * Built from what the app actually loads: its own origin and the API; the
 * Google and Apple sign-in scripts and frames; images over HTTPS (a place's own
 * photo may live on any host, and a hosted tile provider is loaded directly).
 * Inline scripts and styles are allowed because Next's bootstrap and Leaflet
 * use them; replacing that with nonces needs dynamic rendering for every page.
 */
const contentSecurityPolicy = [
  "default-src 'self'",
  [
    "script-src 'self' 'unsafe-inline'",
    "https://accounts.google.com https://appleid.cdn-apple.com",
    // Fast Refresh evaluates code in development only.
    isDev ? "'unsafe-eval'" : "",
  ].join(" "),
  "style-src 'self' 'unsafe-inline' https://accounts.google.com",
  "img-src 'self' data: blob: https:",
  "font-src 'self' data:",
  [
    "connect-src 'self'",
    apiOrigin ?? "",
    "https://accounts.google.com",
    isDev ? "ws: wss:" : "",
  ].join(" "),
  "frame-src https://accounts.google.com https://appleid.apple.com",
  "form-action 'self' https://appleid.apple.com",
  "object-src 'none'",
  "base-uri 'self'",
  "frame-ancestors 'none'",
]
  .map((directive) => directive.replace(/\s+/g, " ").trim())
  .join("; ");

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
          {
            key: "Content-Security-Policy-Report-Only",
            value: contentSecurityPolicy,
          },
        ],
      },
    ];
  },
};

export default nextConfig;
