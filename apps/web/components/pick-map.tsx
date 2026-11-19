"use client";

import { useMemo } from "react";

// A small click-to-pick map of Bangladesh on OpenStreetMap tiles (Web Mercator), no map library needed.
const Z = 7;
const TILE = 256;
const X0 = 94, X1 = 98, Y0 = 53, Y1 = 57; // tiles covering ~87.2°–92.8°E, ~20.6°–27°N at zoom 7

function lonToX(lon: number) { return ((lon + 180) / 360) * 2 ** Z * TILE; }
function latToY(lat: number) { const r = (lat * Math.PI) / 180; return ((1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2) * 2 ** Z * TILE; }
function xToLon(x: number) { return (x / (2 ** Z * TILE)) * 360 - 180; }
function yToLat(y: number) { const nn = Math.PI - (2 * Math.PI * y) / (2 ** Z * TILE); return (180 / Math.PI) * Math.atan(0.5 * (Math.exp(nn) - Math.exp(-nn))); }

export function PickMap({ lat, lon, onPick }: { lat: number | null; lon: number | null; onPick: (lat: number, lon: number) => void }) {
  const tiles = useMemo(() => {
    const out: { x: number; y: number }[] = [];
    for (let y = Y0; y <= Y1; y++) for (let x = X0; x <= X1; x++) out.push({ x, y });
    return out;
  }, []);
  const w = (X1 - X0 + 1) * TILE, h = (Y1 - Y0 + 1) * TILE;
  const ox = X0 * TILE, oy = Y0 * TILE;
  return (
    <div className="relative w-full overflow-hidden rounded-lg border border-line" style={{ aspectRatio: `${w} / ${h}` }}>
      <svg viewBox={`0 0 ${w} ${h}`} className="absolute inset-0 size-full cursor-crosshair" role="application" aria-label="Map of Bangladesh: click to set the factory location"
        onClick={(e) => {
          const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
          const px = ((e.clientX - r.left) / r.width) * w + ox, py = ((e.clientY - r.top) / r.height) * h + oy;
          onPick(Math.round(yToLat(py) * 1000) / 1000, Math.round(xToLon(px) * 1000) / 1000);
        }}>
        {tiles.map(({ x, y }) => <image key={`${x}-${y}`} href={`https://tile.openstreetmap.org/${Z}/${x}/${y}.png`} x={(x - X0) * TILE} y={(y - Y0) * TILE} width={TILE} height={TILE} />)}
        {lat !== null && lon !== null && (
          <g transform={`translate(${lonToX(lon) - ox} ${latToY(lat) - oy})`}>
            <circle r={14} fill="#0F766E" fillOpacity={0.25} /><circle r={6} fill="#0F766E" stroke="white" strokeWidth={2} />
          </g>
        )}
      </svg>
      <span className="absolute bottom-1 right-1 rounded bg-white/85 px-1.5 text-[10px] text-slate">© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap</a> contributors</span>
    </div>
  );
}
