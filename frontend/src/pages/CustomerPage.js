import React, { useState, useEffect } from 'react';
import { getProducts, getDestinations, createOrder } from '../services/api';
import socket from '../services/socket';
import ProductCard from '../components/ProductCard';
import Cart from '../components/Cart';
import Notification from '../components/Notification';

function CustomerPage() {
    const [products, setProducts] = useState([]);
    const [cart, setCart] = useState({});
    const [destinations, setDestinations] = useState([]);
    const [customerName, setCustomerName] = useState('');
    const [selectedDest, setSelectedDest] = useState('');
    const [note, setNote] = useState('');
    const [notification, setNotification] = useState(null);
    const [orderSuccess, setOrderSuccess] = useState(null);
    const [loading, setLoading] = useState(false);

    useEffect(() => {
        loadData();

        socket.on('order-confirmed', (data) => {
            setNotification({ message: '✅ Đơn hàng đã được xác nhận! Xe đang đến...', type: 'success' });
        });

        socket.on('order-delivered', () => {
            setNotification({ message: '📦 Đơn hàng đã giao thành công!', type: 'success' });
        });

        return () => {
            socket.off('order-confirmed');
            socket.off('order-delivered');
        };
    }, []);

    const loadData = async () => {
        try {
            const [prodRes, destRes] = await Promise.all([getProducts(), getDestinations()]);
            setProducts(prodRes.data.data);
            setDestinations(destRes.data.data);
        } catch (err) {
            console.error('Error loading data:', err);
        }
    };

    const addToCart = (product) => {
        setCart(prev => ({
            ...prev,
            [product._id]: {
                product: product._id,
                name: product.name,
                price: product.price,
                image: product.image,
                quantity: (prev[product._id]?.quantity || 0) + 1
            }
        }));
    };

    const removeFromCart = (productId) => {
        setCart(prev => {
            const updated = { ...prev };
            if (updated[productId]) {
                updated[productId] = {
                    ...updated[productId],
                    quantity: updated[productId].quantity - 1
                };
                if (updated[productId].quantity <= 0) {
                    delete updated[productId];
                }
            }
            return updated;
        });
    };

    const cartItems = Object.values(cart);
    const total = cartItems.reduce((sum, item) => sum + item.price * item.quantity, 0);

    const handleOrder = async () => {
        if (cartItems.length === 0) {
            setNotification({ message: 'Vui lòng chọn sản phẩm!', type: 'error' });
            return;
        }
        if (!selectedDest) {
            setNotification({ message: 'Vui lòng chọn vị trí nhận hàng!', type: 'error' });
            return;
        }

        setLoading(true);
        try {
            const orderData = {
                items: cartItems,
                customerName: customerName || 'Khách hàng',
                destinationPoint: selectedDest,
                note
            };
            const res = await createOrder(orderData);
            setOrderSuccess(res.data.data);
            setCart({});
            setNote('');
        } catch (err) {
            setNotification({ message: 'Lỗi đặt hàng: ' + (err.response?.data?.message || err.message), type: 'error' });
        } finally {
            setLoading(false);
        }
    };

    return (
        <div>
            <h1 className="page-title">🛒 Chọn món hàng</h1>

            {notification && (
                <Notification
                    message={notification.message}
                    type={notification.type}
                    onClose={() => setNotification(null)}
                />
            )}

            {orderSuccess && (
                <div className="success-overlay" onClick={() => setOrderSuccess(null)}>
                    <div className="success-box" onClick={(e) => e.stopPropagation()}>
                        <div className="success-emoji">🎉</div>
                        <div className="success-text">Đặt hàng thành công!</div>
                        <div className="success-sub">
                            Mã đơn: #{orderSuccess._id.slice(-6).toUpperCase()}<br />
                            Giao đến: {orderSuccess.destinationPoint}<br />
                            Tổng: {orderSuccess.totalPrice.toLocaleString('vi-VN')}đ
                        </div>
                        <p style={{ fontSize: '0.85rem', color: '#718096' }}>
                            Vui lòng chờ staff xác nhận đơn hàng. Xe sẽ đến vị trí của bạn.
                        </p>
                        <button className="btn btn-primary" onClick={() => setOrderSuccess(null)} style={{ marginTop: '1rem' }}>
                            OK
                        </button>
                    </div>
                </div>
            )}

            <div className="product-grid">
                {products.map(product => (
                    <ProductCard
                        key={product._id}
                        product={product}
                        quantity={cart[product._id]?.quantity || 0}
                        onAdd={addToCart}
                        onRemove={removeFromCart}
                    />
                ))}
            </div>

            <Cart items={cartItems} total={total} />

            {cartItems.length > 0 && (
                <div className="cart-section" style={{ marginTop: '1rem' }}>
                    <h3 className="cart-title">📋 Thông tin đặt hàng</h3>
                    <div className="cart-form">
                        <input
                            className="form-input"
                            placeholder="Tên của bạn (tuỳ chọn)"
                            value={customerName}
                            onChange={(e) => setCustomerName(e.target.value)}
                        />
                        <select
                            className="form-select"
                            value={selectedDest}
                            onChange={(e) => setSelectedDest(e.target.value)}
                        >
                            <option value="">-- Chọn vị trí nhận hàng --</option>
                            {destinations.map(dest => (
                                <option key={dest.pointId} value={dest.pointId}>
                                    {dest.label} ({dest.pointId})
                                </option>
                            ))}
                        </select>
                        <input
                            className="form-input"
                            placeholder="Ghi chú (tuỳ chọn)"
                            value={note}
                            onChange={(e) => setNote(e.target.value)}
                        />
                        <button
                            className="btn btn-primary"
                            onClick={handleOrder}
                            disabled={loading}
                        >
                            {loading ? '⏳ Đang gửi...' : '🚀 Đặt hàng'}
                        </button>
                    </div>
                </div>
            )}
        </div>
    );
}

export default CustomerPage;
