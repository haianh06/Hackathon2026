import React, { useState, useEffect, useCallback } from 'react';
import { getOrders, getPendingOrders, confirmOrder, cancelOrder, getMapPoints, getVehicleStatus, findPath } from '../services/api';
import socket from '../services/socket';
import DemoMap from '../components/DemoMap';
import {
    CheckCircleIcon, TruckIcon, XCircleIcon, ClockIcon,
    ArrowPathIcon, FunnelIcon, MapIcon
} from '@heroicons/react/24/outline';

const statusConfig = {
    pending: { label: 'Chờ xác nhận', color: 'bg-yellow-100 text-yellow-700 border-yellow-200', icon: ClockIcon },
    confirmed: { label: 'Đã xác nhận', color: 'bg-blue-100 text-blue-700 border-blue-200', icon: CheckCircleIcon },
    delivering: { label: 'Đang giao', color: 'bg-amber-100 text-amber-700 border-amber-200', icon: TruckIcon },
    arrived: { label: 'Đã đến nơi', color: 'bg-green-100 text-green-700 border-green-200', icon: CheckCircleIcon },
    delivered: { label: 'Hoàn thành', color: 'bg-green-100 text-green-700 border-green-200', icon: CheckCircleIcon },
    cancelled: { label: 'Đã huỷ', color: 'bg-red-100 text-red-700 border-red-200', icon: XCircleIcon },
};

export default function StaffOrdersPage() {
    const [orders, setOrders] = useState([]);
    const [filter, setFilter] = useState('all'); // all | pending
    const [loading, setLoading] = useState(true);
    const [notification, setNotification] = useState(null);
    const [confirming, setConfirming] = useState(null);

    // Map state
    const [mapPoints, setMapPoints] = useState([]);
    const [expandedMapOrders, setExpandedMapOrders] = useState({}); // orderId -> { path, livePos }
    const [livePos, setLivePos] = useState(null);
    const [vehiclePosition, setVehiclePosition] = useState(null);

    // Load map points once
    useEffect(() => {
        (async () => {
            try {
                const [mapRes, vehicleRes] = await Promise.all([getMapPoints(), getVehicleStatus()]);
                setMapPoints(mapRes.data.data || []);
                if (vehicleRes.data.data) setVehiclePosition(vehicleRes.data.data.currentPosition);
            } catch (e) { console.error(e); }
        })();
    }, []);

    // Listen for live vehicle position
    useEffect(() => {
        const handleNavLog = (data) => {
            if (data.type === 'moving' || data.type === 'waypoint' || data.type === 'start') {
                if (data.x != null && data.y != null) {
                    setLivePos({ x: data.x, y: data.y });
                }
            }
            if (data.type === 'complete' || data.type === 'cancelled') {
                setLivePos(null);
            }
        };
        const handleVehiclePos = (data) => {
            if (data.pointId) setVehiclePosition(data.pointId);
        };

        socket.on('navigation-log', handleNavLog);
        socket.on('vehicle-position', handleVehiclePos);
        socket.on('vehicle-returned', () => { setLivePos(null); setVehiclePosition('S'); });

        return () => {
            socket.off('navigation-log', handleNavLog);
            socket.off('vehicle-position', handleVehiclePos);
            socket.off('vehicle-returned');
        };
    }, []);

    const toggleMapForOrder = async (orderId, destinationPoint) => {
        if (expandedMapOrders[orderId]) {
            // Close map
            setExpandedMapOrders(prev => {
                const next = { ...prev };
                delete next[orderId];
                return next;
            });
            return;
        }
        // Open map: fetch path from S to destinationPoint
        try {
            const res = await findPath('S', destinationPoint);
            setExpandedMapOrders(prev => ({
                ...prev,
                [orderId]: { path: res.data.data }
            }));
        } catch (e) {
            console.error('Error finding path:', e);
            // Still show map without path
            setExpandedMapOrders(prev => ({
                ...prev,
                [orderId]: { path: null }
            }));
        }
    };

    const loadOrders = useCallback(async () => {
        try {
            const res = filter === 'pending' ? await getPendingOrders() : await getOrders();
            setOrders(res.data.data);
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    }, [filter]);

    useEffect(() => {
        loadOrders();
        socket.emit('join-room', 'staff');

        socket.on('new-order', (order) => {
            setNotification({ message: `Đơn hàng mới từ ${order.customerName || 'Khách hàng'}!`, type: 'info' });
            setOrders(prev => [order, ...prev]);
        });
        socket.on('order-confirmed', loadOrders);
        socket.on('order-delivered', loadOrders);
        socket.on('order-cancelled', loadOrders);
        socket.on('order-arrived', loadOrders);

        return () => {
            socket.off('new-order');
            socket.off('order-confirmed');
            socket.off('order-delivered');
            socket.off('order-cancelled');
            socket.off('order-arrived');
        };
    }, [loadOrders]);

    // Auto-dismiss notification
    useEffect(() => {
        if (notification) {
            const t = setTimeout(() => setNotification(null), 4000);
            return () => clearTimeout(t);
        }
    }, [notification]);

    const handleConfirm = async (orderId) => {
        setConfirming(orderId);
        try {
            await confirmOrder(orderId);
            setNotification({ message: 'Đã xác nhận đơn hàng & điều xe tự động!', type: 'success' });
            loadOrders();
        } catch (err) {
            setNotification({ message: 'Lỗi xác nhận đơn hàng', type: 'error' });
        } finally {
            setConfirming(null);
        }
    };

    const handleCancel = async (orderId) => {
        try {
            await cancelOrder(orderId);
            setNotification({ message: 'Đã huỷ đơn hàng', type: 'error' });
            loadOrders();
        } catch (err) {
            setNotification({ message: 'Lỗi huỷ đơn hàng', type: 'error' });
        }
    };

    const pendingCount = orders.filter(o => o.status === 'pending').length;
    const deliveringCount = orders.filter(o => o.status === 'delivering').length;

    return (
        <div className="max-w-4xl mx-auto">
            {/* Notification toast */}
            {notification && (
                <div className={`fixed top-4 right-4 z-50 px-4 py-3 rounded-lg shadow-lg text-sm font-medium animate-pulse ${notification.type === 'success' ? 'bg-green-500 text-white' :
                    notification.type === 'error' ? 'bg-red-500 text-white' :
                        'bg-blue-500 text-white'
                    }`}>
                    {notification.message}
                </div>
            )}

            <div className="flex items-center justify-between mb-6">
                <h1 className="text-2xl font-bold text-gray-900">Quản lý đơn hàng</h1>
                <button onClick={loadOrders} className="inline-flex items-center gap-1.5 text-sm text-gray-500 hover:text-amber-600">
                    <ArrowPathIcon className="w-4 h-4" /> Tải lại
                </button>
            </div>

            {/* Stats bar */}
            <div className="grid grid-cols-3 gap-3 mb-6">
                <div className="bg-yellow-50 rounded-xl p-4 text-center border border-yellow-100">
                    <p className="text-2xl font-bold text-yellow-600">{pendingCount}</p>
                    <p className="text-xs text-yellow-500">Chờ xác nhận</p>
                </div>
                <div className="bg-amber-50 rounded-xl p-4 text-center border border-amber-100">
                    <p className="text-2xl font-bold text-amber-600">{deliveringCount}</p>
                    <p className="text-xs text-amber-500">Đang giao</p>
                </div>
                <div className="bg-green-50 rounded-xl p-4 text-center border border-green-100">
                    <p className="text-2xl font-bold text-green-600">{orders.filter(o => o.status === 'delivered').length}</p>
                    <p className="text-xs text-green-500">Đã giao</p>
                </div>
            </div>

            {/* Filter tabs */}
            <div className="flex gap-2 mb-4">
                {[
                    { key: 'all', label: 'Tất cả' },
                    { key: 'pending', label: `Chờ duyệt (${pendingCount})` }
                ].map(tab => (
                    <button key={tab.key}
                        onClick={() => setFilter(tab.key)}
                        className={`px-4 py-2 rounded-lg text-sm font-medium transition ${filter === tab.key
                            ? 'bg-amber-500 text-white shadow'
                            : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                            }`}>
                        <FunnelIcon className="w-4 h-4 inline mr-1" />
                        {tab.label}
                    </button>
                ))}
            </div>

            {/* Orders list */}
            {loading ? (
                <div className="flex justify-center py-12"><div className="w-8 h-8 border-4 border-amber-500 border-t-transparent rounded-full animate-spin" /></div>
            ) : orders.length === 0 ? (
                <div className="text-center py-16 text-gray-400">
                    <TruckIcon className="w-12 h-12 mx-auto mb-3" />
                    <p>Không có đơn hàng nào</p>
                </div>
            ) : (
                <div className="space-y-4">
                    {orders.map(order => {
                        const cfg = statusConfig[order.status] || statusConfig.pending;
                        const Icon = cfg.icon;
                        const isPending = order.status === 'pending';
                        const isDelivering = order.status === 'delivering' || order.status === 'confirmed';
                        const isArrived = order.status === 'arrived';

                        return (
                            <div key={order._id} className={`bg-white rounded-xl border p-5 ${isPending ? 'border-yellow-300 ring-1 ring-yellow-100' : 'border-gray-200'}`}>
                                <div className="flex items-start justify-between mb-3">
                                    <div>
                                        <p className="font-semibold text-gray-800">
                                            {order.customerName || order.customer?.username || 'Khách hàng'}
                                        </p>
                                        <p className="text-xs text-gray-400">
                                            #{order._id.slice(-6).toUpperCase()} &middot; {new Date(order.createdAt).toLocaleString('vi-VN')}
                                        </p>
                                    </div>
                                    <span className={`inline-flex items-center gap-1 text-xs font-medium px-2.5 py-1 rounded-full border ${cfg.color}`}>
                                        <Icon className="w-3.5 h-3.5" /> {cfg.label}
                                    </span>
                                </div>

                                {/* Items */}
                                <div className="space-y-1 mb-3 text-sm">
                                    {order.items?.map((item, idx) => (
                                        <div key={idx} className="flex justify-between">
                                            <span className="text-gray-600">{item.name} &times;{item.quantity}</span>
                                            <span className="text-gray-800">{(item.price * item.quantity).toLocaleString('vi-VN')}đ</span>
                                        </div>
                                    ))}
                                </div>

                                <div className="flex justify-between items-center border-t pt-3">
                                    <div className="text-xs text-gray-400">
                                        Giao đến: <span className="font-medium text-gray-600">{order.destinationPoint}</span>
                                        {order.note && <span className="ml-2 italic">"{order.note}"</span>}
                                    </div>
                                    <span className="font-bold text-amber-600">{order.totalPrice?.toLocaleString('vi-VN')}đ</span>
                                </div>

                                {/* Action buttons */}
                                <div className="flex gap-2 mt-4 pt-3 border-t">
                                    {isPending && (
                                        <button
                                            onClick={() => handleConfirm(order._id)}
                                            disabled={confirming === order._id}
                                            className="flex-1 inline-flex items-center justify-center gap-1.5 px-4 py-2 bg-amber-500 text-white rounded-lg text-sm font-medium hover:bg-amber-600 disabled:opacity-50 transition">
                                            {confirming === order._id ? (
                                                <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
                                            ) : (
                                                <TruckIcon className="w-4 h-4" />
                                            )}
                                            Xác nhận & Giao hàng tự động
                                        </button>
                                    )}
                                    {isArrived && (
                                        <div className="flex-1 inline-flex items-center justify-center gap-1.5 px-4 py-2 bg-green-50 text-green-700 rounded-lg text-sm font-medium border border-green-200">
                                            <CheckCircleIcon className="w-4 h-4" />
                                            Chờ khách hàng xác nhận
                                        </div>
                                    )}
                                    <button
                                        onClick={() => toggleMapForOrder(order._id, order.destinationPoint)}
                                        className={`inline-flex items-center justify-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium transition ${expandedMapOrders[order._id]
                                            ? 'bg-blue-500 text-white hover:bg-blue-600'
                                            : 'bg-blue-50 text-blue-600 hover:bg-blue-100'
                                            }`}>
                                        <MapIcon className="w-4 h-4" />
                                        {expandedMapOrders[order._id] ? 'Ẩn Map' : 'Xem Map'}
                                    </button>
                                    {(isPending || isDelivering || isArrived) && (
                                        <button
                                            onClick={() => handleCancel(order._id)}
                                            className="inline-flex items-center justify-center gap-1.5 px-4 py-2 bg-red-50 text-red-600 rounded-lg text-sm font-medium hover:bg-red-100 transition">
                                            <XCircleIcon className="w-4 h-4" />
                                            Huỷ
                                        </button>
                                    )}
                                </div>

                                {/* Inline Map for this order */}
                                {expandedMapOrders[order._id] && mapPoints.length > 0 && (
                                    <div className="mt-4 pt-4 border-t border-gray-100">
                                        <div className="flex items-center gap-2 mb-3">
                                            <MapIcon className="w-4 h-4 text-blue-500" />
                                            <span className="text-sm font-medium text-gray-700">
                                                Bản đồ giao hàng — Điểm giao: <span className="text-blue-600 font-bold">{order.destinationPoint}</span>
                                            </span>
                                        </div>
                                        <div className="bg-gray-50 rounded-xl p-3 border border-gray-100">
                                            <DemoMap
                                                points={mapPoints}
                                                vehiclePosition={vehiclePosition}
                                                activePath={expandedMapOrders[order._id].path}
                                                livePos={isDelivering ? livePos : null}
                                            />
                                            {expandedMapOrders[order._id].path && (
                                                <div className="mt-2 text-xs text-gray-500 text-center">
                                                    Tuyến: {expandedMapOrders[order._id].path.map(p => p.pointId).join(' → ')}
                                                </div>
                                            )}
                                        </div>
                                    </div>
                                )}
                            </div>
                        );
                    })}
                </div>
            )}
        </div>
    );
}
