import React from 'react';

function Cart({ items, total, onRemoveItem }) {
    if (items.length === 0) return null;

    return (
        <div className="cart-section">
            <h3 className="cart-title">🛒 Giỏ hàng</h3>
            {items.map((item, idx) => (
                <div key={idx} className="cart-item">
                    <span>
                        {item.image} {item.name} x{item.quantity}
                    </span>
                    <span>{(item.price * item.quantity).toLocaleString('vi-VN')}đ</span>
                </div>
            ))}
            <div className="cart-total">
                Tổng: {total.toLocaleString('vi-VN')}đ
            </div>
        </div>
    );
}

export default Cart;
