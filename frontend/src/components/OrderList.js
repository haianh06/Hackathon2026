import React from 'react';

function OrderList({ orders, onConfirm, onDeliver, onCancel }) {
    if (orders.length === 0) {
        return (
            <div className="empty-state">
                <div className="empty-state-emoji">📭</div>
                <p>Chưa có đơn hàng nào</p>
            </div>
        );
    }

    const getStatusLabel = (status) => {
        const labels = {
            pending: '⏳ Chờ xác nhận',
            confirmed: '✅ Đã xác nhận',
            delivering: '🚗 Đang giao',
            delivered: '📦 Đã giao',
            cancelled: '❌ Đã hủy'
        };
        return labels[status] || status;
    };

    const formatTime = (dateStr) => {
        const d = new Date(dateStr);
        return d.toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' })
            + ' ' + d.toLocaleDateString('vi-VN');
    };

    return (
        <div className="order-list">
            {orders.map((order) => (
                <div key={order._id} className={`order-card ${order.status}`}>
                    <div className="order-header">
                        <span className="order-id">#{order._id.slice(-6).toUpperCase()}</span>
                        <span className={`order-status status-${order.status}`}>
                            {getStatusLabel(order.status)}
                        </span>
                    </div>
                    <div className="order-customer">👤 {order.customerName}</div>
                    <div className="order-destination">📍 Giao đến: {order.destinationPoint}</div>
                    <div className="order-items">
                        {order.items.map((item, idx) => (
                            <div key={idx} className="order-item-row">
                                {item.name} x{item.quantity} — {(item.price * item.quantity).toLocaleString('vi-VN')}đ
                            </div>
                        ))}
                    </div>
                    <div className="order-total">
                        Tổng: {order.totalPrice.toLocaleString('vi-VN')}đ
                    </div>
                    {order.note && (
                        <div style={{ fontSize: '0.85rem', color: '#718096', marginTop: '0.3rem' }}>
                            📝 {order.note}
                        </div>
                    )}
                    <div className="order-time">{formatTime(order.createdAt)}</div>
                    <div className="order-actions">
                        {order.status === 'pending' && (
                            <>
                                <button className="btn btn-success btn-sm" onClick={() => onConfirm(order._id)}>
                                    ✅ Xác nhận & Gửi xe
                                </button>
                                <button className="btn btn-danger btn-sm" onClick={() => onCancel(order._id)}>
                                    ❌ Hủy
                                </button>
                            </>
                        )}
                        {(order.status === 'confirmed' || order.status === 'delivering') && (
                            <button className="btn btn-success btn-sm" onClick={() => onDeliver(order._id)}>
                                📦 Đã giao xong
                            </button>
                        )}
                    </div>
                </div>
            ))}
        </div>
    );
}

export default OrderList;
