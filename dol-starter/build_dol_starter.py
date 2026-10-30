#!/usr/bin/env python3
"""
RAPTI - "DOL Starter Wiring Diagram" poster generator.

    python3 build_dol_starter.py

Writes, next to this file:
    dol-starter-wiring-diagram.svg   self-contained vector (fonts + logo embedded)
    dol-starter-wiring-diagram.png   2160 x 2700 raster (4:5, social-media ready)

The motor and the push buttons are drawn with a tiny orthographic 3D engine
(Lambert + Blinn shading baked into SVG gradients). The panel uses a 2.5D
oblique projection so the printing on component fronts stays readable.

Requires: fontTools + brotli (pip install fonttools brotli) and Node with the
pre-installed Playwright/Chromium for the PNG export.
"""
import base64
import io
import math
import pathlib
import re
import subprocess
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
FONT_DIR = HERE / "fonts"
ASSETS = HERE / "assets"
OUT_SVG = HERE / "dol-starter-wiring-diagram.svg"
OUT_PNG = HERE / "dol-starter-wiring-diagram.png"

W, H = 1080, 1350

# ---------------------------------------------------------------- palette ---
# Brand tokens taken from rapti.net (assets/css/style.css)
NAVY, NAVY2, NAVY_LINE = "#0F1E2E", "#16293D", "#22405C"
DEEP = "#09131D"
TEAL, TEAL_D, TEAL_T = "#0A7E8C", "#075E68", "#E3F1F3"
AMBER, AMBER_D, AMBER_T = "#FFB100", "#8A5B00", "#FFF4DB"
LAMP = "#35D07F"
PAPER, INK, INK_SOFT, SLATE = "#F2F4F6", "#16232F", "#4A5A68", "#5B6B7A"
MUTED, SOFT = "#9FB4C8", "#CFE0EE"

# Wire colours (Bangladesh practice: R-Y-B phases, black neutral, G/Y earth)
WIRE = {"L1": "#D8323C", "L2": "#F2BA1D", "L3": "#2262D6", "N": "#24282D",
        "PE": "#2E9E4E", "CTL": "#E2463F"}

# ---------------------------------------------------------------- helpers ---


def f(v):
    s = f"{v:.1f}"
    if s.endswith(".0"):
        s = s[:-2]
    return "0" if s == "-0" else s


def pts(seq):
    return " ".join(f"{f(x)},{f(y)}" for x, y in seq)


def rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def hx(c):
    return "#%02X%02X%02X" % tuple(max(0, min(255, round(v))) for v in c)


def mix(a, b, t):
    a, b = rgb(a) if isinstance(a, str) else a, rgb(b) if isinstance(b, str) else b
    return hx(tuple(a[i] + (b[i] - a[i]) * t for i in range(3)))


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


DEFS = []
_gid = [0]


def gid(prefix="g"):
    _gid[0] += 1
    return f"{prefix}{_gid[0]}"


def lin_grad(x1, y1, x2, y2, stops, units="userSpaceOnUse"):
    i = gid("lg")
    st = "".join(f'<stop offset="{o:.3f}" stop-color="{c}"' +
                 (f' stop-opacity="{a}"' if a is not None else "") + "/>"
                 for o, c, a in [(s + (None,))[:3] for s in stops])
    if units == "userSpaceOnUse":
        DEFS.append(f'<linearGradient id="{i}" gradientUnits="userSpaceOnUse" x1="{f(x1)}" '
                    f'y1="{f(y1)}" x2="{f(x2)}" y2="{f(y2)}">{st}</linearGradient>')
    else:
        DEFS.append(f'<linearGradient id="{i}" x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}">{st}</linearGradient>')
    return f"url(#{i})"


def rad_grad(cx, cy, r, stops, fx=None, fy=None, units="objectBoundingBox"):
    i = gid("rg")
    st = "".join(f'<stop offset="{o:.3f}" stop-color="{c}"' +
                 (f' stop-opacity="{a}"' if a is not None else "") + "/>"
                 for o, c, a in [(s + (None,))[:3] for s in stops])
    extra = ""
    if fx is not None:
        extra = f' fx="{f(fx)}" fy="{f(fy)}"'
    if units == "userSpaceOnUse":
        DEFS.append(f'<radialGradient id="{i}" gradientUnits="userSpaceOnUse" cx="{f(cx)}" cy="{f(cy)}" '
                    f'r="{f(r)}"{extra}>{st}</radialGradient>')
    else:
        DEFS.append(f'<radialGradient id="{i}" cx="{cx}" cy="{cy}" r="{r}"{extra}>{st}</radialGradient>')
    return f"url(#{i})"


# ------------------------------------------------------------------ fonts ---
FONT_FILES = {
    ("Plex", 400): "IBMPlexSans-400.ttf", ("Plex", 500): "IBMPlexSans-500.ttf",
    ("Plex", 600): "IBMPlexSans-600.ttf", ("Plex", 700): "IBMPlexSans-700.ttf",
    ("PlexC", 600): "IBMPlexSansCondensed-600.ttf", ("PlexC", 700): "IBMPlexSansCondensed-700.ttf",
    ("PlexM", 500): "IBMPlexMono-500.ttf", ("PlexM", 600): "IBMPlexMono-600.ttf",
    ("PlexM", 700): "IBMPlexMono-700.ttf",
    ("Hind", 500): "HindSiliguri-500.ttf", ("Hind", 600): "HindSiliguri-600.ttf",
    ("Hind", 700): "HindSiliguri-700.ttf",
}
GF_CSS = ("https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700"
          "&family=IBM+Plex+Sans+Condensed:wght@600;700&family=IBM+Plex+Mono:wght@500;600;700"
          "&family=Hind+Siliguri:wght@500;600;700")
GF_NAME = {"IBM Plex Sans": "IBMPlexSans", "IBM Plex Sans Condensed": "IBMPlexSansCondensed",
           "IBM Plex Mono": "IBMPlexMono", "Hind Siliguri": "HindSiliguri"}


def ensure_fonts():
    if all((FONT_DIR / fn).exists() for fn in FONT_FILES.values()):
        return
    FONT_DIR.mkdir(exist_ok=True)
    req = urllib.request.Request(GF_CSS, headers={"User-Agent": "Mozilla/4.0"})  # old UA -> TTF urls
    css = urllib.request.urlopen(req, timeout=60).read().decode()
    for fam, w, url in re.findall(r"font-family: '([^']+)';.*?font-weight: (\d+);.*?src: url\(([^)]+)\)",
                                  css, re.S):
        dst = FONT_DIR / f"{GF_NAME[fam]}-{w}.ttf"
        if not dst.exists():
            dst.write_bytes(urllib.request.urlopen(url, timeout=60).read())


_metrics = {}


def text_w(s, size, fam="Plex", wt=500, ls=0.0):
    """Advance width of a string in px (no kerning) - used for pills/centering."""
    key = (fam, wt)
    if key not in _metrics:
        from fontTools.ttLib import TTFont
        tt = TTFont(FONT_DIR / FONT_FILES[key])
        _metrics[key] = (tt.getBestCmap(), tt["hmtx"].metrics, tt["head"].unitsPerEm)
    cmap, hmtx, upm = _metrics[key]
    tot = 0
    for ch in s:
        g = cmap.get(ord(ch))
        tot += hmtx[g][0] if g in hmtx else upm * 0.5
    return tot * size / upm + ls * len(s)


def font_css(used_text):
    from fontTools import subset
    out = []
    for (fam, wt), fn in FONT_FILES.items():
        opts = subset.Options()
        opts.flavor = "woff2"
        opts.layout_features = ["*"]
        opts.name_IDs = ["*"]
        opts.notdef_outline = True
        font = subset.load_font(str(FONT_DIR / fn), opts)
        sub = subset.Subsetter(opts)
        sub.populate(text=used_text)
        sub.subset(font)
        buf = io.BytesIO()
        subset.save_font(font, buf, opts)
        b64 = base64.b64encode(buf.getvalue()).decode()
        out.append(f"@font-face{{font-family:'{fam}';font-weight:{wt};"
                   f"src:url(data:font/woff2;base64,{b64}) format('woff2')}}")
    return "\n".join(out)


FAM = {"s": "'Plex','Hind',sans-serif", "c": "'PlexC','Plex',sans-serif",
       "m": "'PlexM',monospace", "b": "'Hind','Plex',sans-serif"}


def T(x, y, s, size=14, fam="s", wt=500, fill=INK, anchor="start", ls=0, extra=""):
    a = f' text-anchor="{anchor}"' if anchor != "start" else ""
    l = f' letter-spacing="{ls}"' if ls else ""
    return (f'<text x="{f(x)}" y="{f(y)}" class="{fam}" font-size="{size}" font-weight="{wt}" '
            f'fill="{fill}"{a}{l}{extra}>{esc(s)}</text>')


# ============================================================== 3D ENGINE ===
def add(a, b): return (a[0] + b[0], a[1] + b[1], a[2] + b[2])
def sub(a, b): return (a[0] - b[0], a[1] - b[1], a[2] - b[2])
def mul(a, k): return (a[0] * k, a[1] * k, a[2] * k)
def dot(a, b): return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def norm(a):
    l = math.sqrt(dot(a, a)) or 1.0
    return (a[0] / l, a[1] / l, a[2] / l)


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def basis(n):
    n = norm(n)
    t = (0, 1, 0) if abs(n[1]) < 0.9 else (1, 0, 0)
    u = norm(cross(t, n))
    v = cross(n, u)
    return u, v


class Cam:
    """Orthographic camera: world X right, Y up, Z toward the viewer."""

    def __init__(self, yaw, pitch, k=1.0, ox=0.0, oy=0.0):
        a, b = math.radians(yaw), math.radians(pitch)
        self.ca, self.sa, self.cb, self.sb = math.cos(a), math.sin(a), math.cos(b), math.sin(b)
        self.k, self.ox, self.oy = k, ox, oy

    def vd(self, p):
        x, y, z = p
        x1 = x * self.ca - z * self.sa
        z1 = x * self.sa + z * self.ca
        return (x1, y * self.cb - z1 * self.sb, y * self.sb + z1 * self.cb)

    def P(self, p):
        x, y, _ = self.vd(p)
        return (self.ox + self.k * x, self.oy - self.k * y)


LIGHT = norm((-0.50, 0.74, 0.46))
HALF = norm(add(LIGHT, (0, 0, 1)))
RIM = norm((0.92, 0.30, -0.22))

MAT = {
    "paint": dict(c=(16, 128, 143), amb=.26, dif=.82, spec=.55, shin=34, rim=.55),
    "paint_d": dict(c=(11, 98, 112), amb=.24, dif=.75, spec=.25, shin=20, rim=.35),
    "steel": dict(c=(186, 193, 200), amb=.34, dif=.70, spec=.95, shin=42, rim=.45),
    "chrome": dict(c=(205, 212, 218), amb=.30, dif=.62, spec=1.1, shin=60, rim=.5),
    "black": dict(c=(30, 33, 38), amb=.40, dif=.70, spec=.45, shin=30, rim=.35),
    "dkgrey": dict(c=(58, 64, 72), amb=.36, dif=.72, spec=.35, shin=26, rim=.35),
    "red": dict(c=(214, 34, 40), amb=.30, dif=.80, spec=.85, shin=48, rim=.30),
    "green": dict(c=(28, 170, 74), amb=.30, dif=.80, spec=.85, shin=48, rim=.30),
    "brass": dict(c=(196, 160, 82), amb=.32, dif=.75, spec=.8, shin=40, rim=.3),
    "cable": dict(c=(44, 48, 54), amb=.40, dif=.70, spec=.40, shin=22, rim=.35),
}
RIMC = (120, 225, 240)


def lit(mat, nv):
    m = MAT[mat] if isinstance(mat, str) else mat
    nv = norm(nv)
    d = max(0.0, dot(nv, LIGHT))
    sp = max(0.0, dot(nv, HALF)) ** m["shin"] * m["spec"]
    rm = max(0.0, dot(nv, RIM)) ** 2 * m["rim"]
    c = m["c"]
    return hx(tuple(c[i] * (m["amb"] + m["dif"] * d) + 255 * sp + RIMC[i] * rm for i in range(3)))


def hull(points):
    p = sorted(set((round(x, 2), round(y, 2)) for x, y in points))
    if len(p) < 3:
        return p

    def cr(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lo, up = [], []
    for q in p:
        while len(lo) >= 2 and cr(lo[-2], lo[-1], q) <= 0:
            lo.pop()
        lo.append(q)
    for q in reversed(p):
        while len(up) >= 2 and cr(up[-2], up[-1], q) <= 0:
            up.pop()
        up.append(q)
    return lo[:-1] + up[:-1]


def ellipse_d(C, A, B):
    k = 0.55228
    def P(a, b): return (C[0] + A[0] * a + B[0] * b, C[1] + A[1] * a + B[1] * b)
    seq = [((1, 0), (1, k), (k, 1), (0, 1)), ((0, 1), (-k, 1), (-1, k), (-1, 0)),
           ((-1, 0), (-1, -k), (-k, -1), (0, -1)), ((0, -1), (k, -1), (1, -k), (1, 0))]
    d = "M" + pts([P(*seq[0][0])])
    for s in seq:
        d += "C" + pts([P(*s[1]), P(*s[2]), P(*s[3])])
    return d + "Z"


def circle_axes(cam, c, axis, r):
    u, v = basis(axis)
    C = cam.P(c)
    A = cam.P(add(c, mul(u, r)))
    B = cam.P(add(c, mul(v, r)))
    return C, (A[0] - C[0], A[1] - C[1]), (B[0] - C[0], B[1] - C[1])


def revolve(cam, c0, c1, r0, r1, mat, n=96, stroke=None):
    """Side surface of a frustum between two coaxial circles, shaded."""
    axis = norm(sub(c1, c0))
    u, v = basis(axis)
    L = math.sqrt(dot(sub(c1, c0), sub(c1, c0)))
    ring = []
    for c, r in ((c0, r0), (c1, r1)):
        for i in range(n):
            t = 2 * math.pi * i / n
            ring.append(cam.P(add(c, add(mul(u, r * math.cos(t)), mul(v, r * math.sin(t))))))
    hp = hull(ring)
    A0, A1 = cam.P(c0), cam.P(c1)
    dx, dy = A1[0] - A0[0], A1[1] - A0[1]
    ll = math.hypot(dx, dy)
    w = (1.0, 0.0) if ll < 1e-6 else (-dy / ll, dx / ll)
    cm = mul(add(c0, c1), 0.5)
    Pm = cam.P(cm)
    rm = (r0 + r1) / 2
    slope = (r1 - r0) / L
    stops = []
    for i in range(160):
        t = 2 * math.pi * i / 160
        er = add(mul(u, math.cos(t)), mul(v, math.sin(t)))
        nv = cam.vd(norm(sub(er, mul(axis, slope))))
        if nv[2] < -0.02:
            continue
        p = cam.P(add(cm, mul(er, rm)))
        o = (p[0] - Pm[0]) * w[0] + (p[1] - Pm[1]) * w[1]
        stops.append((o, lit(mat, nv)))
    os_ = [(q[0] - Pm[0]) * w[0] + (q[1] - Pm[1]) * w[1] for q in hp]
    lo, hi = min(os_), max(os_)
    stops.sort()
    if hi - lo < 1e-6 or not stops:
        fill = lit(mat, cam.vd(u))
    else:
        keep, last = [], -1
        for o, c in stops:
            fo = (o - lo) / (hi - lo)
            if fo - last > 0.012:
                keep.append((max(0, min(1, fo)), c))
                last = fo
        fill = lin_grad(Pm[0] + w[0] * lo, Pm[1] + w[1] * lo, Pm[0] + w[0] * hi, Pm[1] + w[1] * hi, keep)
    sk = f' stroke="{stroke}" stroke-width=".6"' if stroke else ""
    return f'<polygon points="{pts(hp)}" fill="{fill}"{sk}/>'


def disc(cam, c, axis, r, mat, r_in=0.0, fill=None, extra=""):
    nv = cam.vd(norm(axis))
    if nv[2] <= 1e-4:
        return ""
    C, A, B = circle_axes(cam, c, axis, r)
    d = ellipse_d(C, A, B)
    if r_in:
        k = r_in / r
        d += ellipse_d(C, (A[0] * k, A[1] * k), (-B[0] * k, -B[1] * k))  # reversed winding
    return f'<path d="{d}" fill="{fill or lit(mat, nv)}" fill-rule="evenodd"{extra}/>'


def poly3(cam, P3, normal, mat, fill=None, extra=""):
    nv = cam.vd(norm(normal))
    if nv[2] <= 1e-4:
        return ""
    col = fill or lit(mat, nv)
    return f'<polygon points="{pts([cam.P(p) for p in P3])}" fill="{col}" stroke="{col}" stroke-width=".5" stroke-linejoin="round"{extra}/>'


def box(cam, x0, x1, y0, y1, z0, z1, mat, fills=None):
    faces = [((1, 0, 0), [(x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1)]),
             ((-1, 0, 0), [(x0, y0, z0), (x0, y0, z1), (x0, y1, z1), (x0, y1, z0)]),
             ((0, 1, 0), [(x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (x1, y1, z0)]),
             ((0, -1, 0), [(x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1)]),
             ((0, 0, 1), [(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]),
             ((0, 0, -1), [(x0, y0, z0), (x0, y1, z0), (x1, y1, z0), (x1, y0, z0)])]
    out = ""
    for n, q in faces:
        out += poly3(cam, q, n, mat, fill=(fills or {}).get(n))
    return out


def face_matrix(cam, origin, U, V):
    """SVG matrix mapping local (mm, y down) on a 3D plane to screen."""
    O = cam.P(origin)
    Ux, Uy = cam.P(add(origin, U))
    Vx, Vy = cam.P(add(origin, V))
    return f"matrix({(Ux - O[0]):.4f},{(Uy - O[1]):.4f},{(Vx - O[0]):.4f},{(Vy - O[1]):.4f},{O[0]:.2f},{O[1]:.2f})"


# ================================================================== MOTOR ===
def motor(cx, cy, k):
    """3-phase TEFC induction motor, drive end toward the viewer (right)."""
    cam = Cam(32, 17, k, cx, cy)
    X = (1, 0, 0)
    o = []

    def er(t):
        return (0, math.cos(t), math.sin(t))

    # floor shadow + reflection glow
    g = [cam.P((x, -112, z)) for x, z in ((-250, -118), (250, -118), (250, 118), (-250, 118))]
    o.append(f'<polygon points="{pts(g)}" fill="#000" opacity=".55" filter="url(#blur18)"/>')
    gc = cam.P((0, -112, 0))
    o.append(f'<ellipse cx="{f(gc[0])}" cy="{f(gc[1] + 6)}" rx="{f(270 * k)}" ry="{f(48 * k)}" '
             f'fill="{rad_grad(.5, .5, .5, [(0, TEAL, .45), (1, TEAL, 0)])}"/>')

    def foot(xf, zs):
        z0, z1 = sorted((zs * 70, zs * 114))
        w0, w1 = sorted((zs * 58, zs * 90))
        s = box(cam, xf - 36, xf + 36, -112, -97, z0, z1, "paint_d")
        s += box(cam, xf - 25, xf + 25, -97, -58, w0, w1, "paint_d")
        s += disc(cam, (xf, -96.8, zs * 101), (0, 1, 0), 6.5, "black", fill="#0B1218")
        return s

    for xf in (-84, 84):
        o.append(foot(xf, -1))

    # ---- non-drive end: fan cowl + end shield + frame ring
    o.append(revolve(cam, (-240, 0, 0), (-234, 0, 0), 88, 100, "paint"))
    o.append(revolve(cam, (-234, 0, 0), (-152, 0, 0), 100, 100, "paint"))
    for xg in (-212, -188, -164):
        ptsg = []
        for i in range(0, 181):
            t = math.radians(-100 + i * 1.6)
            p = add((xg, 0, 0), mul(er(t), 100.4))
            if cam.vd(er(t))[2] > 0.02:
                ptsg.append(cam.P(p))
        if len(ptsg) > 1:
            o.append(f'<polyline points="{pts(ptsg)}" fill="none" stroke="#06343B" stroke-width="{f(2.2 * k)}" opacity=".55"/>')
            o.append(f'<polyline points="{pts([(x + .6, y + 1.4 * k) for x, y in ptsg])}" fill="none" stroke="#7FE3EE" stroke-width="{f(1 * k)}" opacity=".35"/>')
    o.append(revolve(cam, (-152, 0, 0), (-146, 0, 0), 103, 103, "paint"))
    o.append(disc(cam, (-146, 0, 0), X, 103, "paint", r_in=97))
    o.append(revolve(cam, (-146, 0, 0), (-136, 0, 0), 97, 97, "paint_d"))
    o.append(revolve(cam, (-136, 0, 0), (-128, 0, 0), 101, 101, "paint"))
    o.append(disc(cam, (-128, 0, 0), X, 101, "paint_d"))

    # ---- stator frame with cooling fins
    NF = 40
    fins = []
    for i in range(NF):
        t = 2 * math.pi * (i + .5) / NF
        fins.append((cam.vd(er(t))[2], t))
    xa, xb = -128, 128

    def fin(t):
        e = er(t)
        et = (0, -math.sin(t), math.cos(t))
        n = et if cam.vd(et)[2] > 0 else mul(et, -1)
        q = [add((xa, 0, 0), mul(e, 86)), add((xb, 0, 0), mul(e, 86)),
             add((xb, 0, 0), mul(e, 101)), add((xa, 0, 0), mul(e, 101))]
        s = poly3(cam, q, n, "paint")
        a, b = cam.P(add((xa, 0, 0), mul(e, 101))), cam.P(add((xb, 0, 0), mul(e, 101)))
        tipc = lit("paint", cam.vd(e))
        s += f'<line x1="{f(a[0])}" y1="{f(a[1])}" x2="{f(b[0])}" y2="{f(b[1])}" stroke="{tipc}" stroke-width="{f(3.2 * k)}" stroke-linecap="round"/>'
        return s

    for z, t in sorted(fins):
        if z < 0:
            o.append(fin(t))
    o.append(revolve(cam, (xa, 0, 0), (xb, 0, 0), 87, 87, MAT["paint_d"]))
    for z, t in sorted(fins):
        if z >= 0:
            o.append(fin(t))

    # ---- drive end
    o.append(revolve(cam, (128, 0, 0), (136, 0, 0), 102, 102, "paint"))
    o.append(disc(cam, (136, 0, 0), X, 102, "paint"))
    prof = [(136, 98), (146, 96), (155, 86), (159, 70)]
    for (x0, r0), (x1, r1) in zip(prof, prof[1:]):
        o.append(revolve(cam, (x0, 0, 0), (x1, 0, 0), r0, r1, "paint"))
    o.append(disc(cam, (159, 0, 0), X, 70, "paint"))
    # end-shield bolts
    for tb in (35, 145, 215, 325):
        t = math.radians(tb)
        if cam.vd(er(t))[2] < -0.25:
            continue
        c0 = add((140, 0, 0), mul(er(t), 99))
        o.append(revolve(cam, c0, add(c0, (6, 0, 0)), 6, 6, "steel"))
        o.append(disc(cam, add(c0, (6, 0, 0)), X, 6, "steel"))
    # stiffening ribs on the shield face
    for tb in range(0, 360, 60):
        t = math.radians(tb + 30)
        a = cam.P(add((159.2, 0, 0), mul(er(t), 40)))
        b = cam.P(add((159.2, 0, 0), mul(er(t), 68)))
        o.append(f'<line x1="{f(a[0])}" y1="{f(a[1])}" x2="{f(b[0])}" y2="{f(b[1])}" stroke="#0A5662" stroke-width="{f(3 * k)}" stroke-linecap="round" opacity=".7"/>')
    o.append(revolve(cam, (159, 0, 0), (167, 0, 0), 38, 36, "paint"))
    o.append(disc(cam, (167, 0, 0), X, 36, "paint"))
    o.append(revolve(cam, (167, 0, 0), (171, 0, 0), 22, 22, "black"))
    o.append(disc(cam, (171, 0, 0), X, 22, "black"))
    # shaft + key
    o.append(revolve(cam, (171, 0, 0), (229, 0, 0), 14, 14, "steel"))
    o.append(box(cam, 184, 222, 9, 16.5, -3.6, 3.6, "steel"))
    o.append(revolve(cam, (229, 0, 0), (232, 0, 0), 14, 12, "chrome"))
    o.append(disc(cam, (232, 0, 0), X, 12, "chrome"))
    o.append(disc(cam, (232.2, 0, 0), X, 3.4, "black", fill="#2A2F35"))

    # ---- front feet
    for xf in (-84, 84):
        o.append(foot(xf, 1))

    # ---- nameplate (on the fins, front side)
    t0 = math.radians(66)
    e, et = er(t0), (0, -math.sin(t0), math.cos(t0))
    vup = mul(et, -1)
    corner = add(add((-108, 0, 0), mul(e, 103)), mul(vup, 25))
    mtx = face_matrix(cam, corner, (1, 0, 0), mul(vup, -1))
    np_ = [f'<g transform="{mtx}">',
           f'<rect x="0" y="0" width="118" height="50" rx="3" fill="{lin_grad(0, 0, 1, 1, [(0, "#F4F6F8"), (.55, "#C9D0D6"), (1, "#E9EDF0")], units="obj")}" stroke="#7C8792" stroke-width=".8"/>',
           f'<rect x="3" y="3" width="112" height="9" rx="1.5" fill="{NAVY}"/>',
           T(59, 10.2, "3~ INDUCTION MOTOR  ·  M1", 6.4, "c", 700, "#FFFFFF", "middle", .4),
           T(6, 21, "3.7 kW (5 HP)", 7, "c", 700, NAVY), T(112, 21, "400 V Δ", 7, "c", 700, NAVY, "end"),
           T(6, 30, "7.5 A   cos φ 0.83", 6, "m", 600, "#2A3742"), T(112, 30, "50 Hz", 6, "m", 600, "#2A3742", "end"),
           T(6, 38.5, "1440 r/min   IP55   Ins. F", 6, "m", 600, "#2A3742"),
           T(6, 46.5, "Frame 112M  ·  IEC 60034", 5.4, "m", 500, "#55626E")]
    for rx_, ry_ in ((2.6, 2.6), (115.4, 2.6), (2.6, 47.4), (115.4, 47.4)):
        np_.append(f'<circle cx="{rx_}" cy="{ry_}" r="1.3" fill="#8D98A3"/>')
    np_.append("</g>")
    o.append("".join(np_))

    # ---- lifting eye bolt
    o.append(revolve(cam, (92, 99, 0), (92, 107, 0), 9, 8, "steel"))
    C, A, B = circle_axes(cam, (92, 125, 0), (0, 0, 1), 16)
    o.append(f'<path d="{ellipse_d(C, A, B)}" fill="none" stroke="#5E6973" stroke-width="{f(9 * k)}"/>')
    o.append(f'<path d="{ellipse_d(C, A, B)}" fill="none" stroke="#C3CBD2" stroke-width="{f(6 * k)}"/>')
    o.append(f'<path d="{ellipse_d((C[0] - .8 * k, C[1] - .8 * k), A, B)}" fill="none" stroke="#FFFFFF" stroke-width="{f(1.6 * k)}" opacity=".7"/>')

    # ---- cable gland on the -X face of the terminal box (drawn before the box)
    pre = "".join(o)
    o = []
    gy, gz = 122, 4
    o.append(revolve(cam, (-53, gy, gz), (-63, gy, gz), 15, 15, "black"))
    o.append(revolve(cam, (-63, gy, gz), (-78, gy, gz), 12.5, 9.5, "black"))
    route3d = [(-78, gy, gz), (-140, gy + 1, gz), (-205, gy + 2, gz + 4), (-252, gy - 6, gz + 14),
               (-280, 72, 34), (-290, 0, 50), (-293, -78, 60), (-300, -108, 76)]
    route = [cam.P(p) for p in route3d]
    gland_svg = "".join(o)
    o = []
    # ---- terminal box
    o.append(box(cam, -46, 46, 82, 102, -50, 50, "paint_d"))
    o.append(box(cam, -53, 53, 102, 146, -61, 61, "paint"))
    o.append(box(cam, -56, 56, 146, 155, -64, 64, "paint"))
    o.append(box(cam, -50, 50, 155, 160, -58, 58, "paint"))
    for sx in (-44, 44):
        for sz in (-50, 50):
            o.append(disc(cam, (sx, 160.2, sz), (0, 1, 0), 4.6, "steel"))
    # earth symbol + label on the box front (+Z face)
    mtx = face_matrix(cam, (-53, 146, 61.2), (1, 0, 0), (0, -1, 0))
    o.append(f'<g transform="{mtx}"><rect x="31" y="10" width="44" height="24" rx="2" fill="#0B4F58" opacity=".55"/>'
             f'{T(53, 27, "M1", 13, "c", 700, "#BDF3F8", "middle", 1)}</g>')
    return pre, gland_svg, "".join(o), cam, route


# ============================================================ PUSH BUTTON ===
def push_button(cx, cy, k, kind):
    """22 mm push button, axis toward the viewer; contact block behind it."""
    cam = Cam(32, 17, k, cx, cy)
    Z = (0, 0, 1)
    head = "red" if kind == "stop" else "green"
    o = []
    sh = [cam.P(p) for p in ((-18, -26, -80), (18, -26, -80), (18, -26, 10), (-18, -26, 10))]
    o.append(f'<polygon points="{pts([(x + 10, y + 14) for x, y in sh])}" fill="#000" opacity=".35" filter="url(#blur6)"/>')
    # contact block (NC = red marker, NO = green marker)
    o.append(box(cam, -16, 16, -22, 22, -80, -34, "dkgrey"))
    mk = "#E0343C" if kind == "stop" else "#21B35A"
    o.append(poly3(cam, [(-10, 22.2, -76), (-10, 22.2, -40), (10, 22.2, -40), (10, 22.2, -76)],
                   (0, 1, 0), "dkgrey", fill=mk))
    mtx = face_matrix(cam, (-10, 22.4, -76), (1, 0, 0), (0, 0, 1))
    o.append(f'<g transform="{mtx}">{T(10, 22, "NC" if kind == "stop" else "NO", 10, "c", 700, "#FFFFFF", "middle")}</g>')
    # collar, nut, bezel, head
    o.append(box(cam, -17, 17, -17, 17, -34, -16, "black"))
    o.append(revolve(cam, (0, 0, -16), (0, 0, -12), 18, 18, "black"))
    o.append(disc(cam, (0, 0, -12), Z, 18, "black"))
    o.append(revolve(cam, (0, 0, -12), (0, 0, -4), 16, 15.5, "chrome"))
    o.append(disc(cam, (0, 0, -4), Z, 15.5, "chrome", r_in=12))
    o.append(revolve(cam, (0, 0, -4), (0, 0, 7), 12, 12, head))
    o.append(revolve(cam, (0, 0, 7), (0, 0, 9), 12, 11, head))
    C, A, B = circle_axes(cam, (0, 0, 9), Z, 11)
    hc = rgb(lit(head, cam.vd(Z)))
    face = rad_grad(.36, .3, .75, [(0, mix(hc, (255, 255, 255), .55)), (.45, hx(hc)), (1, mix(hc, (0, 0, 0), .35))])
    o.append(f'<path d="{ellipse_d(C, A, B)}" fill="{face}"/>')
    mtx = face_matrix(cam, (0, 0, 9.1), (1, 0, 0), (0, -1, 0))
    sym = '<circle cx="0" cy="0" r="4.6" fill="none" stroke="#FFFFFF" stroke-width="1.8"/>' if kind == "stop" \
        else '<rect x="-.95" y="-5.2" width="1.9" height="10.4" rx=".6" fill="#FFFFFF"/>'
    o.append(f'<g transform="{mtx}" opacity=".95">{sym}</g>')
    # screw terminals on the +X face of the contact block
    terms = {}
    names = ("11", "12") if kind == "stop" else ("13", "14")
    for nm, yy in zip(names, (11, -11)):
        c = (16.2, yy, -57)
        o.append(disc(cam, c, (1, 0, 0), 5.6, "black", fill="#16191D"))
        o.append(disc(cam, add(c, (.5, 0, 0)), (1, 0, 0), 4.2, "steel"))
        a, b = cam.P(add(c, (.8, 0, -3))), cam.P(add(c, (.8, 0, 3)))
        o.append(f'<line x1="{f(a[0])}" y1="{f(a[1])}" x2="{f(b[0])}" y2="{f(b[1])}" stroke="#4E5862" stroke-width="1.3"/>')
        terms[nm] = cam.P(add(c, (1, 0, 0)))
    return "".join(o), terms, cam


# ============================================================ PANEL (2.5D) ===
KX, KY = 0.32, 0.24


def S(X, Y, h=0.0):
    return (X - KX * h, Y + KY * h)


def obox(X0, Y0, X1, Y1, h0, h1, front, top=None, side=None, rx=0, front_fill=None):
    top = top or mix(front, "#FFFFFF", .35)
    side = side or mix(front, "#000000", .28)
    a = [S(X0, Y0, h1), S(X1, Y0, h1), S(X1, Y0, h0), S(X0, Y0, h0)]
    b = [S(X1, Y0, h1), S(X1, Y1, h1), S(X1, Y1, h0), S(X1, Y0, h0)]
    fx, fy = S(X0, Y0, h1)
    ff = front_fill or lin_grad(fx, fy, fx + (X1 - X0), fy + (Y1 - Y0),
                                [(0, mix(front, "#FFFFFF", .12)), (1, mix(front, "#000000", .08))])
    return (f'<polygon points="{pts(a)}" fill="{top}" stroke="{top}" stroke-width=".5" stroke-linejoin="round"/>'
            f'<polygon points="{pts(b)}" fill="{side}" stroke="{side}" stroke-width=".5" stroke-linejoin="round"/>'
            f'<rect x="{f(fx)}" y="{f(fy)}" width="{f(X1 - X0)}" height="{f(Y1 - Y0)}" rx="{rx}" fill="{ff}"/>')


def screw(x, y, r=5.2, label=None):
    g = rad_grad(.38, .32, .7, [(0, "#FFFFFF"), (.5, "#C5CCD3"), (1, "#6F7A85")])
    s = (f'<rect x="{f(x - r - 2.2)}" y="{f(y - r - 2.2)}" width="{f(2 * r + 4.4)}" height="{f(2 * r + 4.4)}" rx="2.2" fill="#2A3036" opacity=".55"/>'
         f'<circle cx="{f(x)}" cy="{f(y)}" r="{f(r)}" fill="{g}" stroke="#57626C" stroke-width=".6"/>'
         f'<path d="M{f(x - r * .62)},{f(y)}H{f(x + r * .62)}M{f(x)},{f(y - r * .62)}V{f(y + r * .62)}" stroke="#4A545E" stroke-width="1.2" stroke-linecap="round"/>')
    return s


def rpath(P, r=7):
    """Orthogonal polyline with rounded corners."""
    d = f"M{f(P[0][0])},{f(P[0][1])}"
    for i in range(1, len(P) - 1):
        a, b, c = P[i - 1], P[i], P[i + 1]
        l1 = math.hypot(b[0] - a[0], b[1] - a[1])
        l2 = math.hypot(c[0] - b[0], c[1] - b[1])
        rr = min(r, l1 / 2, l2 / 2)
        if l1 < 1e-6 or l2 < 1e-6:
            continue
        p1 = (b[0] + (a[0] - b[0]) * rr / l1, b[1] + (a[1] - b[1]) * rr / l1)
        p2 = (b[0] + (c[0] - b[0]) * rr / l2, b[1] + (c[1] - b[1]) * rr / l2)
        d += f"L{f(p1[0])},{f(p1[1])}Q{f(b[0])},{f(b[1])} {f(p2[0])},{f(p2[1])}"
    d += f"L{f(P[-1][0])},{f(P[-1][1])}"
    return d


def wire(P, color, w=4.4, r=7, shadow=True, pe=False, ferrule=None):
    d = rpath(P, r)
    o = []
    if shadow:
        o.append(f'<path d="{d}" fill="none" stroke="#000" stroke-opacity=".38" stroke-width="{f(w + 1)}" '
                 f'stroke-linecap="round" stroke-linejoin="round" transform="translate(2.4,3.2)" filter="url(#blur2)"/>')
    o.append(f'<path d="{d}" fill="none" stroke="{mix(color, "#000000", .45)}" stroke-width="{f(w)}" stroke-linecap="round" stroke-linejoin="round"/>')
    o.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{f(w * .74)}" stroke-linecap="round" stroke-linejoin="round"/>')
    if pe:
        o.append(f'<path d="{d}" fill="none" stroke="#F4D21F" stroke-width="{f(w * .74)}" stroke-dasharray="7 7" stroke-linejoin="round"/>')
    o.append(f'<path d="{d}" fill="none" stroke="#FFFFFF" stroke-opacity=".55" stroke-width="{f(max(.8, w * .2))}" '
             f'stroke-linecap="round" stroke-linejoin="round" transform="translate({f(-w * .16)},{f(-w * .16)})"/>')
    if ferrule:
        for (x, y, vert) in ferrule:
            if vert:
                o.append(f'<rect x="{f(x - w * .62)}" y="{f(y - 6)}" width="{f(w * 1.24)}" height="12" rx="1.5" fill="{lin_grad(0, 0, 1, 0, [(0, "#7D8790"), (.4, "#E9EDF1"), (1, "#8A949D")], units="obj")}"/>')
            else:
                o.append(f'<rect x="{f(x - 6)}" y="{f(y - w * .62)}" width="12" height="{f(w * 1.24)}" rx="1.5" fill="{lin_grad(0, 0, 0, 1, [(0, "#7D8790"), (.4, "#E9EDF1"), (1, "#8A949D")], units="obj")}"/>')
    return "".join(o)


def tag(x, y, s, fill="#FFFFFF", ink=INK, size=8.6):
    w = text_w(s, size, "PlexM", 700) + 7
    return (f'<rect x="{f(x - w / 2)}" y="{f(y - 6.4)}" width="{f(w)}" height="12.8" rx="3" fill="{fill}" stroke="#56616B" stroke-width=".7"/>'
            + T(x, y + 3.1, s, size, "m", 700, ink, "middle"))


def hole(x, y, w=10, h=5):
    return f'<rect x="{f(x - w / 2)}" y="{f(y - h / 2)}" width="{w}" height="{h}" rx="1.5" fill="#15191D"/>'


def mcb(X0, Y0, poles, labels_top, labels_bot, rating):
    """Modular circuit breaker, 18 mm per pole (36 px). Returns svg + terminal map."""
    w, hgt = 36 * poles, 180
    X1, Y1 = X0 + w, Y0 + hgt
    o = [obox(X0, Y0, X1, Y1, 0, 56, "#EEF0F2", top="#FFFFFF", side="#C3C9CF")]
    o.append(obox(X0 + 2, Y0 + 44, X1 - 2, Y1 - 44, 56, 68, "#F7F8F9", top="#FFFFFF", side="#CBD1D6"))
    fx, fy = S(0, 0, 56)
    nx, ny = S(0, 0, 68)
    term = {}
    for i in range(poles):
        cxp = X0 + 18 + 36 * i
        # shoulder screws + labels
        for (yy, lab, key) in ((Y0 + 22, labels_top[i], "t"), (Y1 - 22, labels_bot[i], "b")):
            sx, sy = cxp + fx, yy + fy
            o.append(hole(sx, sy - (13 if key == "t" else -13), 13, 6))
            o.append(screw(sx, sy, 5.4))
            o.append(T(sx + 9.5, sy + (-7 if key == "t" else 12), lab, 7.6, "m", 700, "#3B4650"))
        term[f"{i}t"] = S(cxp, Y0, 34)
        term[f"{i}b"] = S(cxp, Y1, 34)
        # nose: toggle, window, rating
        tx, ty = cxp + nx, Y0 + 44 + ny
        if i:
            o.append(f'<line x1="{f(tx - 18)}" y1="{f(ty + 2)}" x2="{f(tx - 18)}" y2="{f(ty + 90)}" stroke="#C3C9CF" stroke-width="1"/>')
        o.append(f'<rect x="{f(tx - 9)}" y="{f(ty + 8)}" width="18" height="42" rx="3" fill="#2B3138"/>')
        o.append(f'<rect x="{f(tx - 7)}" y="{f(ty + 9)}" width="14" height="22" rx="2.4" fill="{lin_grad(0, 0, 1, 0, [(0, "#4C555E"), (.5, "#767F88"), (1, "#3A424A")], units="obj")}"/>')
        o.append(f'<rect x="{f(tx - 6)}" y="{f(ty + 10)}" width="12" height="3" rx="1" fill="#9AA3AC"/>')
        o.append(T(tx, ty + 46, "I", 6.5, "m", 700, "#FFFFFF", "middle"))
        o.append(f'<rect x="{f(tx - 6)}" y="{f(ty + 56)}" width="12" height="7" rx="1.2" fill="#D9262E"/>')
        o.append(T(tx, ty + 75, rating, 8, "c", 700, "#2B3138", "middle"))
        o.append(f'<rect x="{f(tx - 15)}" y="{f(ty + 80)}" width="30" height="3" rx="1" fill="{TEAL}"/>')
    if poles > 1:
        y = Y0 + 44 + ny + 14
        o.append(f'<rect x="{f(X0 + 18 + nx - 9)}" y="{f(y)}" width="{f(36 * (poles - 1) + 18)}" height="6" rx="2" fill="#5E6770" opacity=".9"/>')
    return "".join(o), term


def contactor(X0, Y0):
    """3-pole contactor, 50 mm (100 px) wide; terminals: 13 1/L1 3/L2 5/L3 / 14 2/T1 4/T2 6/T3; A1, A2 on the front."""
    w, hgt = 100, 160
    X1, Y1 = X0 + w, Y0 + hgt
    o = [obox(X0, Y0, X1, Y1, 0, 70, "#E3E6E9", top="#F7F8F9", side="#B8BEC4")]
    o.append(obox(X0 + 5, Y0 + 30, X1 - 5, Y1 - 30, 70, 84, "#39414A", top="#5B646D", side="#262C32"))
    fx, fy = S(0, 0, 70)
    nx, ny = S(0, 0, 84)
    term = {}
    tops = [("13", 12), ("1/L1", 38), ("3/L2", 62), ("5/L3", 86)]
    bots = [("14", 12), ("2/T1", 38), ("4/T2", 62), ("6/T3", 86)]
    for (lab, dx) in tops:
        sx, sy = X0 + dx + fx, Y0 + 15 + fy
        o.append(hole(sx, sy - 11, 12, 5))
        o.append(screw(sx, sy, 5))
        term[lab] = S(X0 + dx, Y0, 36)
        term[lab + "f"] = (sx, sy)
    for (lab, dx) in bots:
        sx, sy = X0 + dx + fx, Y1 - 15 + fy
        o.append(screw(sx, sy, 5))
        term[lab] = S(X0 + dx, Y1, 36)
        term[lab + "f"] = (sx, sy)
    # nose printing
    bx, by = X0 + 5 + nx, Y0 + 30 + ny
    for (lab, dx) in tops:
        o.append(T(X0 + dx + nx, by + 10, lab, 6.6, "m", 600, "#C9D3DC", "middle"))
    for (lab, dx) in bots:
        o.append(T(X0 + dx + nx, by + 98, lab, 6.6, "m", 600, "#C9D3DC", "middle"))
    o.append(f'<rect x="{f(bx + 22)}" y="{f(by + 15)}" width="46" height="28" rx="4" fill="#1C2126" stroke="#5F6973" stroke-width=".8"/>')
    o.append(f'<rect x="{f(bx + 33)}" y="{f(by + 20)}" width="24" height="18" rx="2" fill="{lin_grad(0, 0, 0, 1, [(0, "#F7D44A"), (1, "#C99A12")], units="obj")}"/>')
    o.append(T(bx + 45, by + 33, "I", 10, "m", 700, "#2A2A2A", "middle"))
    o.append(T(bx + 45, by + 58, "K1", 15, "c", 700, "#FFFFFF", "middle"))
    o.append(T(bx + 45, by + 68.5, "AC-3 12 A · 230 V~", 6.4, "m", 600, "#AFC0CE", "middle"))
    # coil terminals on the front
    for lab, dx in (("A1", 13), ("A2", 68.9)):
        sx, sy = bx + dx, by + 81
        o.append(f'<rect x="{f(sx - 7.5)}" y="{f(sy - 7.5)}" width="15" height="15" rx="3" fill="#20262C"/>')
        o.append(screw(sx, sy, 4.6))
        o.append(T(sx + (10 if lab == "A1" else -10), sy + 3, lab, 7.4, "m", 700, "#FFD36A", "start" if lab == "A1" else "end"))
        term[lab] = (sx, sy)
    return "".join(o), term


def overload(X0, Y0):
    """Thermal overload relay mounted under K1 (78 px wide)."""
    w, hgt = 78, 120
    X1, Y1 = X0 + w, Y0 + hgt
    o = [obox(X0, Y0, X1, Y1, 0, 76, "#E3E6E9", top="#F7F8F9", side="#B8BEC4")]
    fx, fy = S(0, 0, 76)
    bx, by = X0 + fx, Y0 + fy
    term = {}
    for lab, dx in (("97", 11), ("98", 28), ("95", 49.3), ("96", 67)):
        sx, sy = bx + dx, by + 13
        o.append(screw(sx, sy, 4.6))
        o.append(T(sx, sy + 15, lab, 6.8, "m", 700, "#33404B", "middle"))
        term[lab] = (sx, sy)
    o.append(f'<path d="M{f(bx + 4)},{f(by + 32)}h34M{f(bx + 42)},{f(by + 32)}h32" stroke="#9AA6B1" stroke-width=".8"/>')
    o.append(T(bx + 19.5, by + 39, "NO", 6, "m", 700, "#1F8F4A", "middle"))
    o.append(T(bx + 58, by + 39, "NC", 6, "m", 700, "#C8262E", "middle"))
    # dial
    dcx, dcy = bx + 22, by + 64
    o.append(f'<circle cx="{f(dcx)}" cy="{f(dcy)}" r="13" fill="#F7F8F9" stroke="#8E99A4" stroke-width="1"/>')
    for i, v in enumerate(("7", "8", "9", "10")):
        a = math.radians(210 - i * 80)
        o.append(T(dcx + 18.5 * math.cos(a), dcy - 18.5 * math.sin(a) + 2.6, v, 6, "m", 600, "#44515C", "middle"))
    a = math.radians(210 - 0.5 * 80 / 1)
    o.append(f'<circle cx="{f(dcx)}" cy="{f(dcy)}" r="8.4" fill="#2A3138"/>')
    o.append(f'<line x1="{f(dcx)}" y1="{f(dcy)}" x2="{f(dcx + 8 * math.cos(a))}" y2="{f(dcy - 8 * math.sin(a))}" stroke="{AMBER}" stroke-width="2.2" stroke-linecap="round"/>')
    # reset / stop buttons
    o.append(f'<circle cx="{f(bx + 55)}" cy="{f(by + 56)}" r="7.5" fill="{rad_grad(.35, .3, .8, [(0, "#7FB2FF"), (1, "#1849B8")])}"/>')
    o.append(T(bx + 55, by + 72, "RESET", 5.6, "m", 700, "#1849B8", "middle"))
    o.append(f'<rect x="{f(bx + 48)}" y="{f(by + 78)}" width="15" height="9" rx="2" fill="#D5262E"/>')
    o.append(T(bx + 55.5, by + 95, "TEST", 5.6, "m", 700, "#9C1C22", "middle"))
    for lab, dx in (("2/T1", 38 - 22), ("4/T2", 62 - 22), ("6/T3", 86 - 22)):
        term[lab] = S(X0 + dx, Y1, 38)
        o.append(T(bx + dx, by + 115, lab, 5.6, "m", 700, "#55616C", "middle"))
    return "".join(o), term


def din_rail(X0, X1, yc):
    a = S(X0, yc - 35, 7.5)
    o = [f'<rect x="{f(X0 + 3)}" y="{f(yc - 33)}" width="{f(X1 - X0)}" height="70" rx="2" fill="#000" opacity=".25" filter="url(#blur2)"/>',
         f'<rect x="{f(a[0])}" y="{f(a[1])}" width="{f(X1 - X0)}" height="70" rx="1.5" fill="{lin_grad(0, 0, 0, 1, [(0, "#F4F6F8"), (.12, "#B9C1C9"), (.18, "#8E98A2"), (.5, "#C4CBD2"), (.82, "#8E98A2"), (.88, "#B9C1C9"), (1, "#E9EDF0")], units="obj")}"/>']
    for x in range(int(X0) + 10, int(X1) - 18, 30):
        o.append(f'<rect x="{f(x + a[0] - X0)}" y="{f(a[1] + 31)}" width="20" height="8" rx="4" fill="#4A535C"/>')
    return "".join(o)


def panel():
    o = []
    PX0, PY0, PX1, PY1 = 206, 198, 566, 800
    # mounting plate
    o.append(f'<rect x="{PX0 + 8}" y="{PY0 + 14}" width="{PX1 - PX0}" height="{PY1 - PY0}" rx="10" fill="#000" opacity=".55" filter="url(#blur14)"/>')
    o.append(f'<polygon points="{pts([S(PX0, PY0, 0), S(PX1, PY0, 0), (PX1 + 5, PY0 - 4), (PX0 + 5, PY0 - 4)])}" fill="#E7EBEE"/>')
    o.append(f'<polygon points="{pts([S(PX1, PY0, 0), S(PX1, PY1, 0), (PX1 + 5, PY1 - 4), (PX1 + 5, PY0 - 4)])}" fill="#7D8892"/>')
    o.append(f'<rect x="{PX0}" y="{PY0}" width="{PX1 - PX0}" height="{PY1 - PY0}" rx="6" '
             f'fill="{lin_grad(PX0, PY0, PX1, PY1, [(0, "#D5DADF"), (.55, "#BCC4CB"), (1, "#A9B2BA")])}"/>')
    o.append(f'<rect x="{PX0}" y="{PY0}" width="{PX1 - PX0}" height="{PY1 - PY0}" rx="6" fill="url(#brushed)" opacity=".35"/>')
    for hx_, hy_ in ((PX0 + 14, PY0 + 14), (PX1 - 14, PY0 + 14), (PX0 + 14, PY1 - 14), (PX1 - 14, PY1 - 14)):
        o.append(f'<circle cx="{hx_}" cy="{hy_}" r="6" fill="#59636D"/><circle cx="{hx_}" cy="{hy_}" r="3.4" fill="#1C2227"/>')

    # rails
    o.append(din_rail(PX0 + 22, PX1 - 22, 330))
    o.append(din_rail(PX0 + 22, PX1 - 100, 556))

    # positions (plate coordinates)
    QX, QY = 342, 240
    FX, FY = 236, 466
    KXp, KYp = 334, 476
    OX, OY = KXp + 22, KYp + 160

    q1_svg, q1 = mcb(QX, QY, 4, ["1", "3", "5", "N"], ["2", "4", "6", "N"], "D16")
    f1_svg, f1 = mcb(FX, FY, 1, ["1"], ["2"], "C2")
    k1_svg, k1 = contactor(KXp, KYp)
    f2_svg, f2 = overload(OX, OY)

    # component shadows
    for (x0, y0, w, h) in ((QX, QY, 144, 180), (FX, FY, 36, 180), (KXp, KYp, 100, 160), (OX, OY, 78, 120)):
        o.append(f'<rect x="{x0 + 6}" y="{y0 + 10}" width="{w}" height="{h}" rx="4" fill="#0B1218" opacity=".45" filter="url(#blur6)"/>')

    PW = 5.0   # power wire width
    CW = 3.4   # control wire width

    # ---- wires that leave from BOTTOM faces (drawn before their component)
    tx = {"2/T1": WIRE["L1"], "4/T2": WIRE["L2"], "6/T3": WIRE["L3"]}
    sheath_x = 430
    for i, lab in enumerate(("2/T1", "4/T2", "6/T3")):
        x, y = f2[lab]
        o.append(wire([(x, y), (x, 782), (sheath_x - 14 + i * 7, 800), (sheath_x - 14 + i * 7, 820)], tx[lab], PW, r=10,
                      ferrule=[(x, y + 16, True)]))
    # Q1 bottom: power to K1 + L1 branch to the control MCB F1
    q = [q1[f"{i}b"] for i in range(4)]
    f1t = f1["0t"]
    o.append(wire([q[0], (q[0][0], 440), (f1t[0], 440), f1t], WIRE["L1"], CW,
                  ferrule=[(f1t[0], f1t[1] - 12, True)]))
    for ph, kt, yj in (("L1", "1/L1", 452), ("L2", "3/L2", 452), ("L3", "5/L3", 452)):
        src = q[{"L1": 0, "L2": 1, "L3": 2}[ph]]
        o.append(wire([src, (src[0], yj), (k1[kt][0], yj), k1[kt]], WIRE[ph], PW,
                      ferrule=[(k1[kt][0], k1[kt][1] - 13, True)]))

    # ---- components (bottom to top so wires between them layer correctly)
    nfx, nfy = f2["96"]
    o.append(f2_svg)
    o.append(k1_svg)
    o.append(f1_svg)
    # neutral Q1.N -> F2.96 (passes in front of F2's side, behind Q1)
    o.append(wire([q[3], (q[3][0], nfy - 22), (nfx + 12, nfy - 22), (nfx + 12, nfy), (nfx, nfy)], WIRE["N"], CW + .4, r=6))
    o.append(screw(nfx, nfy, 4.6))
    o.append(tag(q[3][0], 566, "N"))
    o.append(q1_svg)

    # ---- wires landing on FRONT terminals / TOP faces (drawn after)
    # A2 -> 95 (wire 4)
    a2 = k1["A2"]
    p95 = f2["95"]
    o.append(wire([a2, (a2[0], a2[1] + 18), (p95[0], a2[1] + 18), p95], WIRE["CTL"], CW, r=5))
    o.append(screw(*a2, 4.6) + screw(*p95, 4.6))
    o.append(tag(a2[0], k1["6/T3f"][1], "4"))
    # 14 -> A1 jumper (node 3)
    a1 = k1["A1"]
    k14 = k1["14f"]
    o.append(wire([k14, (k14[0] - 9, k14[1]), (k14[0] - 9, a1[1]), a1], WIRE["CTL"], CW, r=4))
    o.append(screw(*k14, 5) + screw(*a1, 4.6))

    # supply cable from the top
    sx0 = 420
    o.append(f'<path d="M{sx0},150 V214" stroke="#000" stroke-opacity=".4" stroke-width="22" transform="translate(3,4)" filter="url(#blur2)"/>')
    o.append(f'<path d="M{sx0},150 V214" stroke="{lin_grad(sx0 - 11, 0, sx0 + 11, 0, [(0, "#14181C"), (.35, "#5A636C"), (.6, "#30363C"), (1, "#0E1114")])}" stroke-width="22" stroke-linecap="round"/>')
    qt = [q1[f"{i}t"] for i in range(4)]
    cores = [("L1", qt[0]), ("L2", qt[1]), ("L3", qt[2]), ("N", qt[3])]
    for i, (nm, (x, y)) in enumerate(cores):
        o.append(wire([(sx0 - 9 + i * 6, 212), (sx0 - 9 + i * 6, 220), (x, 226), (x, y)], WIRE[nm], PW, r=6,
                      ferrule=[(x, y - 9, True)]))
    # PE down the right side to the earth bar
    ebx, eby = 512, 744
    o.append(wire([(sx0 + 9, 212), (sx0 + 9, 220), (553, 226), (553, eby - 16), (ebx + 22, eby - 16), (ebx + 22, eby - 4)],
                  WIRE["PE"], PW, r=8, pe=True))
    # earth bar
    o.append(obox(ebx - 24, eby - 8, ebx + 34, eby + 8, 0, 12, "#C9A24B", top="#E7C977", side="#8E6E26"))
    for dx in (-14, 2, 22):
        o.append(screw(ebx + dx - 4, eby + 3, 4.2))
    o.append(T(ebx - 26, eby + 26, "PE", 9, "m", 700, "#2B6B3C"))
    o.append(f'<g transform="translate({ebx + 2},{eby + 18})"><path d="M0,0v6M-7,6h14M-4.6,9h9.2M-2.2,12h4.4" stroke="#2B6B3C" stroke-width="1.6" fill="none"/></g>')
    # PE to the motor cable
    o.append(wire([(ebx - 18, eby + 3), (sheath_x + 8, eby + 3), (sheath_x + 8, 820)], WIRE["PE"], PW, r=8, pe=True))

    # cable gland/sheath at the bottom of the plate
    o.append(f'<rect x="{sheath_x - 22}" y="806" width="44" height="18" rx="4" fill="{lin_grad(0, 0, 0, 1, [(0, "#3D454D"), (1, "#14181C")], units="obj")}"/>')

    # designation tags (engraved labels on the plate)
    def plate_tag(x, y, big, small):
        w = max(text_w(big, 15, "PlexC", 700), text_w(small, 8.4, "PlexM", 600)) + 14
        return (f'<rect x="{f(x)}" y="{f(y)}" width="{f(w)}" height="34" rx="4" fill="#111B25"/>'
                f'<rect x="{f(x)}" y="{f(y)}" width="4" height="34" rx="2" fill="{AMBER}"/>'
                + T(x + 9, y + 15, big, 15, "c", 700, "#FFFFFF") + T(x + 9, y + 27, small, 8.4, "m", 600, MUTED))
    o.append(plate_tag(PX0 + 14, 266, "Q1", "MCB 4P 16A"))
    o.append(plate_tag(PX0 + 12, 386, "F1", "MCB 1P 2A"))
    o.append(plate_tag(478, 468, "K1", "contactor"))
    o.append(plate_tag(478, 640, "F2", "overload"))

    # brand label strip
    o.append(f'<rect x="{PX0 + 32}" y="{PY1 - 25}" width="118" height="18" rx="3" fill="#111B25"/>')
    o.append(T(PX0 + 91, PY1 - 12.6, "RAPTI · DOL STARTER", 8.4, "m", 700, AMBER, "middle", .6))
    # supply legend next to the incoming cable
    o.append(T(sx0 - 20, 174, "3~ SUPPLY  400 V / 230 V · 50 Hz", 10, "m", 600, SOFT, "end"))
    lx = sx0 - 20 - 196
    for nm in ("L1", "L2", "L3", "N", "PE"):
        col = {"N": "#B9C4CE"}.get(nm, WIRE[nm])
        o.append(f'<rect x="{f(lx)}" y="179" width="10" height="10" rx="2" fill="{WIRE[nm] if nm != "PE" else "url(#pe)"}" stroke="#56616B" stroke-width=".6"/>')
        o.append(T(lx + 14, 188, nm, 10, "m", 700, col))
        lx += 26 + 7 * len(nm)

    f1b = f1["0b"]
    term = {"k13": k1["13"], "k14f": k1["14f"], "a1": a1, "f1b": (f1b[0], FY + 180 + 14)}
    return "".join(o), term, sheath_x


# ================================================================== CARDS ===
def wrap(text, width, size, fam="Plex", wt=400):
    lines, cur = [], ""
    for word in text.split():
        trial = (cur + " " + word).strip()
        if text_w(trial, size, fam, wt) <= width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    lines.append(cur)
    return lines


def card(x, y, w, h):
    return (f'<rect x="{x + 3}" y="{y + 8}" width="{w}" height="{h}" rx="16" fill="#000" opacity=".35" filter="url(#blur6)"/>'
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="16" fill="{lin_grad(x, y, x, y + h, [(0, "#18304A", .94), (1, "#122438", .94)])}" stroke="{NAVY_LINE}" stroke-width="1.2"/>'
            f'<rect x="{x + 18}" y="{y}" width="56" height="3" rx="1.5" fill="{AMBER}"/>')


def how_it_works(x, y, w, h):
    o = [card(x, y, w, h)]
    o.append(T(x + 20, y + 34, "HOW IT WORKS", 19, "c", 700, "#FFFFFF", ls=1))
    o.append(T(x + 20 + text_w("HOW IT WORKS", 19, "PlexC", 700, 1) + 10, y + 34, "কীভাবে কাজ করে", 16, "b", 600, AMBER))
    steps = [
        ("1", "START", "S1 · NO 13-14", LAMP,
         "Coil A1–A2 gets 230 V. K1 pulls in and the motor starts on full line voltage."),
        ("2", "HOLD", "K1 · NO 13-14", "#4FC3D0",
         "K1's own auxiliary contact bridges START, so the coil stays on after release."),
        ("3", "STOP", "S0 · NC 11-12", "#FF5A5F",
         "Breaks the coil circuit. K1 drops out and the motor coasts to a stop."),
        ("4", "TRIP", "F2 · NC 95-96", AMBER,
         "On overload F2 opens 95-96 and stops the motor. Press blue RESET once cool."),
    ]
    cw, ch = (w - 52) / 2, 100
    for i, (n, title, sub, col, body) in enumerate(steps):
        cx = x + 18 + (i % 2) * (cw + 16)
        cy = y + 54 + (i // 2) * (ch + 10)
        o.append(f'<rect x="{f(cx)}" y="{f(cy)}" width="{f(cw)}" height="{ch}" rx="11" fill="#0E1C2B" stroke="#24425F" stroke-width="1"/>')
        o.append(f'<circle cx="{f(cx + 20)}" cy="{f(cy + 22)}" r="13" fill="{col}"/>')
        o.append(T(cx + 20, cy + 27.5, n, 14, "c", 700, NAVY, "middle"))
        o.append(T(cx + 41, cy + 22, title, 15.5, "c", 700, "#FFFFFF", ls=.6))
        o.append(T(cx + 41, cy + 36, sub, 10.4, "m", 600, col))
        for j, line in enumerate(wrap(body, cw - 24, 12.2)):
            o.append(T(cx + 12, cy + 56 + j * 15.5, line, 12.2, "s", 400, SOFT))
    o.append(T(x + 20, y + h - 13, "⚡ Starting current ≈ 6–8 × FLC, so DOL suits small motors (typically ≤ 7.5 kW).",
               10.6, "s", 500, MUTED))
    return "".join(o)


def sch(d, col="#DCE6EF", w=1.6, extra=""):
    return f'<path d="{d}" fill="none" stroke="{col}" stroke-width="{w}" stroke-linecap="round" stroke-linejoin="round"{extra}/>'


def schematic(x0, y0, w, h):
    o = [card(x0, y0, w, h)]
    o.append(T(x0 + 20, y0 + 32, "CIRCUIT DIAGRAM", 18, "c", 700, "#FFFFFF", ls=1))
    o.append(T(x0 + 20 + text_w("CIRCUIT DIAGRAM", 18, "PlexC", 700, 1) + 10, y0 + 32, "পাওয়ার ও কন্ট্রোল সার্কিট", 15, "b", 600, AMBER))
    LC = "#DCE6EF"
    # ---------------- power circuit
    xs = {"PE": x0 + 22, "L1": x0 + 46, "L2": x0 + 70, "L3": x0 + 94, "N": x0 + 118}
    top = y0 + 58
    for nm, x in xs.items():
        col = {"N": "#B9C4CE", "PE": "#55D07F"}.get(nm, WIRE[nm])
        o.append(T(x, top - 5, nm, 10, "m", 700, col, "middle"))
    yq0, yq1 = y0 + 68, y0 + 84
    ph = ("L1", "L2", "L3")
    for nm in ph + ("N",):
        x = xs[nm]
        o.append(sch(f"M{f(x)},{f(top)}V{f(yq0)}", LC))
        o.append(sch(f"M{f(x - 3)},{f(yq0 - 3)}l6,6M{f(x + 3)},{f(yq0 - 3)}l-6,6", LC, 1.3))
        o.append(sch(f"M{f(x)},{f(yq1)}L{f(x - 8)},{f(yq0 + 1)}", LC))
    o.append(sch(f"M{f(xs['L1'] - 4)},{f(y0 + 76)}H{f(xs['N'] - 4)}", LC, 1, ' stroke-dasharray="3 3"'))
    o.append(T(xs["N"] + 12, y0 + 80, "Q1", 12, "c", 700, AMBER))
    # N tap -> right rail ; L1 tap -> left rail (hops over L2/L3)
    ytn, ytl = y0 + 96, y0 + 104
    xl, xr = x0 + 250, x0 + w - 22
    o.append(sch(f"M{f(xs['N'])},{f(yq1)}V{f(ytn)}H{f(xr)}V{f(y0 + 176)}", "#B9C4CE"))
    hop = "".join(f"H{f(xs[n] - 4.5)}a4.5,4.5 0 0 1 9,0" for n in ("L2", "L3"))
    o.append(sch(f"M{f(xs['L1'])},{f(ytl)}{hop}H{f(xl)}V{f(y0 + 176)}", "#FF8A8E"))
    o.append(f'<circle cx="{f(xs["L1"])}" cy="{f(ytl)}" r="3" fill="#FF8A8E"/>')
    # K1 main contacts
    yk0, yk1 = y0 + 118, y0 + 134
    for nm in ph:
        x = xs[nm]
        o.append(sch(f"M{f(x)},{f(yq1)}V{f(yk0)}M{f(x)},{f(yk1)}L{f(x - 8)},{f(yk0 + 1)}", LC))
    o.append(sch(f"M{f(xs['L1'] - 4)},{f(y0 + 126)}H{f(xs['L3'] + 4)}", LC, 1, ' stroke-dasharray="3 3"'))
    o.append(T(xs["L3"] + 14, y0 + 130, "K1", 12, "c", 700, AMBER))
    # F2 thermal elements
    yf0, yf1 = y0 + 146, y0 + 164
    for nm in ph:
        x = xs[nm]
        o.append(sch(f"M{f(x)},{f(yk1)}V{f(yf0)}M{f(x)},{f(yf1)}V{f(y0 + 180)}", LC))
        o.append(f'<rect x="{f(x - 6)}" y="{f(yf0)}" width="12" height="{f(yf1 - yf0)}" fill="#122438" stroke="{LC}" stroke-width="1.4"/>')
        o.append(sch(f"M{f(x)},{f(yf0 + 3)}v4h-3v5h3v3", LC, 1.2))
    o.append(T(xs["L3"] + 14, y0 + 159, "F2", 12, "c", 700, AMBER))
    # motor
    mx, my, mr = xs["L2"], y0 + 208, 20
    o.append(sch(f"M{f(xs['L1'])},{f(y0 + 180)}L{f(mx - 12)},{f(my - 16)}M{f(xs['L2'])},{f(y0 + 180)}V{f(my - mr)}"
                 f"M{f(xs['L3'])},{f(y0 + 180)}L{f(mx + 12)},{f(my - 16)}", LC))
    o.append(f'<circle cx="{f(mx)}" cy="{f(my)}" r="{mr}" fill="#0E1C2B" stroke="{LC}" stroke-width="1.6"/>')
    o.append(T(mx, my + 1, "M", 15, "c", 700, "#FFFFFF", "middle"))
    o.append(T(mx, my + 12, "3~", 9, "m", 700, "#9FD8E0", "middle"))
    o.append(sch(f"M{f(xs['PE'])},{f(top)}V{f(my)}H{f(mx - mr)}", "#55D07F", 1.4, ' stroke-dasharray="5 2"'))
    o.append(T(mx + 26, my + 4, "M1", 12, "c", 700, AMBER))
    o.append(T(mx + 26, my + 16, "3.7 kW", 9, "m", 600, MUTED))

    # ---------------- control circuit (ladder)
    yr, yh = y0 + 128, y0 + 162
    o.append(T(xl, ytl - 9, "L1", 10, "m", 700, "#FF8A8E", "middle"))
    o.append(T(xr, ytn - 6, "N", 10, "m", 700, "#B9C4CE", "middle"))

    def no_h(xa, y, push=False, nc=False, breaker=False, thermal=False):
        xb = xa + 22
        s = sch(f"M{f(xa)},{f(y)}H{f(xa)}", LC)
        if nc:
            s += sch(f"M{f(xb)},{f(y)}v-9M{f(xa)},{f(y)}L{f(xb + 3)},{f(y - 7)}", LC)
        else:
            s += sch(f"M{f(xa)},{f(y)}L{f(xb - 2)},{f(y - 9)}", LC)
        if breaker:
            s += sch(f"M{f(xb - 3)},{f(y - 3)}l6,6M{f(xb + 3)},{f(y - 3)}l-6,6", LC, 1.3)
        if push:
            mx_ = xa + 11
            s += sch(f"M{f(mx_)},{f(y - 5)}V{f(y - 18)}", LC, 1, ' stroke-dasharray="2.5 2"')
            s += sch(f"M{f(mx_ - 5)},{f(y - 15)}v-4h10v4", LC, 1.4)
        if thermal:
            mx_ = xa + 12
            s += sch(f"M{f(mx_)},{f(y - 4)}V{f(y - 14)}", LC, 1, ' stroke-dasharray="2.5 2"')
            s += sch(f"M{f(mx_ - 6)},{f(y - 14)}h4v-4h4v4h4", LC, 1.4)
        return s

    els = [  # (x, label, terms, kind)
        (xl + 16, "F1", "1  2", dict(breaker=True)),
        (xl + 72, "S0", "11 12", dict(push=True, nc=True)),
        (xl + 130, "S1", "13 14", dict(push=True)),
        (xl + 242, "F2", "95 96", dict(thermal=True, nc=True)),
    ]
    coil_x = xl + 204
    segs = [xl, els[0][0], els[0][0] + 22, els[1][0], els[1][0] + 22, els[2][0], els[2][0] + 22, coil_x - 13,
            coil_x + 13, els[3][0], els[3][0] + 22, xr]
    for a, b in zip(segs[::2], segs[1::2]):
        o.append(sch(f"M{f(a)},{f(yr)}H{f(b)}", LC))
    for xa, lab, terms, kw in els:
        o.append(no_h(xa, yr, **kw))
        o.append(T(xa + 11, yr - 24, lab, 11.5, "c", 700, AMBER, "middle"))
        o.append(T(xa + 11, yr + 13, terms, 8.6, "m", 600, MUTED, "middle"))
    o.append(f'<rect x="{f(coil_x - 13)}" y="{f(yr - 8)}" width="26" height="16" fill="#122438" stroke="{LC}" stroke-width="1.6"/>')
    o.append(T(coil_x, yr - 14, "K1", 11.5, "c", 700, AMBER, "middle"))
    o.append(T(coil_x - 14, yr + 18, "A1", 8.6, "m", 600, MUTED, "middle"))
    o.append(T(coil_x + 14, yr + 18, "A2", 8.6, "m", 600, MUTED, "middle"))
    # hold-in branch
    n1, n2 = els[1][0] + 34, els[2][0] + 34
    o.append(sch(f"M{f(n1)},{f(yr)}V{f(yh)}H{f(els[2][0])}M{f(els[2][0] + 22)},{f(yh)}H{f(n2)}V{f(yr)}", LC))
    o.append(no_h(els[2][0], yh))
    o.append(T(els[2][0] + 11, yh + 14, "K1 13-14", 8.6, "m", 600, "#9FD8E0", "middle"))
    for nx_ in (n1, n2):
        o.append(f'<circle cx="{f(nx_)}" cy="{f(yr)}" r="2.8" fill="{LC}"/>')
    # wire numbers
    for xx, lab in ((els[0][0] + 39, "1"), (els[1][0] + 46, "2"), (els[2][0] + 52, "3"), (coil_x + 25, "4")):
        o.append(f'<rect x="{f(xx - 6)}" y="{f(yr - 16)}" width="12" height="11" rx="2" fill="#E9EEF2"/>')
        o.append(T(xx, yr - 7.5, lab, 8.6, "m", 700, NAVY, "middle"))

    # ---------------- motor terminal box (delta links) + notes
    o.append(sch(f"M{f(xl - 4)},{f(y0 + 186)}H{f(xr)}", "#24425F", 1))
    bx, by = xl - 4, y0 + 202
    o.append(T(bx, by, "TERMINAL BOX · Δ", 9.4, "m", 700, AMBER))
    tops, bots = ("U1", "V1", "W1"), ("W2", "U2", "V2")
    for i in range(3):
        cx = bx + 8 + i * 40
        o.append(f'<rect x="{f(cx - 4.5)}" y="{f(by + 10)}" width="9" height="24" rx="3" fill="{lin_grad(0, 0, 1, 0, [(0, "#9C7A2E"), (.5, "#F1D27E"), (1, "#9C7A2E")], units="obj")}"/>')
        for yy, lab in ((by + 12, tops[i]), (by + 32, bots[i])):
            o.append(f'<circle cx="{f(cx)}" cy="{f(yy)}" r="4.6" fill="#DCE6EF" stroke="#0E1C2B" stroke-width="1"/>')
            o.append(T(cx + 8, yy + 3.4, lab, 9, "m", 700, SOFT))
    nx0 = xl + 140
    notes = ["Coil 230 V AC across L1–N", "Wire nos. match the 3D view", "Earth (PE) the motor frame"]
    for i, t in enumerate(notes):
        yy = y0 + 202 + i * 15
        o.append(f'<circle cx="{f(nx0)}" cy="{f(yy - 3.4)}" r="2.2" fill="{AMBER}"/>')
        o.append(T(nx0 + 8, yy, t, 9.8, "s", 500, SOFT))
    return "".join(o)


def selection(x, y, w, h):
    o = [card(x, y, w, h)]
    o.append(T(x + 20, y + 32, "TYPICAL SELECTION", 18, "c", 700, "#FFFFFF", ls=1))
    o.append(T(x + 20 + text_w("TYPICAL SELECTION", 18, "PlexC", 700, 1) + 10, y + 32, "যন্ত্রাংশ নির্বাচন", 15, "b", 600, AMBER))
    o.append(T(x + 20, y + 50, "for a 3.7 kW (5 HP) motor · 400 V · 7.5 A full-load current", 10.4, "m", 500, MUTED))
    rows = [("Q1", "Main MCB, 4-pole", "16 A · D-curve"),
            ("F1", "Control MCB, 1-pole", "2 A · C-curve"),
            ("K1", "Magnetic contactor", "12 A AC-3 · 230 V~"),
            ("F2", "Thermal overload relay", "7–10 A, set 7.5 A"),
            ("S0", "STOP push button (red)", "NC 11-12"),
            ("S1", "START push button (green)", "NO 13-14"),
            ("M1", "3-phase induction motor", "3.7 kW · Δ 400 V"),
            ("W1", "Motor cable, copper", "4C × 2.5 mm²")]
    for i, (tg, dev, rt) in enumerate(rows):
        ry = y + 66 + i * 21.6
        if i % 2 == 0:
            o.append(f'<rect x="{x + 12}" y="{f(ry - 1)}" width="{w - 24}" height="21.6" rx="5" fill="#0E1C2B" opacity=".75"/>')
        o.append(f'<rect x="{x + 18}" y="{f(ry + 3)}" width="30" height="15" rx="3.5" fill="#0B1520" stroke="{AMBER}" stroke-width=".8"/>')
        o.append(T(x + 33, ry + 14, tg, 9.6, "m", 700, AMBER, "middle"))
        o.append(T(x + 58, ry + 14.5, dev, 12, "s", 500, SOFT))
        o.append(T(x + w - 20, ry + 14.5, rt, 11, "m", 600, "#FFFFFF", "end"))
    return "".join(o)


ICON = {
    "phone": '<path d="M6.6 10.8a15.1 15.1 0 0 0 6.6 6.6l2.2-2.2a1 1 0 0 1 1-.25 11.4 11.4 0 0 0 3.6.6 1 1 0 0 1 1 1V20a1 1 0 0 1-1 1A17 17 0 0 1 3 4a1 1 0 0 1 1-1h3.5a1 1 0 0 1 1 1c0 1.25.2 2.45.6 3.6a1 1 0 0 1-.25 1z"/>',
    "wa": '<path d="M17.47 14.38c-.3-.15-1.77-.87-2.04-.97-.27-.1-.47-.15-.67.15-.2.3-.77.97-.94 1.17-.17.2-.35.22-.65.07-.3-.15-1.26-.46-2.4-1.48-.89-.79-1.49-1.77-1.66-2.07-.17-.3-.02-.46.13-.61.13-.13.3-.35.45-.52.15-.17.2-.3.3-.5.1-.2.05-.37-.02-.52-.07-.15-.67-1.62-.92-2.22-.24-.58-.49-.5-.67-.51h-.57c-.2 0-.52.07-.8.37-.27.3-1.04 1.02-1.04 2.5 0 1.47 1.07 2.89 1.22 3.09.15.2 2.1 3.2 5.08 4.49.71.3 1.26.49 1.7.63.71.22 1.36.19 1.87.12.57-.09 1.77-.72 2.02-1.42.25-.7.25-1.3.17-1.42-.07-.13-.27-.2-.57-.35zM12.05 21.79h-.01a9.87 9.87 0 0 1-5.03-1.38l-.36-.21-3.74.98 1-3.65-.24-.37a9.86 9.86 0 0 1-1.51-5.26c0-5.45 4.44-9.88 9.9-9.88a9.85 9.85 0 0 1 9.88 9.9c0 5.45-4.43 9.87-9.89 9.87zm8.41-18.28A11.82 11.82 0 0 0 12.04 0C5.46 0 .1 5.35.1 11.93c0 2.1.55 4.16 1.6 5.97L0 24l6.24-1.64a11.93 11.93 0 0 0 5.8 1.48h.01c6.58 0 11.94-5.36 11.94-11.94 0-3.19-1.24-6.19-3.53-8.39z"/>',
    "web": '<path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm6.9 6h-2.95a15.6 15.6 0 0 0-1.38-3.56A8.03 8.03 0 0 1 18.9 8zM12 4.04c.83 1.2 1.48 2.53 1.91 3.96h-3.82c.43-1.43 1.08-2.76 1.91-3.96zM4.26 14a7.9 7.9 0 0 1 0-4h3.38a16.5 16.5 0 0 0 0 4H4.26zm.82 2h2.95c.32 1.25.78 2.45 1.38 3.56A7.99 7.99 0 0 1 5.08 16zm2.95-8H5.08a7.99 7.99 0 0 1 4.33-3.56A15.6 15.6 0 0 0 8.03 8zM12 19.96A14.1 14.1 0 0 1 10.09 16h3.82A14.1 14.1 0 0 1 12 19.96zM14.34 14H9.66a14.7 14.7 0 0 1 0-4h4.68a14.7 14.7 0 0 1 0 4zm.25 5.56c.6-1.11 1.06-2.31 1.38-3.56h2.95a8.03 8.03 0 0 1-4.33 3.56zM16.36 14a16.5 16.5 0 0 0 0-4h3.38a7.9 7.9 0 0 1 0 4h-3.38z"/>',
    "mail": '<path d="M20 4H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2zm0 4-8 5-8-5V6l8 5 8-5v2z"/>',
    "fb": '<path d="M24 12.07C24 5.4 18.63 0 12 0S0 5.4 0 12.07C0 18.1 4.39 23.09 10.13 24v-8.44H7.08v-3.49h3.05V9.41c0-3.02 1.79-4.7 4.53-4.7 1.31 0 2.68.24 2.68.24v2.97h-1.51c-1.49 0-1.96.93-1.96 1.89v2.26h3.33l-.53 3.49h-2.8V24C19.61 23.09 24 18.1 24 12.07z"/>',
    "yt": '<path d="M23.5 6.19a3.02 3.02 0 0 0-2.12-2.14C19.5 3.55 12 3.55 12 3.55s-7.5 0-9.38.5A3.02 3.02 0 0 0 .5 6.19C0 8.07 0 12 0 12s0 3.93.5 5.81a3.02 3.02 0 0 0 2.12 2.14c1.88.5 9.38.5 9.38.5s7.5 0 9.38-.5a3.02 3.02 0 0 0 2.12-2.14C24 15.93 24 12 24 12s0-3.93-.5-5.81zM9.55 15.57V8.43L15.82 12l-6.27 3.57z"/>',
    "pin": '<path d="M12 2a7 7 0 0 0-7 7c0 5.25 7 13 7 13s7-7.75 7-13a7 7 0 0 0-7-7zm0 9.5A2.5 2.5 0 1 1 12 6.5a2.5 2.5 0 0 1 0 5z"/>',
}


def icon(name, x, y, size=16, fill=AMBER):
    k = size / 24
    return f'<g transform="translate({f(x)},{f(y)}) scale({k:.4f})" fill="{fill}">{ICON[name]}</g>'


# =================================================================== MAIN ===
def build():
    ensure_fonts()
    body = []

    # ---------------------------------------------------------------- page / stage
    body.append(f'<rect width="{W}" height="{H}" fill="{DEEP}"/>')
    body.append(f'<rect y="160" width="{W}" height="944" fill="{lin_grad(0, 160, 0, 1104, [(0, "#10233A"), (.55, "#0C1A2A"), (1, "#081320")])}"/>')
    body.append(f'<rect y="160" width="{W}" height="944" fill="url(#grid)" opacity=".55"/>')
    body.append(f'<ellipse cx="830" cy="650" rx="360" ry="230" fill="{rad_grad(.5, .5, .5, [(0, TEAL, .34), (1, TEAL, 0)])}"/>')
    body.append(f'<ellipse cx="370" cy="480" rx="320" ry="330" fill="{rad_grad(.5, .5, .5, [(0, "#3E6A93", .3), (1, "#3E6A93", 0)])}"/>')

    # ---------------------------------------------------------------- 3D scene
    panel_svg, pt, sheath_x = panel()
    m_pre, m_gland, m_box, _, route = motor(858, 638, 0.93)
    stop_svg, st, _ = push_button(78, 498, 1.85, "stop")
    start_svg, sr, _ = push_button(78, 642, 1.85, "start")

    body.append(panel_svg)
    body.append(m_pre)

    # motor cable: terminal-box gland -> over the fan cowl -> floor -> plate bottom (Catmull-Rom)
    c0 = (sheath_x, 826)
    P = list(route) + [(566, 826), (500, 838), (446, 836), c0]
    d = f"M{f(P[0][0])},{f(P[0][1])}"
    for i in range(len(P) - 1):
        p0, p1, p2 = P[max(0, i - 1)], P[i], P[i + 1]
        p3 = P[min(len(P) - 1, i + 2)]
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        d += f" C{f(c1[0])},{f(c1[1])} {f(c2[0])},{f(c2[1])} {f(p2[0])},{f(p2[1])}"
    body.append(f'<path d="{d}" fill="none" stroke="#000" stroke-opacity=".5" stroke-width="18" transform="translate(5,9)" filter="url(#blur6)"/>')
    body.append(m_gland)
    body.append(f'<path d="{d}" fill="none" stroke="#101317" stroke-width="17" stroke-linecap="round"/>')
    body.append(f'<path d="{d}" fill="none" stroke="#39414A" stroke-width="12.5" stroke-linecap="round"/>')
    body.append(f'<path d="{d}" fill="none" stroke="#9AA6B1" stroke-width="2.4" stroke-linecap="round" transform="translate(-1.6,-3.4)" opacity=".6"/>')
    body.append(m_box)
    body.append(T(sheath_x - 30, 820, "W1 · 4C × 2.5 mm² Cu  →  M1", 10, "m", 600, MUTED, "end"))

    # push buttons + control wires (no crossings: outer terminal -> outer run)
    body.append(stop_svg)
    body.append(start_svg)
    CW = 3.4
    s11, s12, s13, s14 = st["11"], st["12"], sr["13"], sr["14"]
    f1b, k13, k14 = pt["f1b"], pt["k13"], pt["k14f"]
    xa, xb, xc, xj = s11[0] + 38, s13[0] + 25, s14[0] + 15, s12[0] + 10
    y1, y2, y3 = 676, 698, 720
    body.append(wire([f1b, (f1b[0], y1), (xa, y1), (xa, s11[1]), s11], WIRE["CTL"], CW, r=7,
                     ferrule=[(f1b[0], f1b[1] + 8, True)]))
    body.append(wire([s12, (xj, s12[1]), (xj, s13[1]), s13], WIRE["CTL"], CW, r=6))
    body.append(wire([s13, (xb, s13[1]), (xb, y2), (284, y2), (284, 456), (k13[0], 456), k13], WIRE["CTL"], CW, r=7,
                     ferrule=[(k13[0], k13[1] - 12, True)]))
    body.append(wire([s14, (xc, s14[1]), (xc, y3), (298, y3), (298, k14[1]), k14], WIRE["CTL"], CW, r=7))
    body.append(screw(*k14, 5))
    for (x, y, lab) in ((xa, s11[1] + 40, "1"), (f1b[0], y1 - 14, "1"), (xb, s13[1] + 34, "2"), (284, 560, "2"),
                        (xc, s14[1] + 32, "3"), (298, 690, "3"), (xj, (s12[1] + s13[1]) / 2, "2")):
        body.append(tag(x, y, lab))
    for (x, y, big, small, col) in ((14, 528, "S0  STOP", "NC 11-12", "#FF6B6F"), (14, 676, "S1  START", "NO 13-14", LAMP)):
        body.append(f'<rect x="{x}" y="{y}" width="92" height="34" rx="5" fill="#0B1520" stroke="{col}" stroke-width="1"/>')
        body.append(T(x + 8, y + 15, big, 13, "c", 700, col))
        body.append(T(x + 8, y + 28, small, 9.6, "m", 600, SOFT))

    # wire colour legend under the motor
    lx, ly = 612, 822
    body.append(T(lx, ly, "WIRE COLOURS", 10, "m", 700, AMBER, ls=.8))
    lx += 92
    for nm, lab in (("L1", "L1"), ("L2", "L2"), ("L3", "L3"), ("N", "N"), ("PE", "PE"), ("CTL", "Control 1 mm²")):
        body.append(f'<rect x="{lx}" y="{ly - 9}" width="18" height="9" rx="3" fill="{WIRE[nm] if nm != "PE" else "url(#pe)"}" stroke="#56616B" stroke-width=".6"/>')
        body.append(T(lx + 23, ly, lab, 10, "m", 600, SOFT))
        lx += 23 + text_w(lab, 10, "PlexM", 600) + 14

    # ---------------------------------------------------------------- cards
    body.append(how_it_works(586, 174, 474, 284))
    body.append(schematic(20, 848, 572, 246))
    body.append(selection(604, 848, 456, 246))

    # ---------------------------------------------------------------- top strip
    body.append(f'<rect width="{W}" height="36" fill="{NAVY}"/><rect y="35" width="{W}" height="1" fill="{NAVY_LINE}"/>')
    body.append(f'<circle cx="28" cy="18" r="8" fill="{LAMP}" opacity=".25"/><circle cx="28" cy="18" r="4.5" fill="{LAMP}"/>')
    body.append(T(42, 22.5, "ADMISSION OPEN", 12, "m", 600, "#FFFFFF", ls=1.4))
    body.append(T(200, 22.5, "HOTLINE  01617-345534", 12, "m", 500, SOFT, ls=.8))
    body.append(T(400, 22.5, "raptibd@gmail.com", 12, "m", 500, MUTED, ls=.4))
    body.append(T(W - 24, 22.5, "RAPTI.NET  ↗", 12, "m", 700, AMBER, "end", 1.4))

    # ---------------------------------------------------------------- header
    body.append(f'<rect y="36" width="{W}" height="124" fill="#FFFFFF"/>')
    body.append(f'<rect y="158" width="{W}" height="2" fill="{AMBER}"/>')
    body.append('<image href="data:image/png;base64,__LOGO__" x="22" y="52" width="252" height="92"/>')
    body.append('<rect x="292" y="60" width="1.5" height="84" fill="#DCE2E8"/>')
    body.append(T(312, 76, "INDUSTRIAL ELECTRICAL CONTROL · MOTOR STARTER SERIES", 11.5, "m", 600, TEAL, ls=1.6))
    body.append(T(310, 121, "DOL Starter Wiring Diagram", 46, "c", 700, NAVY, ls=.2))
    body.append(T(312, 146, "Direct-On-Line starter for a 3-phase induction motor — power & control wiring, step by step",
                  14.5, "s", 500, INK_SOFT))
    tw = text_w("DOL Starter Wiring Diagram", 46, "PlexC", 700, .2)
    bx = 310 + tw + 18
    body.append(f'<rect x="{f(bx)}" y="92" width="96" height="30" rx="15" fill="{AMBER}"/>')
    body.append(T(bx + 48, 112, "3D GUIDE", 13, "c", 700, NAVY, "middle", 1.2))

    # ---------------------------------------------------------------- course band
    by = 1104
    body.append(f'<rect y="{by}" width="{W}" height="104" fill="{lin_grad(0, 0, 1, 0, [(0, "#FFB100"), (.6, "#FFC02E"), (1, "#FFD15C")], units="obj")}"/>')
    body.append(f'<rect y="{by}" width="{W}" height="104" fill="url(#hatch)" opacity=".12"/>')
    body.append(T(24, by + 40, "এই সার্কিট নিজ হাতে ওয়্যারিং করে শিখুন!", 27, "b", 700, NAVY))
    body.append(T(24, by + 66, "Industrial Electrical Control · 3-month hands-on course — contactor, TOR, timer,", 14.2, "s", 600, "#3B2A00"))
    body.append(T(24, by + 84, "star-delta, forward-reverse, ATS & more on real industrial equipment.", 14.2, "s", 600, "#3B2A00"))
    btx, bty, btw, bth = 836, by + 22, 220, 60
    body.append(f'<rect x="{btx + 2}" y="{bty + 5}" width="{btw}" height="{bth}" rx="30" fill="#7A4F00" opacity=".35"/>')
    body.append(f'<rect x="{btx}" y="{bty}" width="{btw}" height="{bth}" rx="30" fill="{NAVY}"/>')
    body.append(T(btx + btw / 2, bty + 27, "ENROLL NOW  →", 21, "c", 700, AMBER, "middle", 1.2))
    body.append(T(btx + btw / 2, bty + 46, "ভর্তি চলছে · Call 01617-345534", 12.5, "b", 600, "#FFFFFF", "middle"))

    # ---------------------------------------------------------------- footer
    fy = 1208
    body.append(f'<rect y="{fy}" width="{W}" height="{H - fy}" fill="{NAVY}"/>')
    body.append(f'<circle cx="62" cy="{fy + 50}" r="38" fill="#FFFFFF"/>')
    body.append(f'<image href="data:image/png;base64,__ICON__" x="28" y="{fy + 16}" width="68" height="68"/>')
    body.append(T(114, fy + 42, "RAPTI", 30, "c", 700, "#FFFFFF", ls=2))
    body.append(T(114, fy + 62, "Resonance Automation Professional", 12.5, "s", 500, SOFT))
    body.append(T(114, fy + 78, "Training Institute · Est. 2019", 12.5, "s", 500, SOFT))
    body.append(T(114, fy + 98, "Siemens · Mitsubishi · Delta · LS", 10.6, "m", 600, AMBER))
    body.append(f'<rect x="380" y="{fy + 18}" width="1" height="92" fill="{NAVY_LINE}"/>')
    items = [("phone", "01617-345534", "Admission hotline"), ("wa", "+880 1617-345534", "WhatsApp"),
             ("web", "rapti.net", "Courses & virtual lab"), ("mail", "raptibd@gmail.com", "Email"),
             ("fb", "/resonanceautomationbd", "Facebook"), ("yt", "/c/ResonanceAutomation", "YouTube")]
    for i, (ic, main, sub_) in enumerate(items):
        cxp = 404 + (i % 3) * 222
        cyp = fy + 24 + (i // 3) * 46
        body.append(f'<rect x="{cxp}" y="{cyp}" width="30" height="30" rx="8" fill="#1B3550"/>')
        body.append(icon(ic, cxp + 7, cyp + 7, 16, AMBER))
        body.append(T(cxp + 40, cyp + 14, main, 13, "s", 600, "#FFFFFF"))
        body.append(T(cxp + 40, cyp + 28, sub_, 10, "m", 500, MUTED))
    body.append(f'<rect y="{fy + 118}" width="{W}" height="{H - fy - 118}" fill="#0A1622"/>')
    body.append(icon("pin", 24, fy + 122, 14, AMBER))
    body.append(T(44, fy + 134, "2101, Bhuighar, Rupayan Town, Near Gate-1, Fatullah, Narayanganj-1420", 11, "m", 500, SOFT))
    body.append(T(W - 24, fy + 134, "© RAPTI · Learn by doing.", 11, "m", 600, MUTED, "end"))

    return "".join(body)


def assemble(body):
    logo = base64.b64encode((ASSETS / "rapti-logo.png").read_bytes()).decode()
    icon = base64.b64encode((ASSETS / "rapti-icon.png").read_bytes()).decode()
    body = body.replace("__LOGO__", logo).replace("__ICON__", icon)
    used = "".join(re.findall(r">([^<>]+)<", body))
    used = used.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    used += "".join(chr(c) for c in range(32, 127))
    css = font_css(used)
    defs = "".join(DEFS)
    filters = ('<filter id="blur2" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="1.6"/></filter>'
               '<filter id="blur6" x="-30%" y="-30%" width="160%" height="160%"><feGaussianBlur stdDeviation="5"/></filter>'
               '<filter id="blur14" x="-30%" y="-30%" width="160%" height="160%"><feGaussianBlur stdDeviation="12"/></filter>'
               '<filter id="blur18" x="-40%" y="-40%" width="180%" height="180%"><feGaussianBlur stdDeviation="16"/></filter>'
               '<pattern id="grid" width="24" height="24" patternUnits="userSpaceOnUse"><path d="M24 0H0V24" fill="none" stroke="#1E3A55" stroke-width=".7"/></pattern>'
               '<pattern id="hatch" width="10" height="10" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="4" height="10" fill="#FFFFFF"/></pattern>'
               '<pattern id="pe" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="8" height="8" fill="#2E9E4E"/><rect width="4" height="8" fill="#F4D21F"/></pattern>'
               '<pattern id="brushed" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(-8)"><path d="M0 1.5H6M0 4.5H6" stroke="#FFFFFF" stroke-width=".5" opacity=".5"/></pattern>')
    css += "".join(f".{k}{{font-family:{v}}}" for k, v in FAM.items())
    return (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
            f'viewBox="0 0 {W} {H}" width="{W}" height="{H}">'
            f'<title>DOL Starter Wiring Diagram — RAPTI</title>'
            f'<desc>3D illustrated Direct-On-Line starter wiring for a 3-phase induction motor, by Resonance Automation Professional Training Institute (rapti.net).</desc>'
            f'<defs><style>{css}</style>{filters}{defs}</defs>{body}</svg>')


def render_png():
    js = HERE / ".render.cjs"
    js.write_text(
        "const { chromium } = require('playwright');\n"
        "(async () => {\n"
        "  const [svg, png] = process.argv.slice(2);\n"
        "  const b = await chromium.launch();\n"
        f"  const p = await b.newPage({{ viewport: {{ width: {W}, height: {H} }}, deviceScaleFactor: 2 }});\n"
        "  await p.goto('file://' + svg);\n"
        "  await p.evaluate(() => document.fonts.ready);\n"
        "  await p.waitForTimeout(400);\n"
        "  await p.screenshot({ path: png });\n"
        "  await b.close();\n"
        "})();\n")
    env = dict(**__import__("os").environ)
    env["NODE_PATH"] = subprocess.run(["npm", "root", "-g"], capture_output=True, text=True).stdout.strip()
    subprocess.run(["node", str(js), str(OUT_SVG), str(OUT_PNG)], check=True, env=env)
    js.unlink()


if __name__ == "__main__":
    svg = assemble(build())
    OUT_SVG.write_text(svg, encoding="utf-8")
    print(f"SVG  {OUT_SVG}  ({len(svg) / 1024:.0f} KB)")
    render_png()
    print(f"PNG  {OUT_PNG}")
