import React, { useState, useEffect } from 'react';
import { getMyOrders } from '../services/api';
import socket from '../services/socket';
import { ClockIcon, CheckCircleIcon, TruckIcon, XCircleIcon } from '@heroicons/react/24/outline';

const statusConfig = {
    pending: { label: 'Chờ xác nhận', color: 'bg-yellow-100 text-yellow-700', icon: ClockIcon },
    confirmed: { label: 'Đã xác nhận', color: 'bg-blue-100 text-blue-700', icon: CheckCircleIcon },
    delivering: { label: 'Đang giao', color: 'bg-amber-100 text-amber-700', icon: TruckIcon },
    delivered: { label: 'Đã giao', color: 'bg-green-100 text-green-700', icon: CheckCircleIcon },
    cancelled: { label: 'Đã huỷ', color: 'bg-red-100 text-red-700', icon: XCircleIcon },
};

export default function MyOrdersPage() {
    const [orders, setOrders] = useState([]);
    const [loading, setLoading] = useState(true);

    const loadOrders = async () => {
        try {
            const res = await getMyOrders();
            setOrders(res.data.data);
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        loadOrders();
        socket.on('order-confirmed', loadOrders);
        socket.on('order-delivered', loadOrders);
        socket.on('order-cancelled', loadOrders);
        return () => {
            socket.off('order-confirmed');
            socket.off('order-delivered');
            socket.off('order-cancelled');
        };
    }, []); // eslint-disable-line

    if (loading) return <div className="flex justify-center py-12"><div className="w-8 h-8 border-4 border-amber-500 border-t-transparent rounded-full animate-spin" /></div>;

    return (
        <div className="max-w-3xl mx-auto">
            <h1 className="text-2xl font-bold text-gray-900 mb-6">Đơn hàng của tôi</h1>

            {orders.length === 0 ? (
                <div className="text-center py-16 text-gray-400">
                    <TruckIcon className="w-12 h-12 mx-auto mb-3" />
                    <p>Chưa có đơn hàng nào</p>
                </div>
            ) : (
                <div className="space-y-4">
                    {orders.map((order) => {
                        const cfg = statusConfig[order.status] || statusConfig.pending;
                        const Icon = cfg.icon;
                        return (
                            <div key={order._id} className="bg-white rounded-xl border border-gray-200 p-5">
                                <div className="flex items-start justify-between mb-3">
                                    <div>
                                        <p className="text-sm text-gray-400">#{order._id.slice(-6).toUpperCase()}</p>
                                        <p className="text-xs text-gray-400 mt-0.5">
                                            {new Date(order.createdAt).toLocaleString('vi-VN')}
                                        </p>
                                    </div>
                                    <span className={`inline-flex items-center gap-1 text-xs font-medium px-2.5 py-1 rounded-full ${cfg.color}`}>
                                        <Icon className="w-3.5 h-3.5" />
                                        {cfg.label}
                                    </span>
                                </div>
                                <div className="space-y-1 mb-3">
                                    {order.items?.map((item, idx) => (
                                        <div key={idx} className="flex justify-between text-sm">
                                            <span className="text-gray-600">{item.name} x{item.quantity}</span>
                                            <span className="text-gray-800">{(item.price * item.quantity).toLocaleString('vi-VN')}đ</span>
                                        </div>
                                    ))}
                                </div>
                                <div className="flex justify-between items-center border-t pt-3">
                                    <span className="text-xs text-gray-400">Giao đến: {order.destinationPoint}</span>
                                    <span className="font-semibold text-amber-600">{order.totalPrice?.toLocaleString('vi-VN')}đ</span>
                                </div>
                            </div>
                        );
                    })}
                </div>
            )}
        </div>
    );
}
