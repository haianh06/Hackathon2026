import React, { useState, useEffect, useCallback } from 'react';
import { getMyOrders, customerConfirmOrder } from '../services/api';
import { useAuth } from '../contexts/AuthContext';
import socket from '../services/socket';
import {
    ClockIcon, CheckCircleIcon, TruckIcon, XCircleIcon,
    BellAlertIcon, HandThumbUpIcon, MapPinIcon
} from '@heroicons/react/24/outline';

const statusConfig = {
    pending: { label: 'Chờ xác nhận', color: 'bg-yellow-100 text-yellow-700', icon: ClockIcon },
    confirmed: { label: 'Đã xác nhận', color: 'bg-blue-100 text-blue-700', icon: CheckCircleIcon },
    delivering: { label: 'Đang giao', color: 'bg-amber-100 text-amber-700', icon: TruckIcon },
    arrived: { label: 'Đã đến nơi', color: 'bg-green-100 text-green-700 animate-pulse', icon: MapPinIcon },
    delivered: { label: 'Hoàn thành', color: 'bg-green-100 text-green-700', icon: CheckCircleIcon },
    cancelled: { label: 'Đã huỷ', color: 'bg-red-100 text-red-700', icon: XCircleIcon },
};

export default function MyOrdersPage() {
    const { user } = useAuth();
    const [orders, setOrders] = useState([]);
    const [loading, setLoading] = useState(true);
    const [confirming, setConfirming] = useState(null);
    const [notifications, setNotifications] = useState([]);
    const [vehicleReturning, setVehicleReturning] = useState(false);

    const addNotification = useCallback((notif) => {
        const id = Date.now() + Math.random();
        setNotifications(prev => [...prev, { ...notif, id }]);
        // Auto-dismiss after 10 seconds
        setTimeout(() => {
            setNotifications(prev => prev.filter(n => n.id !== id));
        }, 10000);
    }, []);

    const removeNotification = useCallback((id) => {
        setNotifications(prev => prev.filter(n => n.id !== id));
    }, []);

    const loadOrders = useCallback(async () => {
        try {
            const res = await getMyOrders();
            setOrders(res.data.data);
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        loadOrders();

        // Join personal customer room for targeted notifications
        if (user && user.id) {
            socket.emit('join-customer', user.id);
        }

        // Listen for delivery notification (vehicle arrived at destination)
        const handleDeliveryNotif = (data) => {
            addNotification({
                type: 'arrived',
                title: data.title,
                message: data.message,
                orderId: data.orderId
            });
            // Also reload orders to update status
            loadOrders();
            // Play notification sound
            try {
                const audio = new Audio('data:audio/wav;base64,UklGRnoGAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQoGAACBhYqFbF1fdJivrJBhNjVgip2teleagoSQoJ+Wj3tsd3eIlZmThHBfX2qEoJ6Ui3thYWl+kpSSjIF0bHiIk5CJfnJsdoKSk5CIfnRueIWSkI6GfHVweISQj4yFfnd0eIKMjImCe3h4fYaMi4eCfHl6fIWKiYaCfXt8foSIhoN/fX1+g4aFg4B+f4CDhYSCgH9/gIKEg4KAgICBgoOCgYGAgYGCgoGBgYGBgoKBgYGBgYKCgYGBgQ==');
                audio.volume = 0.5;
                audio.play().catch(() => { });
            } catch (e) { }
        };

        // Listen for general order events
        const handleOrderArrived = (data) => {
            loadOrders();
        };

        // Listen for vehicle returning / returned events
        const handleVehicleReturning = () => {
            setVehicleReturning(true);
        };
        const handleVehicleReturned = () => {
            setVehicleReturning(false);
            addNotification({
                type: 'success',
                title: 'Xe đã về!',
                message: 'Xe đã trở về điểm xuất phát.',
            });
        };

        // Listen for batch-point-progress (real-time remaining count at a stop)
        const handleBatchProgress = (data) => {
            addNotification({
                type: 'arrived',
                title: `Còn ${data.remaining} đơn cần xác nhận`,
                message: data.message,
            });
            loadOrders();
        };

        socket.on('delivery-notification', handleDeliveryNotif);
        socket.on('order-arrived', handleOrderArrived);
        socket.on('order-confirmed', loadOrders);
        socket.on('order-delivered', loadOrders);
        socket.on('order-cancelled', loadOrders);
        socket.on('vehicle-returning', handleVehicleReturning);
        socket.on('vehicle-returned', handleVehicleReturned);
        socket.on('batch-point-progress', handleBatchProgress);

        // Also listen for new-notification (persistent notifications from DB)
        const handleNewNotification = (notif) => {
            if (notif.type === 'order_confirmed') {
                addNotification({
                    type: 'success',
                    title: notif.title,
                    message: notif.message,
                    orderId: notif.order?._id || notif.order
                });
                loadOrders();
            } else if (notif.type === 'order_delivered') {
                addNotification({
                    type: 'success',
                    title: notif.title,
                    message: notif.message,
                    orderId: notif.order?._id || notif.order
                });
                loadOrders();
            }
        };
        socket.on('new-notification', handleNewNotification);

        return () => {
            socket.off('delivery-notification', handleDeliveryNotif);
            socket.off('order-arrived', handleOrderArrived);
            socket.off('order-confirmed');
            socket.off('order-delivered');
            socket.off('order-cancelled');
            socket.off('vehicle-returning', handleVehicleReturning);
            socket.off('vehicle-returned', handleVehicleReturned);
            socket.off('batch-point-progress', handleBatchProgress);
            socket.off('new-notification', handleNewNotification);
        };
    }, [loadOrders, user, addNotification]);

    const handleCustomerConfirm = async (orderId) => {
        setConfirming(orderId);
        try {
            const res = await customerConfirmOrder(orderId);
            const remaining = res.data?.remainingAtPoint || 0;
            if (remaining > 0) {
                addNotification({
                    type: 'arrived',
                    title: 'Đã xác nhận!',
                    message: `Còn ${remaining} đơn hàng cần xác nhận tại điểm này. Vui lòng xác nhận hết để xe tiếp tục.`,
                    orderId
                });
            } else {
                // Check if there are more delivering/confirmed orders in this session
                const hasMoreOrders = orders.some(o =>
                    o._id !== orderId && (o.status === 'confirmed' || o.status === 'delivering')
                );
                if (hasMoreOrders) {
                    addNotification({
                        type: 'success',
                        title: 'Đã xác nhận!',
                        message: 'Cảm ơn bạn! Xe sẽ tự động di chuyển đến điểm giao tiếp theo.',
                        orderId
                    });
                } else {
                    addNotification({
                        type: 'success',
                        title: 'Đã xác nhận tất cả!',
                        message: 'Cảm ơn bạn! Không còn đơn hàng nào. Xe sẽ tự động quay về điểm xuất phát.',
                        orderId
                    });
                    setVehicleReturning(true);
                }
            }
            loadOrders();
        } catch (err) {
            addNotification({
                type: 'error',
                title: 'Lỗi',
                message: 'Không thể xác nhận đơn hàng. Vui lòng thử lại.',
                orderId
            });
        } finally {
            setConfirming(null);
        }
    };

    if (loading) return <div className="flex justify-center py-12"><div className="w-8 h-8 border-4 border-amber-500 border-t-transparent rounded-full animate-spin" /></div>;

    return (
        <div className="max-w-3xl mx-auto">
            {/* Notification toasts */}
            <div className="fixed top-4 right-4 z-50 space-y-3 max-w-sm">
                {notifications.map((notif) => (
                    <div
                        key={notif.id}
                        className={`rounded-xl shadow-2xl border p-4 transform transition-all duration-300 animate-slide-in ${notif.type === 'arrived'
                            ? 'bg-green-50 border-green-300'
                            : notif.type === 'success'
                                ? 'bg-blue-50 border-blue-300'
                                : 'bg-red-50 border-red-300'
                            }`}
                    >
                        <div className="flex items-start gap-3">
                            <div className={`p-2 rounded-full ${notif.type === 'arrived' ? 'bg-green-100' :
                                notif.type === 'success' ? 'bg-blue-100' : 'bg-red-100'
                                }`}>
                                {notif.type === 'arrived' ? (
                                    <BellAlertIcon className="w-5 h-5 text-green-600 animate-bounce" />
                                ) : notif.type === 'success' ? (
                                    <CheckCircleIcon className="w-5 h-5 text-blue-600" />
                                ) : (
                                    <XCircleIcon className="w-5 h-5 text-red-600" />
                                )}
                            </div>
                            <div className="flex-1 min-w-0">
                                <p className={`text-sm font-bold ${notif.type === 'arrived' ? 'text-green-800' :
                                    notif.type === 'success' ? 'text-blue-800' : 'text-red-800'
                                    }`}>
                                    {notif.title}
                                </p>
                                <p className="text-xs text-gray-600 mt-1">{notif.message}</p>
                            </div>
                            <button onClick={() => removeNotification(notif.id)} className="text-gray-400 hover:text-gray-600">
                                <XCircleIcon className="w-4 h-4" />
                            </button>
                        </div>
                    </div>
                ))}
            </div>

            <h1 className="text-2xl font-bold text-gray-900 mb-6">Đơn hàng của tôi</h1>

            {/* Vehicle returning banner */}
            {vehicleReturning && (
                <div className="mb-6 bg-blue-50 border border-blue-200 rounded-xl p-4 flex items-center gap-3">
                    <TruckIcon className="w-6 h-6 text-blue-600 animate-bounce flex-shrink-0" />
                    <div>
                        <p className="text-sm font-bold text-blue-800">Xe đang trên đường về điểm xuất phát</p>
                        <p className="text-xs text-blue-600">Tất cả đơn hàng đã được xác nhận. Xe đang tự động quay về.</p>
                    </div>
                </div>
            )}

            {/* Arrived orders alert banner */}
            {orders.filter(o => o.status === 'arrived').length > 0 && (
                <div className="mb-6 bg-green-50 border border-green-200 rounded-xl p-4 flex items-center gap-3">
                    <BellAlertIcon className="w-6 h-6 text-green-600 animate-bounce flex-shrink-0" />
                    <div>
                        <p className="text-sm font-bold text-green-800">
                            {orders.filter(o => o.status === 'arrived').length > 1
                                ? `${orders.filter(o => o.status === 'arrived').length} đơn hàng đã đến!`
                                : 'Đơn hàng đã đến!'}
                        </p>
                        <p className="text-xs text-green-600">
                            {orders.filter(o => o.status === 'arrived').length > 1
                                ? 'Vui lòng xác nhận tất cả đơn hàng để xe tiếp tục hành trình.'
                                : 'Vui lòng lấy hàng và bấm "Xác nhận đã nhận" để xe quay về.'}
                        </p>
                    </div>
                </div>
            )}

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
                        const isArrived = order.status === 'arrived';

                        return (
                            <div key={order._id} className={`bg-white rounded-xl border p-5 transition-all ${isArrived
                                ? 'border-green-400 ring-2 ring-green-100 shadow-lg'
                                : 'border-gray-200'
                                }`}>
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

                                {/* Customer confirm button for arrived orders */}
                                {isArrived && (
                                    <div className="mt-4 pt-3 border-t border-green-100">
                                        <div className="bg-green-50 rounded-lg p-3 mb-3">
                                            <div className="flex items-center gap-2 text-green-700">
                                                <BellAlertIcon className="w-5 h-5 animate-bounce" />
                                                <div>
                                                    <p className="text-sm font-bold">Xe đã đến điểm giao hàng!</p>
                                                    {orders.filter(o => o.status === 'arrived' && o.destinationPoint === order.destinationPoint).length > 1 ? (
                                                        <p className="text-xs text-green-600">
                                                            Có {orders.filter(o => o.status === 'arrived' && o.destinationPoint === order.destinationPoint).length} đơn tại điểm {order.destinationPoint}. Xác nhận hết để xe tiếp tục.
                                                        </p>
                                                    ) : (
                                                        <p className="text-xs text-green-600">Vui lòng lấy hàng và bấm xác nhận bên dưới.</p>
                                                    )}
                                                </div>
                                            </div>
                                        </div>
                                        <button
                                            onClick={() => handleCustomerConfirm(order._id)}
                                            disabled={confirming === order._id}
                                            className="w-full inline-flex items-center justify-center gap-2 px-4 py-3 bg-green-500 text-white rounded-xl text-sm font-bold hover:bg-green-600 disabled:opacity-50 transition shadow-lg shadow-green-200"
                                        >
                                            {confirming === order._id ? (
                                                <div className="w-5 h-5 border-2 border-white border-t-transparent rounded-full animate-spin" />
                                            ) : (
                                                <HandThumbUpIcon className="w-5 h-5" />
                                            )}
                                            Xác nhận đã nhận hàng
                                        </button>
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
