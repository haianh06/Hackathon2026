import React, { useState, useEffect } from 'react';
import { getDeliveryLogs } from '../services/api';
import { FunnelIcon, ArrowPathIcon, TableCellsIcon } from '@heroicons/react/24/outline';

export default function AdminLogsPage() {
    const [logs, setLogs] = useState([]);
    const [loading, setLoading] = useState(true);
    const [staffFilter, setStaffFilter] = useState('');
    const [statusFilter, setStatusFilter] = useState('');
    const [staffList, setStaffList] = useState([]);

    const loadLogs = async () => {
        setLoading(true);
        try {
            const params = {};
            if (staffFilter) params.staffId = staffFilter;
            if (statusFilter) params.status = statusFilter;
            const res = await getDeliveryLogs(params);
            const data = res.data.data || [];
            setLogs(data);

            // Extract unique staff names
            const staffs = new Map();
            data.forEach(l => {
                if (l.staff?._id) staffs.set(l.staff._id, l.staff.displayName || l.staff.username);
            });
            setStaffList(Array.from(staffs, ([id, name]) => ({ id, name })));
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => { loadLogs(); }, [staffFilter, statusFilter]); // eslint-disable-line

    const fmtDate = (d) => d ? new Date(d).toLocaleString('vi-VN') : '—';
    const fmtTime = (s) => s != null ? `${s.toFixed(1)}s` : '—';

    const statusBadge = (st) => {
        const map = {
            completed: 'bg-green-100 text-green-700',
            failed: 'bg-red-100 text-red-700',
            'in-progress': 'bg-amber-100 text-amber-700',
            pending: 'bg-yellow-100 text-yellow-700',
        };
        return map[st] || 'bg-gray-100 text-gray-600';
    };

    return (
        <div className="max-w-6xl mx-auto">
            <div className="flex items-center justify-between mb-6">
                <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
                    <TableCellsIcon className="w-7 h-7 text-amber-500" />
                    Lịch sử giao hàng
                </h1>
                <button onClick={loadLogs} className="inline-flex items-center gap-1.5 text-sm text-gray-400 hover:text-amber-600 transition">
                    <ArrowPathIcon className="w-4 h-4" /> Refresh
                </button>
            </div>

            {/* Filters */}
            <div className="bg-white rounded-xl border border-gray-200 p-4 mb-6 flex flex-wrap items-center gap-4">
                <FunnelIcon className="w-5 h-5 text-gray-400" />
                <select value={staffFilter} onChange={e => setStaffFilter(e.target.value)}
                    className="rounded-lg border-gray-200 text-sm focus:ring-amber-500 focus:border-amber-500">
                    <option value="">Tất cả nhân viên</option>
                    {staffList.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
                </select>
                <select value={statusFilter} onChange={e => setStatusFilter(e.target.value)}
                    className="rounded-lg border-gray-200 text-sm focus:ring-amber-500 focus:border-amber-500">
                    <option value="">Tất cả trạng thái</option>
                    <option value="completed">Completed</option>
                    <option value="in-progress">In Progress</option>
                    <option value="failed">Failed</option>
                    <option value="pending">Pending</option>
                </select>
                <span className="text-xs text-gray-400 ml-auto">{logs.length} kết quả</span>
            </div>

            {/* Table */}
            {loading ? (
                <div className="flex justify-center py-12"><div className="w-8 h-8 border-4 border-amber-500 border-t-transparent rounded-full animate-spin" /></div>
            ) : logs.length === 0 ? (
                <div className="text-center py-16 text-gray-400">Không có dữ liệu</div>
            ) : (
                <div className="bg-white rounded-2xl border border-gray-200 overflow-hidden">
                    <div className="overflow-x-auto">
                        <table className="w-full text-sm">
                            <thead>
                                <tr className="bg-gray-50 text-left text-xs font-semibold text-gray-500 uppercase tracking-wide">
                                    <th className="px-4 py-3">#</th>
                                    <th className="px-4 py-3">Khách hàng</th>
                                    <th className="px-4 py-3">Nhân viên</th>
                                    <th className="px-4 py-3">Điểm giao</th>
                                    <th className="px-4 py-3">Bắt đầu</th>
                                    <th className="px-4 py-3">Kết thúc</th>
                                    <th className="px-4 py-3">Thời gian</th>
                                    <th className="px-4 py-3">Trạng thái</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-gray-100">
                                {logs.map((log, idx) => (
                                    <tr key={log._id} className="hover:bg-amber-50/40 transition">
                                        <td className="px-4 py-3 text-gray-400 font-mono text-xs">{idx + 1}</td>
                                        <td className="px-4 py-3">
                                            <p className="font-medium text-gray-800">{log.customer?.displayName || log.customer?.username || '—'}</p>
                                            <p className="text-xs text-gray-400">{log.customer?._id?.slice(-6).toUpperCase() || ''}</p>
                                        </td>
                                        <td className="px-4 py-3">
                                            <p className="font-medium text-gray-800">{log.staff?.displayName || log.staff?.username || '—'}</p>
                                        </td>
                                        <td className="px-4 py-3 font-medium text-amber-600">{log.destinationPoint || '—'}</td>
                                        <td className="px-4 py-3 text-gray-500 text-xs">{fmtDate(log.startAt)}</td>
                                        <td className="px-4 py-3 text-gray-500 text-xs">{fmtDate(log.endAt)}</td>
                                        <td className="px-4 py-3 font-mono font-bold text-gray-800">{fmtTime(log.timeProcess)}</td>
                                        <td className="px-4 py-3">
                                            <span className={`text-xs font-medium px-2.5 py-1 rounded-full ${statusBadge(log.status)}`}>
                                                {log.status}
                                            </span>
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </div>
            )}
        </div>
    );
}
