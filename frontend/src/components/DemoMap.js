import React, { useRef, useEffect, useState, useCallback } from 'react';

/*
 * Map layout matching the physical track (portrait orientation):
 *
 *   TL ──────── B ──────── S    Row 0 (→ right)
 *   │   ╔══════════════╗   │
 *   │   ║  Large Rect  ║   │    (purple border)
 *   │   ║     (B)      ║   │
 *   │   ╚══════════════╝   │
 *   P1 ─────────────── P2  Row 1 (← left)
 *   │ ══════barrier══════ │
 *   P3 ── P4 ──────── R1  Row 2 (→ right)
 *   │  [obs]  │  [obs]  │
 *   │         │          │
 *   A ── P5 ──────── C    Row 3 (← left)
 *   │ ══════barrier══════ │
 *   P7 ─────────────── P8  Row 4 (→ right)
 *   │  [obs]     [obs]  │
 *   │                    │
 *   ST ── P9 ──────── P6  Row 5 (← left)
 *
 *   ↑ left side  |  ↓ right side (outer clockwise loop)
 */

// Obstacles (white rounded-rect areas surrounded by roads)
const OBSTACLES = [
    // Large top rectangle with purple border (destination B area)
    { x: 100, y: 55, w: 400, h: 130, purpleBorder: true },
    // Middle-left (between row 2 and row 3)
    { x: 110, y: 335, w: 130, h: 100 },
    // Middle-right (between row 2 and row 3)
    { x: 360, y: 335, w: 130, h: 100 },
    // Bottom-left (between row 4 and row 5)
    { x: 110, y: 597, w: 130, h: 120 },
    // Bottom-right (between row 4 and row 5)
    { x: 360, y: 597, w: 130, h: 120 },
];

// Horizontal barrier lines (black walls with gaps at sides)
const BARRIERS = [
    { y: 255, xStart: 40, xEnd: 560 },
    { y: 508, xStart: 40, xEnd: 560 },
];

// Direction arrows on roads — two-way (bidirectional) arrows
// Each entry: [fromX, fromY, toX, toY]
// Each road segment gets TWO arrows showing both directions
const FLOW_ARROWS = [
    // Top road (horizontal, two-way)
    [140, 28, 300, 28],       // → right
    [430, 42, 270, 42],       // ← left
    // Below rect (horizontal, two-way)
    [430, 203, 270, 203],     // ← left
    [140, 217, 300, 217],     // → right
    // Left side (vertical, two-way)
    [43, 600, 43, 200],       // ↑ up
    [57, 130, 57, 660],       // ↓ down
    // Right side (vertical, two-way)
    [543, 200, 543, 600],     // ↓ down
    [557, 660, 557, 130],     // ↑ up
    // Upper corridor (horizontal, two-way)
    [140, 293, 300, 293],     // → right
    [430, 307, 270, 307],     // ← left
    // Center vertical (two-way)
    [293, 340, 293, 430],     // ↓ down
    [307, 430, 307, 340],     // ↑ up
    // Lower corridor (horizontal, two-way)
    [430, 463, 270, 463],     // ← left
    [140, 477, 300, 477],     // → right
    // Below barrier (horizontal, two-way)
    [140, 538, 300, 538],     // → right
    [430, 552, 270, 552],     // ← left
    // Bottom road (horizontal, two-way)
    [430, 763, 270, 763],     // ← left
    [140, 777, 300, 777],     // → right
    // Left bottom vertical (two-way)
    [43, 720, 43, 500],       // ↑ up
    [57, 500, 57, 720],       // ↓ down
    // Right bottom vertical (two-way)
    [543, 500, 543, 720],     // ↓ down
    [557, 720, 557, 500],     // ↑ up
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

        // ── Background (gray road surface) ──
        ctx.fillStyle = '#9e9e9e';
        ctx.fillRect(0, 0, W, H);

        // ── Obstacles (white rounded rectangles) ──
        OBSTACLES.forEach(obs => {
            // Shadow
            ctx.fillStyle = 'rgba(0,0,0,0.15)';
            ctx.beginPath();
            ctx.roundRect(obs.x + 3, obs.y + 3, obs.w, obs.h, 18);
            ctx.fill();
            // Body
            ctx.fillStyle = '#ffffff';
            ctx.beginPath();
            ctx.roundRect(obs.x, obs.y, obs.w, obs.h, 18);
            ctx.fill();
            // Purple border for top rectangle
            if (obs.purpleBorder) {
                ctx.strokeStyle = '#7c3aed';
                ctx.lineWidth = 3;
                ctx.beginPath();
                ctx.roundRect(obs.x, obs.y, obs.w, obs.h, 18);
                ctx.stroke();
            }
        });

        // ── Barrier lines (thick black horizontal lines) ──
        BARRIERS.forEach(bar => {
            ctx.strokeStyle = '#333';
            ctx.lineWidth = 5;
            ctx.lineCap = 'round';
            ctx.beginPath();
            ctx.moveTo(bar.xStart, bar.y);
            ctx.lineTo(bar.xEnd, bar.y);
            ctx.stroke();
        });

        // ── Direction arrows on roads ──
        FLOW_ARROWS.forEach(([fx, fy, tx, ty]) => {
            const mx = (fx + tx) / 2;
            const my = (fy + ty) / 2;
            const angle = Math.atan2(ty - fy, tx - fx);
            const headLen = 12;

            ctx.save();
            ctx.translate(mx, my);
            ctx.rotate(angle);
            // Arrow shaft
            const shaftLen = 20;
            ctx.strokeStyle = 'rgba(255,255,255,0.5)';
            ctx.lineWidth = 2;
            ctx.beginPath();
            ctx.moveTo(-shaftLen, 0);
            ctx.lineTo(shaftLen, 0);
            ctx.stroke();
            // Arrow head
            ctx.fillStyle = 'rgba(255,255,255,0.5)';
            ctx.beginPath();
            ctx.moveTo(headLen + 6, 0);
            ctx.lineTo(-headLen / 2 + 6, -headLen / 2);
            ctx.lineTo(-headLen / 2 + 6, headLen / 2);
            ctx.closePath();
            ctx.fill();
            ctx.restore();
        });

        if (!points || points.length === 0) {
            ctx.fillStyle = '#fff';
            ctx.font = '16px "Segoe UI", sans-serif';
            ctx.textAlign = 'center';
            ctx.fillText('Đang tải bản đồ...', W / 2, H / 2);
            return;
        }

        const pointMap = {};
        points.forEach(p => { pointMap[p.pointId] = p; });

        // ── Roads (connections) — thick gray road with dashed center ──
        const drawnEdges = new Set();
        points.forEach(point => {
            (point.connections || []).forEach(connId => {
                const ek = [point.pointId, connId].sort().join('-');
                if (drawnEdges.has(ek)) return;
                drawnEdges.add(ek);
                const t = pointMap[connId];
                if (!t) return;

                // Road bed (dark gray)
                ctx.strokeStyle = '#757575';
                ctx.lineWidth = 28;
                ctx.lineCap = 'round';
                ctx.beginPath(); ctx.moveTo(point.x, point.y); ctx.lineTo(t.x, t.y); ctx.stroke();

                // Road surface (lighter gray)
                ctx.strokeStyle = '#9e9e9e';
                ctx.lineWidth = 24;
                ctx.beginPath(); ctx.moveTo(point.x, point.y); ctx.lineTo(t.x, t.y); ctx.stroke();

                // Road center dashed line (white)
                ctx.strokeStyle = 'rgba(255,255,255,0.7)';
                ctx.lineWidth = 2;
                ctx.setLineDash([8, 8]);
                ctx.beginPath(); ctx.moveTo(point.x, point.y); ctx.lineTo(t.x, t.y); ctx.stroke();
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
                height={800}
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
