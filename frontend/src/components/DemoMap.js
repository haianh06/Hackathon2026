import React, { useRef, useEffect, useState, useCallback } from 'react';

/*
 * Map layout — single-lane grid (portrait orientation):
 *
 *   TL ─ ─ ─ ─ B ─ ─ ─ ─ S       Row 0 (top)
 *   │     ┌───────────┐     │
 *   │     │ Building B │     │
 *   │     └───────────┘     │
 *   P1 ─ ─ ─ P2 ─ ─ ─ ─ P3      Row 1
 *   │  [bld]   │   [bld]    │
 *   │          │             │
 *   A ─ ─ ─ ─ P4 ─ ─ ─ ─  C      Row 2
 *   │  [bld]   │   [bld]    │
 *   │          │             │
 *   ST ─ ─ ─  P5 ─ ─ ─ ─ P6      Row 3 (bottom)
 *
 *   All roads are single-lane, bidirectional (free movement).
 *   Dashed lines (─ ─) indicate road markings.
 *   White blocks [bld] are buildings.
 */

// Buildings (white rounded-rect areas) — coordinates in map space (4x scaled)
const OBSTACLES = [
    // Large top building with purple border (destination B area)
    { x: 440, y: 320, w: 1520, h: 500, purpleBorder: true },
    // Middle-left building (between Row 1 and Row 2)
    { x: 400, y: 1080, w: 580, h: 460 },
    // Middle-right building (between Row 1 and Row 2)
    { x: 1420, y: 1080, w: 580, h: 460 },
    // Bottom-left building (between Row 2 and Row 3)
    { x: 400, y: 1820, w: 580, h: 520 },
    // Bottom-right building (between Row 2 and Row 3)
    { x: 1420, y: 1820, w: 580, h: 520 },
];

const POINT_COLORS = {
    start: '#34A853',
    stop: '#EA4335',
    warehouse: '#EA4335',
    waypoint: '#607D8B',
    intersection: '#FB8C00',
    destination: '#34A853',
};

function DemoMap({ points, vehiclePosition, activePath, livePos }) {
    const canvasRef = useRef(null);
    const [hoveredPoint, setHoveredPoint] = useState(null);
    const transformRef = useRef({ offX: 0, offY: 0, mapScale: 1, minX: 0, minY: 0 });

    const drawMap = useCallback(() => {
        const canvas = canvasRef.current;
        if (!canvas) return;
        const ctx = canvas.getContext('2d');
        const W = canvas.width;
        const H = canvas.height;

        // ── 1. Background (gray road surface) ──
        ctx.fillStyle = '#8e8e8e';
        ctx.fillRect(0, 0, W, H);

        // ── Auto-fit: map coordinates → canvas pixels ──
        // Compute bounding box of all map content
        let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
        if (points && points.length > 0) {
            points.forEach(p => {
                minX = Math.min(minX, p.x); minY = Math.min(minY, p.y);
                maxX = Math.max(maxX, p.x); maxY = Math.max(maxY, p.y);
            });
        }
        OBSTACLES.forEach(o => {
            minX = Math.min(minX, o.x); minY = Math.min(minY, o.y);
            maxX = Math.max(maxX, o.x + o.w); maxY = Math.max(maxY, o.y + o.h);
        });
        const PAD = 40; // padding in canvas pixels
        const mapW = (maxX - minX) || 1;
        const mapH = (maxY - minY) || 1;
        const mapScale = Math.min((W - PAD * 2) / mapW, (H - PAD * 2) / mapH);
        const offX = PAD + ((W - PAD * 2) - mapW * mapScale) / 2;
        const offY = PAD + ((H - PAD * 2) - mapH * mapScale) / 2;
        // Helper: map coords → canvas coords
        const tx = (x) => offX + (x - minX) * mapScale;
        const ty = (y) => offY + (y - minY) * mapScale;
        const ts = (size) => size * mapScale; // scale a size/dimension
        transformRef.current = { offX, offY, mapScale, minX, minY };

        // ── 2. Buildings (white rounded rectangles) ──
        OBSTACLES.forEach(obs => {
            ctx.fillStyle = 'rgba(0,0,0,0.12)';
            ctx.beginPath();
            ctx.roundRect(tx(obs.x) + 3, ty(obs.y) + 3, ts(obs.w), ts(obs.h), 14);
            ctx.fill();
            ctx.fillStyle = '#ffffff';
            ctx.beginPath();
            ctx.roundRect(tx(obs.x), ty(obs.y), ts(obs.w), ts(obs.h), 14);
            ctx.fill();
            if (obs.purpleBorder) {
                ctx.strokeStyle = '#9b51e0';
                ctx.lineWidth = 3.5;
                ctx.beginPath();
                ctx.roundRect(tx(obs.x), ty(obs.y), ts(obs.w), ts(obs.h), 14);
                ctx.stroke();
            }
        });

        // ── 3. Points and interactive elements ──
        if (!points || points.length === 0) {
            ctx.fillStyle = '#fff';
            ctx.font = '16px "Segoe UI", sans-serif';
            ctx.textAlign = 'center';
            ctx.fillText('Đang tải bản đồ...', W / 2, H / 2);
            return;
        }

        const pointMap = {};
        points.forEach(p => { pointMap[p.pointId] = p; });

        // ── Road connections (dashed lane markings) ──
        const drawnEdges = new Set();
        points.forEach(point => {
            (point.connections || []).forEach(connId => {
                const ek = [point.pointId, connId].sort().join('-');
                if (drawnEdges.has(ek)) return;
                drawnEdges.add(ek);
                const t = pointMap[connId];
                if (!t) return;
                // Road surface strip
                ctx.strokeStyle = 'rgba(255,255,255,0.13)';
                ctx.lineWidth = 26;
                ctx.lineCap = 'round';
                ctx.setLineDash([]);
                ctx.beginPath();
                ctx.moveTo(tx(point.x), ty(point.y));
                ctx.lineTo(tx(t.x), ty(t.y));
                ctx.stroke();
                // Dashed center line
                ctx.strokeStyle = 'rgba(255,255,255,0.5)';
                ctx.lineWidth = 2;
                ctx.setLineDash([14, 10]);
                ctx.lineCap = 'butt';
                ctx.beginPath();
                ctx.moveTo(tx(point.x), ty(point.y));
                ctx.lineTo(tx(t.x), ty(t.y));
                ctx.stroke();
                ctx.setLineDash([]);
            });
        });

        // ── Active path (Dijkstra result) ──
        if (activePath && activePath.length > 1) {
            // Glow
            ctx.strokeStyle = 'rgba(66,133,244,0.30)';
            ctx.lineWidth = 22;
            ctx.lineCap = 'round';
            ctx.lineJoin = 'round';
            ctx.beginPath();
            ctx.moveTo(tx(activePath[0].x), ty(activePath[0].y));
            for (let i = 1; i < activePath.length; i++) ctx.lineTo(tx(activePath[i].x), ty(activePath[i].y));
            ctx.stroke();

            // Path line
            ctx.strokeStyle = '#4285F4';
            ctx.lineWidth = 5;
            ctx.setLineDash([10, 5]);
            ctx.beginPath();
            ctx.moveTo(tx(activePath[0].x), ty(activePath[0].y));
            for (let i = 1; i < activePath.length; i++) ctx.lineTo(tx(activePath[i].x), ty(activePath[i].y));
            ctx.stroke();
            ctx.setLineDash([]);

            // Direction arrows
            for (let i = 0; i < activePath.length - 1; i++) {
                const p1 = activePath[i], p2 = activePath[i + 1];
                const ax = (tx(p1.x) + tx(p2.x)) / 2, ay = (ty(p1.y) + ty(p2.y)) / 2;
                const angle = Math.atan2(ty(p2.y) - ty(p1.y), tx(p2.x) - tx(p1.x));
                ctx.save();
                ctx.translate(ax, ay);
                ctx.rotate(angle);
                ctx.fillStyle = '#4285F4';
                ctx.beginPath();
                ctx.moveTo(8, 0); ctx.lineTo(-5, -5); ctx.lineTo(-5, 5);
                ctx.closePath(); ctx.fill();
                ctx.restore();
            }

            // Total distance badge
            let totalDist = 0;
            for (let i = 0; i < activePath.length - 1; i++) {
                const a = activePath[i], b = activePath[i + 1];
                totalDist += Math.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2);
            }
            const badge = `Tổng: ${Math.round(totalDist)}px`;
            ctx.font = 'bold 12px "Segoe UI", sans-serif';
            const bw = ctx.measureText(badge).width + 16;
            ctx.fillStyle = '#4285F4';
            ctx.beginPath();
            ctx.roundRect(W - bw - 15, H - 38, bw, 26, 6);
            ctx.fill();
            ctx.fillStyle = '#fff';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(badge, W - bw / 2 - 15, H - 25);
            ctx.textBaseline = 'alphabetic';
        }

        // ── Node dots ──
        points.forEach(point => {
            const isStart = point.type === 'start';
            const isStop = point.type === 'stop';
            const isDest = point.type === 'destination';
            const color = POINT_COLORS[point.type] || '#607D8B';
            const r = (isStart || isStop || isDest) ? 16 : 10;

            // Shadow
            ctx.beginPath(); ctx.arc(tx(point.x) + 1, ty(point.y) + 1, r, 0, Math.PI * 2);
            ctx.fillStyle = 'rgba(0,0,0,0.2)'; ctx.fill();

            // Circle
            ctx.beginPath(); ctx.arc(tx(point.x), ty(point.y), r, 0, Math.PI * 2);
            ctx.fillStyle = color; ctx.fill();
            ctx.strokeStyle = '#fff'; ctx.lineWidth = 3; ctx.stroke();

            // Label inside
            ctx.fillStyle = '#fff';
            ctx.font = `bold ${(isStart || isStop) ? 9 : isDest ? 12 : 9}px "Segoe UI", sans-serif`;
            ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
            ctx.fillText(isStart ? 'Start' : isStop ? 'Stop' : point.pointId, tx(point.x), ty(point.y));
            ctx.textBaseline = 'alphabetic';

            // Label below
            if (isStart || isStop || isDest) {
                ctx.font = 'bold 11px "Segoe UI", sans-serif';
                const lbl = point.label;
                const lblW = ctx.measureText(lbl).width + 8;
                const ly = ty(point.y) + r + 14;
                ctx.fillStyle = 'rgba(255,255,255,0.92)';
                ctx.beginPath(); ctx.roundRect(tx(point.x) - lblW / 2, ly - 10, lblW, 15, 4); ctx.fill();
                ctx.fillStyle = '#333'; ctx.textAlign = 'center';
                ctx.fillText(lbl, tx(point.x), ly);
            }
        });

        // ── Live vehicle position (interpolated) ──
        if (livePos && livePos.x != null && livePos.y != null) {
            ctx.beginPath();
            ctx.arc(tx(livePos.x), ty(livePos.y), 26, 0, Math.PI * 2);
            ctx.fillStyle = 'rgba(66,133,244,0.25)';
            ctx.fill();
            ctx.font = '22px serif';
            ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
            ctx.fillText('\ud83d\ude97', tx(livePos.x), ty(livePos.y));
            ctx.textBaseline = 'alphabetic';
        } else if (vehiclePosition) {
            // Fallback: show car at waypoint
            const vp = pointMap[vehiclePosition];
            if (vp) {
                ctx.beginPath();
                ctx.arc(tx(vp.x), ty(vp.y), 26, 0, Math.PI * 2);
                ctx.fillStyle = 'rgba(66,133,244,0.25)';
                ctx.fill();
                ctx.font = '22px serif';
                ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
                ctx.fillText('\ud83d\ude97', tx(vp.x), ty(vp.y));
                ctx.textBaseline = 'alphabetic';
            }
        }

    }, [points, vehiclePosition, activePath, livePos]);

    useEffect(() => {
        drawMap();
    }, [drawMap]);

    const handleMouseMove = (e) => {
        if (!points) return;
        const canvas = canvasRef.current;
        const rect = canvas.getBoundingClientRect();
        const sx = canvas.width / rect.width;
        const sy = canvas.height / rect.height;
        const mx = (e.clientX - rect.left) * sx;
        const my = (e.clientY - rect.top) * sy;
        const { offX: tOffX, offY: tOffY, mapScale: tScale, minX: tMinX, minY: tMinY } = transformRef.current;
        const htx = (x) => tOffX + (x - tMinX) * tScale;
        const hty = (y) => tOffY + (y - tMinY) * tScale;
        let found = null;
        for (const p of points) {
            if ((htx(p.x) - mx) ** 2 + (hty(p.y) - my) ** 2 < 600) { found = p; break; }
        }
        setHoveredPoint(found);
    };

    return (
        <div className="map-canvas-wrapper" style={{ position: 'relative' }}>
            <canvas
                ref={canvasRef}
                width={600}
                height={700}
                style={{ width: '100%', maxWidth: 500, height: 'auto', borderRadius: '8px', cursor: hoveredPoint ? 'pointer' : 'default', margin: '0 auto', display: 'block' }}
                onMouseMove={handleMouseMove}
            />
            {hoveredPoint && (
                <div style={{
                    position: 'absolute', top: 10, right: 10,
                    background: 'rgba(255,255,255,0.96)', padding: '10px 14px',
                    borderRadius: '10px', boxShadow: '0 2px 12px rgba(0,0,0,0.15)',
                    fontSize: '0.85rem', lineHeight: 1.6, minWidth: 160,
                    borderLeft: `4px solid ${POINT_COLORS[hoveredPoint.type] || '#607D8B'}`
                }}>
                    <strong>{hoveredPoint.label}</strong><br />
                    ID: {hoveredPoint.pointId}<br />
                    Loại: {hoveredPoint.type === 'start' ? '🚀 Xuất phát' :
                        hoveredPoint.type === 'stop' ? '🏁 Kết thúc' :
                            hoveredPoint.type === 'destination' ? '📦 Giao hàng' :
                                hoveredPoint.type === 'intersection' ? '🔀 Ngã tư' : '📍 Trung gian'}<br />
                    Toạ độ: ({hoveredPoint.x}, {hoveredPoint.y})<br />
                    Kết nối: {(hoveredPoint.connections || []).join(', ')}
                </div>
            )}
        </div>
    );
}

export default DemoMap;
