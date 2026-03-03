import React, { useState, useEffect } from 'react';
import { getDeliveryStats, getDeliveryLogs } from '../services/api';
import {
    TruckIcon, CheckCircleIcon, XCircleIcon, ClockIcon,
    ChartBarIcon
} from '@heroicons/react/24/outline';

export default function AdminDashboard() {
    const [stats, setStats] = useState(null);
    const [recentLogs, setRecentLogs] = useState([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        loadData();
    }, []);

    const loadData = async () => {
        try {
            const [statsRes, logsRes] = await Promise.all([
                getDeliveryStats(),
                getDeliveryLogs({ limit: 5 })
            ]);
            setStats(statsRes.data.data);
            setRecentLogs(logsRes.data.data || []);
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

    if (loading) return <div className="flex justify-center py-12"><div className="w-8 h-8 border-4 border-amber-500 border-t-transparent rounded-full animate-spin" /></div>;

    const cards = [
        { label: 'Tổng giao hàng', value: stats?.total || 0, icon: TruckIcon, color: 'bg-amber-50 text-amber-600 border-amber-200' },
        { label: 'Hoàn thành', value: stats?.completed || 0, icon: CheckCircleIcon, color: 'bg-green-50 text-green-600 border-green-200' },
        { label: 'Thất bại', value: stats?.failed || 0, icon: XCircleIcon, color: 'bg-red-50 text-red-600 border-red-200' },
        { label: 'TB thời gian', value: stats?.avgTimeProcess ? `${stats.avgTimeProcess.toFixed(1)}s` : '—', icon: ClockIcon, color: 'bg-blue-50 text-blue-600 border-blue-200' },
    ];

    return (
        <div className="max-w-5xl mx-auto">
            <div className="flex items-center justify-between mb-6">
                <h1 className="text-2xl font-bold text-gray-900">Dashboard</h1>
                <button onClick={loadData} className="text-sm text-gray-400 hover:text-amber-600 transition">Refresh</button>
            </div>

            {/* Stats cards */}
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
                {cards.map((card, i) => {
                    const Icon = card.icon;
                    return (
                        <div key={i} className={`rounded-2xl border p-5 ${card.color}`}>
                            <Icon className="w-8 h-8 mb-2 opacity-70" />
                            <p className="text-3xl font-bold">{card.value}</p>
                            <p className="text-sm opacity-70 mt-1">{card.label}</p>
                        </div>
                    );
                })}
            </div>

            {/* Success rate bar */}
            {stats && stats.total > 0 && (
                <div className="bg-white rounded-2xl border border-gray-200 p-5 mb-8">
                    <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wide mb-3 flex items-center gap-1.5">
                        <ChartBarIcon className="w-4 h-4" /> Tỉ lệ giao hàng thành công
                    </h2>
                    <div className="w-full bg-gray-100 rounded-full h-4 overflow-hidden">
                        <div className="bg-green-500 h-4 rounded-full transition-all duration-500"
                            style={{ width: `${((stats.completed / stats.total) * 100).toFixed(0)}%` }} />
                    </div>
                    <div className="flex justify-between text-xs text-gray-400 mt-1">
                        <span>{((stats.completed / stats.total) * 100).toFixed(1)}% thành công</span>
                        <span>{stats.completed}/{stats.total}</span>
                    </div>
                </div>
            )}

            {/* Recent deliveries */}
            <div className="bg-white rounded-2xl border border-gray-200 overflow-hidden">
                <div className="px-5 py-3 border-b bg-gray-50">
                    <h2 className="text-sm font-semibold text-gray-600">Giao hàng gần đây</h2>
                </div>
                {recentLogs.length === 0 ? (
                    <div className="text-center py-12 text-gray-400 text-sm">Chưa có dữ liệu</div>
                ) : (
                    <div className="divide-y">
                        {recentLogs.map(log => (
                            <div key={log._id} className="px-5 py-3 flex items-center justify-between">
                                <div>
                                    <p className="text-sm font-medium text-gray-800">
                                        {log.customer?.displayName || log.customer?.username || '—'} → {log.destinationPoint}
                                    </p>
                                    <p className="text-xs text-gray-400">
                                        Staff: {log.staff?.displayName || log.staff?.username || '—'}
                                        &middot; {log.startAt ? new Date(log.startAt).toLocaleString('vi-VN') : '?'}
                                    </p>
                                </div>
                                <div className="text-right">
                                    <span className={`text-xs font-medium px-2 py-1 rounded-full ${log.status === 'completed' ? 'bg-green-100 text-green-700' :
                                            log.status === 'failed' ? 'bg-red-100 text-red-700' :
                                                'bg-yellow-100 text-yellow-700'
                                        }`}>{log.status}</span>
                                    {log.timeProcess != null && (
                                        <p className="text-xs text-gray-400 mt-1">{log.timeProcess.toFixed(1)}s</p>
                                    )}
                                </div>
                            </div>
                        ))}
                    </div>
                )}
            </div>
        </div>
    );
}
