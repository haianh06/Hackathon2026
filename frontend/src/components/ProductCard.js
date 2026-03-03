import React from 'react';

function ProductCard({ product, quantity, onAdd, onRemove }) {
    return (
        <div className="product-card">
            <div className="product-emoji">{product.image}</div>
            <div className="product-name">{product.name}</div>
            <div className="product-desc">{product.description}</div>
            <div className="product-price">
                {product.price.toLocaleString('vi-VN')}đ
            </div>
            <div className="product-actions">
                <button className="qty-btn" onClick={() => onRemove(product._id)} disabled={!quantity}>
                    −
                </button>
                <span className="qty-display">{quantity || 0}</span>
                <button className="qty-btn" onClick={() => onAdd(product)}>
                    +
                </button>
            </div>
        </div>
    );
}

export default ProductCard;
