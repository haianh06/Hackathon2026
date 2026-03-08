import React, { useState, useEffect, useCallback } from 'react';
import socket from '../services/socket';
import { getRfids, lookupRfid, saveRfid, deleteRfid } from '../services/api';
import {
    SignalIcon,
    TrashIcon,
    PencilSquareIcon,
    CheckCircleIcon,
    StopCircleIcon,
} from '@heroicons/react/24/outline';

export default function RfidPage() {
    const [rfidList, setRfidList] = useState([]);
    const [currentRfid, setCurrentRfid] = useState(null); // { rfidId, name, x, y, isNew }
    const [form, setForm] = useState({ name: '', x: 0, y: 0 });
    const [loading, setLoading] = useState(false);
    const [scanning, setScanning] = useState(false);
    const [message, setMessage] = useState(null);

    // Load all RFIDs
    const loadRfids = useCallback(async () => {
        try {
            const res = await getRfids();
            setRfidList(res.data.data || []);
        } catch {
            setRfidList([]);
        }
    }, []);

    useEffect(() => {
        loadRfids();
    }, [loadRfids]);

    // Listen for RFID scanned events from hardware
    useEffect(() => {
        socket.emit('join-room', 'admin');

        const handleRfidScanned = (data) => {
            if (data?.rfidId) {
                setScanning(false);
                handleLookup(data.rfidId);
            }
        };

        const handleScanStatus = (data) => {
            setScanning(data?.scanning || false);
            if (data?.error) {
                setMessage({ type: 'error', text: `Lỗi RFID: ${data.error}` });
            }
        };

        socket.on('rfid-scanned', handleRfidScanned);
        socket.on('rfid-scan-status', handleScanStatus);
        return () => {
            socket.off('rfid-scanned', handleRfidScanned);
            socket.off('rfid-scan-status', handleScanStatus);
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    // Start hardware RFID scan
    const handleStartScan = useCallback(() => {
        setScanning(true);
        setMessage({ type: 'info', text: 'Đang quét... Đưa thẻ RFID lại gần đầu đọc.' });
        setCurrentRfid(null);
        setForm({ name: '', x: 0, y: 0 });
        socket.emit('rfid-start-scan');
    }, []);

    // Stop hardware RFID scan
    const handleStopScan = useCallback(() => {
        setScanning(false);
        setMessage(null);
        socket.emit('rfid-stop-scan');
    }, []);

    // Lookup RFID by ID (called when hardware detects a tag)
    const handleLookup = useCallback(async (rfidId) => {
        const id = rfidId.trim();
        if (!id) return;
        setLoading(true);
        setMessage(null);
        try {
            const res = await lookupRfid(id);
            const { data, isNew } = res.data;
            if (isNew || !data) {
                setCurrentRfid({ rfidId: id, isNew: true });
                setForm({ name: '', x: 0, y: 0 });
                setMessage({ type: 'info', text: `RFID "${id}" chưa có trong hệ thống. Nhập thông tin để lưu mới.` });
            } else {
                setCurrentRfid({ rfidId: id, isNew: false, _id: data._id });
                setForm({ name: data.name, x: data.x, y: data.y });
                setMessage({ type: 'success', text: `Đã tìm thấy RFID "${id}". Bạn có thể chỉnh sửa thông tin.` });
            }
        } catch (err) {
            setMessage({ type: 'error', text: 'Lỗi tra cứu RFID: ' + (err.response?.data?.message || err.message) });
        } finally {
            setLoading(false);
        }
    }, []);

    // Save (create or update)
    const handleSave = useCallback(async () => {
        if (!currentRfid) return;
        if (!form.name.trim()) {
            setMessage({ type: 'error', text: 'Tên RFID không được để trống' });
            return;
        }
        setLoading(true);
        try {
            await saveRfid({ rfidId: currentRfid.rfidId, name: form.name.trim(), x: Number(form.x), y: Number(form.y) });
            setMessage({ type: 'success', text: currentRfid.isNew ? 'Đã lưu RFID mới thành công!' : 'Đã cập nhật RFID thành công!' });
            setCurrentRfid(prev => ({ ...prev, isNew: false }));
            await loadRfids();
        } catch (err) {
            setMessage({ type: 'error', text: 'Lỗi lưu: ' + (err.response?.data?.message || err.message) });
        } finally {
            setLoading(false);
        }
    }, [currentRfid, form, loadRfids]);

    // Delete
    const handleDelete = useCallback(async (rfidId) => {
        if (!window.confirm(`Xoá RFID "${rfidId}"?`)) return;
        try {
            await deleteRfid(rfidId);
            setMessage({ type: 'success', text: 'Đã xoá RFID' });
            if (currentRfid?.rfidId === rfidId) {
                setCurrentRfid(null);
                setForm({ name: '', x: 0, y: 0 });
            }
            await loadRfids();
        } catch (err) {
            setMessage({ type: 'error', text: 'Lỗi xoá: ' + (err.response?.data?.message || err.message) });
        }
    }, [currentRfid, loadRfids]);

    // Select from list
    const handleSelect = useCallback((rfid) => {
        setCurrentRfid({ rfidId: rfid.rfidId, isNew: false, _id: rfid._id });
        setForm({ name: rfid.name, x: rfid.x, y: rfid.y });
        setMessage(null);
    }, []);

    const msgColors = { info: 'bg-blue-50 text-blue-700 border-blue-200', success: 'bg-green-50 text-green-700 border-green-200', error: 'bg-red-50 text-red-700 border-red-200' };

    return (
        <div className="max-w-5xl mx-auto">
            <h1 className="text-2xl font-bold text-gray-900 mb-5 flex items-center gap-2">
                <SignalIcon className="w-7 h-7 text-amber-500" /> Quản lý RFID
            </h1>

            <div className="grid grid-cols-1 lg:grid-cols-12 gap-5">

                {/* Left: Scan + Edit */}
                <div className="lg:col-span-5 space-y-4">
                    {/* Scan button */}
                    <div className="bg-white rounded-2xl border border-gray-200 p-5">
                        <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">Quét RFID</h2>
                        {!scanning ? (
                            <button
                                onClick={handleStartScan}
                                className="w-full px-4 py-4 bg-amber-500 text-white rounded-xl text-base font-bold hover:bg-amber-600 transition flex items-center justify-center gap-2 shadow-md"
                            >
                                <SignalIcon className="w-6 h-6" />
                                Bắt đầu quét thẻ RFID
                            </button>
                        ) : (
                            <div className="space-y-3">
                                <div className="flex items-center justify-center gap-3 py-4 bg-amber-50 rounded-xl border-2 border-amber-300 border-dashed">
                                    <div className="w-4 h-4 bg-amber-500 rounded-full animate-pulse" />
                                    <span className="text-amber-700 font-semibold">Đang quét... Đưa thẻ lại gần</span>
                                </div>
                                <button
                                    onClick={handleStopScan}
                                    className="w-full px-4 py-2.5 bg-red-500 text-white rounded-xl text-sm font-bold hover:bg-red-600 transition flex items-center justify-center gap-2"
                                >
                                    <StopCircleIcon className="w-5 h-5" />
                                    Dừng quét
                                </button>
                            </div>
                        )}
                        <p className="text-[10px] text-gray-400 mt-2">Nhấn nút để bắt đầu quét, đưa thẻ RFID lại gần đầu đọc</p>
                    </div>

                    {/* Message */}
                    {message && (
                        <div className={`rounded-xl border px-4 py-3 text-sm ${msgColors[message.type]}`}>
                            {message.text}
                        </div>
                    )}

                    {/* Edit form */}
                    {currentRfid && (
                        <div className="bg-white rounded-2xl border border-gray-200 p-5">
                            <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">
                                {currentRfid.isNew ? '✨ RFID mới' : '✏️ Chỉnh sửa RFID'}
                            </h2>

                            <div className="mb-3 p-3 bg-gray-50 rounded-lg">
                                <span className="text-xs text-gray-400">RFID ID:</span>
                                <p className="text-sm font-mono font-bold text-amber-600">{currentRfid.rfidId}</p>
                            </div>

                            <div className="space-y-3">
                                <div>
                                    <label className="text-xs text-gray-500 mb-1 block">Tên RFID</label>
                                    <input
                                        type="text"
                                        value={form.name}
                                        onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
                                        placeholder="VD: Điểm A, Kho hàng..."
                                        className="w-full px-3 py-2 text-sm border rounded-lg focus:ring-2 focus:ring-amber-400 focus:border-amber-400"
                                    />
                                </div>
                                <div className="grid grid-cols-2 gap-3">
                                    <div>
                                        <label className="text-xs text-gray-500 mb-1 block">Toạ độ X</label>
                                        <input
                                            type="number"
                                            value={form.x}
                                            onChange={e => setForm(f => ({ ...f, x: e.target.value }))}
                                            className="w-full px-3 py-2 text-sm border rounded-lg focus:ring-2 focus:ring-amber-400 focus:border-amber-400"
                                        />
                                    </div>
                                    <div>
                                        <label className="text-xs text-gray-500 mb-1 block">Toạ độ Y</label>
                                        <input
                                            type="number"
                                            value={form.y}
                                            onChange={e => setForm(f => ({ ...f, y: e.target.value }))}
                                            className="w-full px-3 py-2 text-sm border rounded-lg focus:ring-2 focus:ring-amber-400 focus:border-amber-400"
                                        />
                                    </div>
                                </div>
                            </div>

                            <button
                                onClick={handleSave}
                                disabled={loading}
                                className="mt-4 w-full inline-flex items-center justify-center gap-2 px-4 py-2.5 bg-green-500 text-white rounded-xl text-sm font-bold hover:bg-green-600 disabled:opacity-40 transition shadow-md"
                            >
                                <CheckCircleIcon className="w-5 h-5" />
                                {currentRfid.isNew ? 'Lưu RFID mới' : 'Lưu thay đổi'}
                            </button>
                        </div>
                    )}
                </div>

                {/* Right: RFID list */}
                <div className="lg:col-span-7">
                    <div className="bg-white rounded-2xl border border-gray-200 overflow-hidden">
                        <div className="px-5 py-3 border-b bg-gray-50">
                            <h2 className="text-sm font-semibold text-gray-600">
                                Danh sách RFID ({rfidList.length})
                            </h2>
                        </div>
                        {rfidList.length === 0 ? (
                            <div className="p-8 text-center text-gray-400 text-sm">
                                Chưa có RFID nào. Quét thẻ để bắt đầu!
                            </div>
                        ) : (
                            <div className="divide-y divide-gray-100 max-h-[600px] overflow-y-auto">
                                {rfidList.map(rfid => (
                                    <div
                                        key={rfid.rfidId}
                                        className={`flex items-center justify-between px-5 py-3 hover:bg-amber-50 transition cursor-pointer ${currentRfid?.rfidId === rfid.rfidId ? 'bg-amber-50 border-l-4 border-amber-400' : ''}`}
                                        onClick={() => handleSelect(rfid)}
                                    >
                                        <div className="flex-1 min-w-0">
                                            <div className="flex items-center gap-2">
                                                <SignalIcon className="w-4 h-4 text-amber-500 flex-shrink-0" />
                                                <span className="text-sm font-semibold text-gray-800 truncate">{rfid.name}</span>
                                            </div>
                                            <div className="flex items-center gap-3 mt-1">
                                                <span className="text-[10px] font-mono text-gray-400 bg-gray-100 px-1.5 py-0.5 rounded">{rfid.rfidId}</span>
                                                <span className="text-xs text-gray-500">X: {rfid.x}, Y: {rfid.y}</span>
                                            </div>
                                        </div>
                                        <div className="flex items-center gap-1 ml-3">
                                            <button
                                                onClick={e => { e.stopPropagation(); handleSelect(rfid); }}
                                                className="p-1.5 rounded-lg hover:bg-blue-100 text-blue-500 transition"
                                                title="Sửa"
                                            >
                                                <PencilSquareIcon className="w-4 h-4" />
                                            </button>
                                            <button
                                                onClick={e => { e.stopPropagation(); handleDelete(rfid.rfidId); }}
                                                className="p-1.5 rounded-lg hover:bg-red-100 text-red-500 transition"
                                                title="Xoá"
                                            >
                                                <TrashIcon className="w-4 h-4" />
                                            </button>
                                        </div>
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>
                </div>
            </div>
        </div>
    );
}
