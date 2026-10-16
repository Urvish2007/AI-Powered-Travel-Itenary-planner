import { NextResponse } from "next/server";

const SOURCES: Record<string, string> = {
  clear: "https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png",
  streets: "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
  satellite: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
};

// The community tile servers' policies ask for an agent that identifies the
// app and a Referer naming the real site; a placeholder contact or a localhost
// Referer in production is exactly what gets a client blocked.
const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? "http://localhost:3000";
const UA = process.env.TILE_USER_AGENT ?? `Waypoint/1.0 (+${SITE_URL})`;
const REFERER = SITE_URL.endsWith("/") ? SITE_URL : `${SITE_URL}/`;

export async function GET(
  _req: Request,
  { params }: { params: Promise<{ style: string; z: string; x: string; y: string }> }
) {
  const { style, z, x, y } = await params;
  const tpl = SOURCES[style];
  const zi = Number(z), xi = Number(x), yi = Number(y);
  const max = 2 ** zi;
  
  if (!tpl || !Number.isInteger(zi) || zi < 0 || zi > 19 ||
      !Number.isInteger(xi) || !Number.isInteger(yi) ||
      xi < 0 || yi < 0 || xi >= max || yi >= max) {
    return NextResponse.json({ error: "bad request" }, { status: 400 });
  }

  // Handle subdomain and retina variables
  const url = tpl
    .replace("{z}", z)
    .replace("{x}", x)
    .replace("{y}", y)
    .replace("{s}", "a")
    .replace("{r}", "");
    
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 6000);
  
  try {
    const up = await fetch(url, {
      headers: { "User-Agent": UA, Referer: REFERER },
      signal: ctrl.signal,
      next: { revalidate: 86400 },
    });
    
    if (!up.ok) {
      return NextResponse.json({ error: "upstream", status: up.status }, { status: up.status === 404 ? 404 : 502 });
    }
    
    return new NextResponse(up.body, {
      status: 200,
      headers: {
        "Content-Type": up.headers.get("content-type") ?? "image/png",
        "Cache-Control": "public, max-age=86400, stale-while-revalidate=604800",
      },
    });
  } catch {
    return NextResponse.json({ error: "tile fetch failed" }, { status: 502 });
  } finally {
    clearTimeout(timer);
  }
}
