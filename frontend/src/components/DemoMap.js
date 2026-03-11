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

// Buildings (white rounded-rect areas)
const OBSTACLES = [
    // Large top building with purple border (destination B area)
    { x: 110, y: 80, w: 380, h: 125, purpleBorder: true },
    // Middle-left building (between Row 1 and Row 2)
    { x: 100, y: 270, w: 145, h: 115 },
    // Middle-right building (between Row 1 and Row 2)
    { x: 355, y: 270, w: 145, h: 115 },
    // Bottom-left building (between Row 2 and Row 3)
    { x: 100, y: 455, w: 145, h: 130 },
    // Bottom-right building (between Row 2 and Row 3)
    { x: 355, y: 455, w: 145, h: 130 },
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

    const drawMap = useCallback(() => {
        const canvas = canvasRef.current;
        if (!canvas) return;
        const ctx = canvas.getContext('2d');
        const W = canvas.width;
        const H = canvas.height;

        // ── 1. Background (gray road surface) ──
        ctx.fillStyle = '#8e8e8e';
        ctx.fillRect(0, 0, W, H);

        // ── 2. Buildings (white rounded rectangles) ──
        OBSTACLES.forEach(obs => {
            ctx.fillStyle = 'rgba(0,0,0,0.12)';
            ctx.beginPath();
            ctx.roundRect(obs.x + 3, obs.y + 3, obs.w, obs.h, 14);
            ctx.fill();
            ctx.fillStyle = '#ffffff';
            ctx.beginPath();
            ctx.roundRect(obs.x, obs.y, obs.w, obs.h, 14);
            ctx.fill();
            if (obs.purpleBorder) {
                ctx.strokeStyle = '#9b51e0';
                ctx.lineWidth = 3.5;
                ctx.beginPath();
                ctx.roundRect(obs.x, obs.y, obs.w, obs.h, 14);
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
                ctx.moveTo(point.x, point.y);
                ctx.lineTo(t.x, t.y);
                ctx.stroke();
                // Dashed center line
                ctx.strokeStyle = 'rgba(255,255,255,0.5)';
                ctx.lineWidth = 2;
                ctx.setLineDash([14, 10]);
                ctx.lineCap = 'butt';
                ctx.beginPath();
                ctx.moveTo(point.x, point.y);
                ctx.lineTo(t.x, t.y);
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
            ctx.moveTo(activePath[0].x, activePath[0].y);
            for (let i = 1; i < activePath.length; i++) ctx.lineTo(activePath[i].x, activePath[i].y);
            ctx.stroke();

            // Path line
            ctx.strokeStyle = '#4285F4';
            ctx.lineWidth = 5;
            ctx.setLineDash([10, 5]);
            ctx.beginPath();
            ctx.moveTo(activePath[0].x, activePath[0].y);
            for (let i = 1; i < activePath.length; i++) ctx.lineTo(activePath[i].x, activePath[i].y);
            ctx.stroke();
            ctx.setLineDash([]);

            // Direction arrows
            for (let i = 0; i < activePath.length - 1; i++) {
                const p1 = activePath[i], p2 = activePath[i + 1];
                const ax = (p1.x + p2.x) / 2, ay = (p1.y + p2.y) / 2;
                const angle = Math.atan2(p2.y - p1.y, p2.x - p1.x);
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
            ctx.beginPath(); ctx.arc(point.x + 1, point.y + 1, r, 0, Math.PI * 2);
            ctx.fillStyle = 'rgba(0,0,0,0.2)'; ctx.fill();

            // Circle
            ctx.beginPath(); ctx.arc(point.x, point.y, r, 0, Math.PI * 2);
            ctx.fillStyle = color; ctx.fill();
            ctx.strokeStyle = '#fff'; ctx.lineWidth = 3; ctx.stroke();

            // Label inside
            ctx.fillStyle = '#fff';
            ctx.font = `bold ${(isStart || isStop) ? 9 : isDest ? 12 : 9}px "Segoe UI", sans-serif`;
            ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
            ctx.fillText(isStart ? 'Start' : isStop ? 'Stop' : point.pointId, point.x, point.y);
            ctx.textBaseline = 'alphabetic';

            // Label below
            if (isStart || isStop || isDest) {
                ctx.font = 'bold 11px "Segoe UI", sans-serif';
                const lbl = point.label;
                const lblW = ctx.measureText(lbl).width + 8;
                const ly = point.y + r + 14;
                ctx.fillStyle = 'rgba(255,255,255,0.92)';
                ctx.beginPath(); ctx.roundRect(point.x - lblW / 2, ly - 10, lblW, 15, 4); ctx.fill();
                ctx.fillStyle = '#333'; ctx.textAlign = 'center';
                ctx.fillText(lbl, point.x, ly);
            }
        });

        // ── Live vehicle position (interpolated) ──
        if (livePos && livePos.x != null && livePos.y != null) {
            ctx.beginPath();
            ctx.arc(livePos.x, livePos.y, 26, 0, Math.PI * 2);
            ctx.fillStyle = 'rgba(66,133,244,0.25)';
            ctx.fill();
            ctx.font = '22px serif';
            ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
            ctx.fillText('\ud83d\ude97', livePos.x, livePos.y);
            ctx.textBaseline = 'alphabetic';
        } else if (vehiclePosition) {
            // Fallback: show car at waypoint
            const vp = pointMap[vehiclePosition];
            if (vp) {
                ctx.beginPath();
                ctx.arc(vp.x, vp.y, 26, 0, Math.PI * 2);
                ctx.fillStyle = 'rgba(66,133,244,0.25)';
                ctx.fill();
                ctx.font = '22px serif';
                ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
                ctx.fillText('\ud83d\ude97', vp.x, vp.y);
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
        let found = null;
        for (const p of points) {
            if ((p.x - mx) ** 2 + (p.y - my) ** 2 < 600) { found = p; break; }
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
