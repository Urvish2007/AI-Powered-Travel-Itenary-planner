#!/usr/bin/env python3
"""Generate the isometric 3D illustrations used by README.md.

GitHub cannot run CSS/WebGL in a README, but it renders SVG images. This script
draws true isometric (30 degree) projections with the painter's algorithm, so the
"3D" artwork is regenerated from code instead of being hand-edited.

    python docs/build_assets.py

Output: docs/assets/{hero,architecture,pipeline}.svg  (no dependencies).
"""

from __future__ import annotations

import math
from pathlib import Path

OUT = Path(__file__).parent / "assets"
FONT = "'Segoe UI', 'Inter', Helvetica, Arial, sans-serif"
MONO = "'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace"
COS, SIN = math.cos(math.radians(30)), 0.5


# --------------------------------------------------------------------------- #
# Isometric primitives
# --------------------------------------------------------------------------- #
def _shade(hex_color: str, factor: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    if factor >= 1:  # lighten towards white
        t = factor - 1
        r, g, b = (round(c + (255 - c) * t) for c in (r, g, b))
    else:
        r, g, b = (round(c * factor) for c in (r, g, b))
    return f"#{r:02x}{g:02x}{b:02x}"


class Iso:
    """Maps world (x, y, z) to screen space. +x goes right-down, +y left-down."""

    def __init__(self, ox: float, oy: float, unit: float):
        self.ox, self.oy, self.u = ox, oy, unit

    def p(self, x: float, y: float, z: float = 0) -> tuple[float, float]:
        return (
            self.ox + (x - y) * COS * self.u,
            self.oy + (x + y) * SIN * self.u - z,
        )

    def poly(self, pts, fill, stroke="none", sw=1, opacity=1.0, extra="") -> str:
        d = " ".join(f"{px:.1f},{py:.1f}" for px, py in (self.p(*q) for q in pts))
        return (
            f'<polygon points="{d}" fill="{fill}" stroke="{stroke}" '
            f'stroke-width="{sw}" stroke-linejoin="round" opacity="{opacity}" {extra}/>'
        )

    def cuboid(self, x, y, z, w, d, h, color, *, glow=False, top=None) -> str:
        """Three visible faces: top, left (y+d side) and right (x+w side)."""
        edge = _shade(color, 1.35)
        out = []
        if glow:
            out.append(
                self.poly(
                    [(x, y, z), (x + w, y, z), (x + w, y + d, z), (x, y + d, z)],
                    color,
                    opacity=0.35,
                    extra='filter="url(#blur)"',
                )
            )
        out.append(
            self.poly(
                [(x, y + d, z), (x + w, y + d, z), (x + w, y + d, z + h), (x, y + d, z + h)],
                _shade(color, 0.78),
                edge,
                0.6,
            )
        )
        out.append(
            self.poly(
                [(x + w, y, z), (x + w, y + d, z), (x + w, y + d, z + h), (x + w, y, z + h)],
                _shade(color, 0.55),
                edge,
                0.6,
            )
        )
        out.append(
            self.poly(
                [(x, y, z + h), (x + w, y, z + h), (x + w, y + d, z + h), (x, y + d, z + h)],
                top or _shade(color, 1.18),
                edge,
                0.8,
            )
        )
        return "\n".join(out)

    def shadow(self, x, y, w, d, opacity=0.35) -> str:
        return self.poly(
            [(x + 0.6, y + 0.6, 0), (x + w + 0.9, y + 0.6, 0), (x + w + 0.9, y + d + 0.9, 0), (x + 0.6, y + d + 0.9, 0)],
            "#000",
            opacity=opacity,
            extra='filter="url(#blur)"',
        )


def defs(w: int, h: int, a="#0b1020", b="#1a1446") -> str:
    return f"""
<defs>
  <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="{a}"/><stop offset="1" stop-color="{b}"/>
  </linearGradient>
  <linearGradient id="title" x1="0" y1="0" x2="1" y2="0">
    <stop offset="0" stop-color="#ffffff"/><stop offset="1" stop-color="#a5b4fc"/>
  </linearGradient>
  <radialGradient id="halo" cx="0.5" cy="0.5" r="0.5">
    <stop offset="0" stop-color="#6366f1" stop-opacity="0.55"/>
    <stop offset="1" stop-color="#6366f1" stop-opacity="0"/>
  </radialGradient>
  <filter id="blur" x="-30%" y="-30%" width="160%" height="160%"><feGaussianBlur stdDeviation="9"/></filter>
  <filter id="soft" x="-30%" y="-30%" width="160%" height="160%"><feGaussianBlur stdDeviation="3"/></filter>
  <marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
    <path d="M0,0 L10,5 L0,10 z" fill="#c7d2fe"/>
  </marker>
  <pattern id="grid" width="32" height="32" patternUnits="userSpaceOnUse">
    <path d="M32 0H0V32" fill="none" stroke="#ffffff" stroke-opacity="0.045"/>
  </pattern>
</defs>
<rect width="{w}" height="{h}" rx="22" fill="url(#bg)"/>
<rect width="{w}" height="{h}" rx="22" fill="url(#grid)"/>"""


def svg(w: int, h: int, body: str, title: str, desc: str, **kw) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
        f'role="img" aria-labelledby="t d" font-family="{FONT}">'
        f"<title id=\"t\">{title}</title><desc id=\"d\">{desc}</desc>"
        f"{defs(w, h, **kw)}{body}</svg>"
    )


def pin(x, y, color="#f43f5e") -> str:
    return (
        f'<g transform="translate({x:.1f},{y:.1f})">'
        f'<ellipse cx="0" cy="2" rx="9" ry="3.5" fill="#000" opacity="0.35"/>'
        f'<path d="M0,0 C-11,-14 -11,-30 0,-30 C11,-30 11,-14 0,0 Z" fill="{color}" stroke="#fff" stroke-opacity="0.7" stroke-width="1.2"/>'
        f'<circle cx="0" cy="-21" r="4" fill="#fff"/></g>'
    )


# --------------------------------------------------------------------------- #
# 1) Hero banner
# --------------------------------------------------------------------------- #
def hero() -> str:
    W, H = 1280, 420
    iso = Iso(985, 118, 40)
    body = [f'<ellipse cx="985" cy="215" rx="330" ry="190" fill="url(#halo)"/>']

    # Base map slab with tiled "streets".
    body.append(iso.shadow(-1, -1, 9, 9, 0.5))
    body.append(iso.cuboid(-0.5, -0.5, 0, 8, 8, 14, "#1e2a5a", top="#243170"))
    for i in range(1, 8):  # grid lines on the slab top
        a, b = iso.p(i - 0.5, -0.5, 14), iso.p(i - 0.5, 7.5, 14)
        c, d = iso.p(-0.5, i - 0.5, 14), iso.p(7.5, i - 0.5, 14)
        body.append(f'<path d="M{a[0]:.1f},{a[1]:.1f}L{b[0]:.1f},{b[1]:.1f}M{c[0]:.1f},{c[1]:.1f}L{d[0]:.1f},{d[1]:.1f}" stroke="#818cf8" stroke-opacity="0.28" fill="none"/>')

    # Stops: (x, y, height, colour)
    stops = [
        (0.6, 5.2, 46, "#22d3ee"),
        (2.4, 2.2, 96, "#818cf8"),
        (4.8, 4.9, 64, "#f472b6"),
        (5.2, 0.9, 130, "#a78bfa"),
        (1.2, 0.2, 30, "#fbbf24"),
        (3.6, 6.1, 38, "#34d399"),
    ]
    blocks = []
    for x, y, hgt, col in stops:
        blocks.append((x + y, x, y, hgt, col))
    for _, x, y, hgt, col in sorted(blocks):
        body.append(iso.cuboid(x, y, 14, 1.5, 1.5, hgt, col, glow=True))
        # window strips
        for k in range(1, int(hgt // 22)):
            z = 14 + k * 20
            body.append(iso.poly([(x + 0.25, y + 1.5, z), (x + 1.25, y + 1.5, z), (x + 1.25, y + 1.5, z + 6), (x + 0.25, y + 1.5, z + 6)], "#ffffff", opacity=0.25))

    # Route hopping across rooftops.
    order = [0, 4, 1, 3, 2, 5]
    pts = []
    for i in order:
        x, y, hgt, _ = stops[i]
        pts.append(iso.p(x + 0.75, y + 0.75, 14 + hgt))
    path = "M" + " L".join(f"{px:.1f},{py:.1f}" for px, py in pts)
    body.append(f'<path d="{path}" stroke="#fde68a" stroke-width="3" stroke-dasharray="2 9" stroke-linecap="round" fill="none"/>')
    for n, (px, py) in enumerate(pts, 1):
        body.append(pin(px, py - 2, "#f43f5e" if n in (1, len(pts)) else "#6366f1"))

    # Floating mini cubes (depth cue)
    for (fx, fy, fz, c, s) in [(8.4, 1.0, 175, "#22d3ee", 0.7), (-0.8, 3.0, 105, "#f472b6", 0.55), (7.8, 6.8, 80, "#fbbf24", 0.5)]:
        body.append(iso.cuboid(fx, fy, fz, s, s, s * 40, c, glow=True))

    # Copy
    text = f"""
<g>
  <text x="64" y="104" font-size="15" font-weight="700" letter-spacing="4" fill="#a5b4fc">AI-POWERED</text>
  <text x="62" y="170" font-size="56" font-weight="800" fill="url(#title)">Travel Itinerary</text>
  <text x="62" y="232" font-size="56" font-weight="800" fill="url(#title)">Planner</text>
  <text x="64" y="274" font-size="19" fill="#c7d2fe">An LLM decides <tspan font-weight="700" fill="#fff">what is worth doing</tspan>.</text>
  <text x="64" y="302" font-size="19" fill="#c7d2fe">Google OR-Tools decides <tspan font-weight="700" fill="#fff">when and in what order</tspan>.</text>
  <g font-family="{MONO}" font-size="13" font-weight="600">
    <rect x="64" y="332" width="104" height="30" rx="15" fill="#312e81" stroke="#6366f1"/><text x="116" y="352" text-anchor="middle" fill="#e0e7ff">FastAPI</text>
    <rect x="178" y="332" width="112" height="30" rx="15" fill="#312e81" stroke="#6366f1"/><text x="234" y="352" text-anchor="middle" fill="#e0e7ff">Next.js 16</text>
    <rect x="300" y="332" width="112" height="30" rx="15" fill="#312e81" stroke="#6366f1"/><text x="356" y="352" text-anchor="middle" fill="#e0e7ff">MongoDB</text>
    <rect x="422" y="332" width="116" height="30" rx="15" fill="#312e81" stroke="#6366f1"/><text x="480" y="352" text-anchor="middle" fill="#e0e7ff">OR-Tools</text>
  </g>
</g>"""
    return svg(W, H, "\n".join(body) + text, "AI-Powered Travel Itinerary Planner",
               "Isometric 3D city blocks on a map tile, linked by a dotted route with map pins.")


# --------------------------------------------------------------------------- #
# 2) Architecture stack
# --------------------------------------------------------------------------- #
def architecture() -> str:
    W, H = 1240, 740
    GAP = 120
    iso = Iso(330, 479, 32)
    SW, SD, SH = 9.5, 5.2, 14  # slab size
    layers = [  # top to bottom on screen; drawn bottom-first
        dict(z=3 * GAP, c="#6366f1", name="Client", tech="Next.js 16 · React 19 · TanStack Query · Leaflet",
             items=[("#a5b4fc", "Pages"), ("#818cf8", "Map"), ("#c7d2fe", "State")]),
        dict(z=2 * GAP, c="#06b6d4", name="API", tech="FastAPI · JWT + OAuth · rate limiting · Prometheus",
             items=[("#67e8f9", "Auth"), ("#22d3ee", "Trips"), ("#a5f3fc", "Places")]),
        dict(z=GAP, c="#a855f7", name="Intelligence", tech="LLM (Gemini / Groq) · embeddings · OR-Tools solver",
             items=[("#d8b4fe", "LLM"), ("#c084fc", "Embed"), ("#f0abfc", "Solver")]),
        dict(z=0, c="#10b981", name="Data", tech="MongoDB (Beanie) · Redis cache · Celery workers",
             items=[("#6ee7b7", "Mongo"), ("#34d399", "Redis"), ("#a7f3d0", "Celery")]),
    ]
    body = [f'<ellipse cx="330" cy="420" rx="300" ry="320" fill="url(#halo)"/>']

    for layer in reversed(layers):  # painter: bottom (z=0) first
        z = layer["z"]
        body.append(iso.shadow(0, 0, SW, SD, 0.25) if z == 0 else "")
        body.append(iso.cuboid(0, 0, z, SW, SD, SH, layer["c"], glow=(z == 0)))
        # component blocks standing on the slab
        slot = SW / 3
        for i, (col, label) in enumerate(layer["items"]):
            bx, by = i * slot + 0.55, SD - 2.0 - 0.5
            bh = 26 + (i % 2) * 8
            body.append(iso.cuboid(bx, by, z + SH, slot - 1.1, 2.0, bh, col, top=_shade(col, 1.25)))
            lx, ly = iso.p(bx + (slot - 1.1) / 2, by + 2.0, z + SH + bh * 0.55)
            body.append(f'<text x="{lx:.1f}" y="{ly + 4:.1f}" font-size="11.5" font-weight="700" text-anchor="middle" fill="#0b1020">{label}</text>')

        # Callout on the right
        ex, ey = iso.p(SW, SD / 2, z + SH / 2)
        tx, ty = 680, ey
        body.append(f'<path d="M{ex:.1f},{ey:.1f} L{tx - 14},{ty:.1f}" stroke="{layer["c"]}" stroke-width="1.6" stroke-dasharray="3 5" fill="none"/>')
        body.append(f'<circle cx="{ex:.1f}" cy="{ey:.1f}" r="4" fill="{layer["c"]}"/>')
        body.append(f'<rect x="{tx - 14}" y="{ty - 36}" width="500" height="68" rx="12" fill="#ffffff" fill-opacity="0.06" stroke="{layer["c"]}" stroke-opacity="0.7"/>')
        body.append(f'<text x="{tx + 6}" y="{ty - 10}" font-size="21" font-weight="800" fill="#fff">{layer["name"]}</text>')
        body.append(f'<text x="{tx + 6}" y="{ty + 14}" font-size="13.5" fill="#c7d2fe">{layer["tech"]}</text>')

    # Request arrow down the left side
    x0, y0 = iso.p(0, SD, 3 * GAP + 30)
    x1, y1 = iso.p(0, SD, 10)
    body.append(f'<path d="M{x0 - 48:.1f},{y0:.1f} L{x1 - 48:.1f},{y1:.1f}" stroke="#c7d2fe" stroke-width="2" marker-end="url(#arrow)" stroke-dasharray="6 6" fill="none"/>')
    body.append(f'<text x="{x0 - 58:.1f}" y="{(y0 + y1) / 2:.1f}" font-size="12" fill="#a5b4fc" text-anchor="middle" transform="rotate(-90 {x0 - 58:.1f} {(y0 + y1) / 2:.1f})" letter-spacing="3">REQUEST FLOW</text>')

    head = '<text x="40" y="52" font-size="26" font-weight="800" fill="#fff">System architecture</text><text x="40" y="78" font-size="14" fill="#a5b4fc">Four layers, one direction of dependency.</text>'
    return svg(W, H, head + "\n".join(b for b in body if b), "Layered architecture",
               "Isometric stack: Next.js client, FastAPI API, AI and OR-Tools intelligence layer, MongoDB and Redis data layer.")


# --------------------------------------------------------------------------- #
# 3) Planning pipeline
# --------------------------------------------------------------------------- #
def pipeline() -> str:
    W, H = 1180, 790
    iso = Iso(120, 140, 36)
    nodes = [
        ("parse_intent", "LLM", "#a855f7", "Read the brief"),
        ("retrieve", "Data", "#f59e0b", "Vector + filter recall"),
        ("score", "Data", "#f59e0b", "Rank by interests"),
        ("optimize", "OR-Tools", "#06b6d4", "Days, order, time windows"),
        ("assemble", "Code", "#6366f1", "Stops, meals, gaps"),
        ("narrate", "LLM", "#a855f7", "Human-friendly copy"),
        ("validate", "Guard", "#10b981", "Re-check every rule"),
    ]
    body = []
    step, size = 4.4, 2.0
    pts = []
    for i, (name, kind, col, sub) in enumerate(nodes):
        x, y = i * step, 0
        hgt = 56 if kind == "OR-Tools" else 38
        pts.append((x, y, hgt, name, kind, col, sub))
    # connectors under blocks
    for i in range(len(pts) - 1):
        a = iso.p(pts[i][0] + size + 0.15, size / 2, 6)
        b = iso.p(pts[i + 1][0] - 0.2, size / 2, 6)
        body.append(f'<path d="M{a[0]:.1f},{a[1]:.1f} L{b[0]:.1f},{b[1]:.1f}" stroke="#c7d2fe" stroke-width="2.2" marker-end="url(#arrow)" fill="none"/>')
    for i, (x, y, hgt, name, kind, col, sub) in enumerate(pts):
        body.append(iso.shadow(x, y, size, size, 0.4))
        body.append(iso.cuboid(x, y, 0, size, size, hgt, col, glow=(kind == "OR-Tools")))
        tx, ty = iso.p(x + size / 2, y + size / 2, hgt)
        body.append(f'<text x="{tx:.1f}" y="{ty + 5:.1f}" font-size="20" font-weight="800" text-anchor="middle" fill="#fff" fill-opacity="0.92">{i + 1}</text>')
        lx, ly = iso.p(x + size, y + size, 0)
        base = ly + 34
        body.append(f'<text x="{lx - 38:.1f}" y="{base:.1f}" font-size="15.5" font-weight="800" text-anchor="middle" fill="#fff" font-family="{MONO}">{name}</text>')
        body.append(f'<text x="{lx - 38:.1f}" y="{base + 20:.1f}" font-size="12" text-anchor="middle" fill="#a5b4fc">{sub}</text>')
        body.append(f'<rect x="{lx - 38 - 36:.1f}" y="{base + 30:.1f}" width="72" height="20" rx="10" fill="{col}" fill-opacity="0.25" stroke="{col}"/>'
                    f'<text x="{lx - 38:.1f}" y="{base + 44.5:.1f}" font-size="11" font-weight="700" text-anchor="middle" fill="#fff">{kind}</text>')

    head = '<text x="40" y="52" font-size="26" font-weight="800" fill="#fff">Planning pipeline</text><text x="40" y="78" font-size="14" fill="#a5b4fc">Seven async nodes sharing one PlanningState. LangGraph-ready, runs fine without it.</text>'
    return svg(W, H, head + "\n".join(body), "AI planning pipeline",
               "Seven isometric blocks in a staircase: parse intent, retrieve, score, optimize, assemble, narrate, validate.")


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for name, fn in [("hero", hero), ("architecture", architecture), ("pipeline", pipeline)]:
        (OUT / f"{name}.svg").write_text(fn(), encoding="utf-8")
        print("wrote", OUT / f"{name}.svg")
