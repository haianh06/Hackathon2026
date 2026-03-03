import React, { useState, useEffect, useCallback } from 'react';
import { getOrders, confirmOrder, markDelivered, cancelOrder } from '../services/api';
import socket from '../services/socket';
import OrderList from '../components/OrderList';
import Notification from '../components/Notification';
import CameraView from '../components/CameraView';
import MotorControl from '../components/MotorControl';
import HardwareStatus from '../components/HardwareStatus';

function StaffPage() {
    const [orders, setOrders] = useState([]);
    const [notification, setNotification] = useState(null);

    const loadOrders = useCallback(async () => {
        try {
            const res = await getOrders();
            setOrders(res.data.data);
        } catch (err) {
            console.error('Error loading orders:', err);
        }
    }, []);

    useEffect(() => {
        loadOrders();
        socket.emit('join-room', 'staff');

        socket.on('new-order', (order) => {
            setNotification({ message: `🔔 Đơn hàng mới từ ${order.customerName}!`, type: 'info' });
            setOrders(prev => [order, ...prev]);
        });

        socket.on('order-confirmed', () => loadOrders());
        socket.on('order-delivered', () => loadOrders());
        socket.on('order-cancelled', () => loadOrders());

        return () => {
            socket.off('new-order');
            socket.off('order-confirmed');
            socket.off('order-delivered');
            socket.off('order-cancelled');
        };
    }, [loadOrders]);

    const handleConfirm = async (orderId) => {
        try {
            await confirmOrder(orderId);
            setNotification({ message: '✅ Đã xác nhận đơn hàng và điều xe!', type: 'success' });
            loadOrders();
        } catch (err) {
            setNotification({ message: 'Lỗi xác nhận đơn hàng', type: 'error' });
        }
    };

    const handleDeliver = async (orderId) => {
        try {
            await markDelivered(orderId);
            setNotification({ message: '📦 Đã đánh dấu giao thành công!', type: 'success' });
            loadOrders();
        } catch (err) {
            setNotification({ message: 'Lỗi cập nhật đơn hàng', type: 'error' });
        }
    };

    const handleCancel = async (orderId) => {
        try {
            await cancelOrder(orderId);
            setNotification({ message: '❌ Đã hủy đơn hàng', type: 'error' });
            loadOrders();
        } catch (err) {
            setNotification({ message: 'Lỗi hủy đơn hàng', type: 'error' });
        }
    };

    return (
        <div>
            <h1 className="page-title">👨‍💼 Quản lý đơn hàng (Staff)</h1>

            {notification && (
                <Notification
                    message={notification.message}
                    type={notification.type}
                    onClose={() => setNotification(null)}
                />
            )}

            <div style={{ marginBottom: '1rem', display: 'flex', gap: '1rem', alignItems: 'center' }}>
                <button className="btn btn-primary btn-sm" onClick={loadOrders}>
                    🔄 Tải lại
                </button>
                <span style={{ color: '#718096' }}>
                    {orders.filter(o => o.status === 'pending').length} đơn chờ xác nhận
                </span>
            </div>

            <OrderList
                orders={orders}
                onConfirm={handleConfirm}
                onDeliver={handleDeliver}
                onCancel={handleCancel}
            />

            {/* Camera & Hardware Control */}
            <div className="staff-hardware-section">
                <div className="staff-hw-left">
                    <CameraView />
                    <MotorControl />
                </div>
                <div className="staff-hw-right">
                    <HardwareStatus />
                </div>
            </div>
        </div>
    );
}

export default StaffPage;
