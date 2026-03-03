import React, { useState, useEffect } from 'react';
import { getProducts, getDestinations, createOrder } from '../services/api';
import { useAuth } from '../contexts/AuthContext';
import { ShoppingCartIcon, MapPinIcon, PlusIcon, MinusIcon, PaperAirplaneIcon, CheckCircleIcon } from '@heroicons/react/24/outline';

export default function CustomerOrderPage() {
    const { user } = useAuth();
    const [products, setProducts] = useState([]);
    const [cart, setCart] = useState({});
    const [destinations, setDestinations] = useState([]);
    const [selectedDest, setSelectedDest] = useState('');
    const [note, setNote] = useState('');
    const [loading, setLoading] = useState(false);
    const [success, setSuccess] = useState(null);
    const [error, setError] = useState('');

    useEffect(() => {
        Promise.all([getProducts(), getDestinations()])
            .then(([p, d]) => {
                setProducts(p.data.data);
                setDestinations(d.data.data);
            })
            .catch(console.error);
    }, []);

    const addToCart = (product) => {
        setCart((prev) => ({
            ...prev,
            [product._id]: {
                product: product._id,
                name: product.name,
                price: product.price,
                quantity: (prev[product._id]?.quantity || 0) + 1,
            },
        }));
    };

    const removeFromCart = (id) => {
        setCart((prev) => {
            const next = { ...prev };
            if (next[id]) {
                next[id] = { ...next[id], quantity: next[id].quantity - 1 };
                if (next[id].quantity <= 0) delete next[id];
            }
            return next;
        });
    };

    const cartItems = Object.values(cart);
    const total = cartItems.reduce((s, i) => s + i.price * i.quantity, 0);

    const handleOrder = async () => {
        if (!cartItems.length) return setError('Vui lòng chọn sản phẩm');
        if (!selectedDest) return setError('Vui lòng chọn vị trí giao hàng');
        setError('');
        setLoading(true);
        try {
            const res = await createOrder({ items: cartItems, destinationPoint: selectedDest, note });
            setSuccess(res.data.data);
            setCart({});
            setNote('');
            setSelectedDest('');
        } catch (err) {
            setError(err.response?.data?.message || 'Lỗi đặt hàng');
        } finally {
            setLoading(false);
        }
    };

    const categories = [
        { key: 'drink', label: 'Đồ uống' },
        { key: 'food', label: 'Đồ ăn' },
        { key: 'other', label: 'Khác' },
    ];

    return (
        <div className="max-w-5xl mx-auto">
            <div className="mb-6">
                <h1 className="text-2xl font-bold text-gray-900">Đặt hàng</h1>
                <p className="text-gray-500 mt-1">Chào {user?.displayName || 'bạn'}, chọn sản phẩm và vị trí giao hàng</p>
            </div>

            {/* Success Modal */}
            {success && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={() => setSuccess(null)}>
                    <div className="bg-white rounded-2xl shadow-2xl p-8 max-w-sm w-full mx-4 text-center" onClick={(e) => e.stopPropagation()}>
                        <CheckCircleIcon className="w-16 h-16 text-green-500 mx-auto mb-4" />
                        <h3 className="text-xl font-bold text-gray-900 mb-2">Đặt hàng thành công!</h3>
                        <p className="text-gray-500 text-sm mb-1">Mã đơn: #{success._id?.slice(-6).toUpperCase()}</p>
                        <p className="text-gray-500 text-sm mb-1">Giao đến: {success.destinationPoint}</p>
                        <p className="text-amber-600 font-semibold">{success.totalPrice?.toLocaleString('vi-VN')}đ</p>
                        <p className="text-xs text-gray-400 mt-3">Vui lòng chờ staff xác nhận đơn hàng.</p>
                        <button onClick={() => setSuccess(null)} className="mt-4 px-6 py-2 bg-amber-500 text-white rounded-lg hover:bg-amber-600 transition-colors font-medium">
                            Đóng
                        </button>
                    </div>
                </div>
            )}

            {error && (
                <div className="mb-4 p-3 bg-red-50 border border-red-200 text-red-700 rounded-lg text-sm">
                    {error}
                </div>
            )}

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                {/* Products */}
                <div className="lg:col-span-2 space-y-6">
                    {categories.map((cat) => {
                        const items = products.filter((p) => p.category === cat.key && p.inStock);
                        if (!items.length) return null;
                        return (
                            <div key={cat.key}>
                                <h2 className="text-lg font-semibold text-gray-800 mb-3">{cat.label}</h2>
                                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                                    {items.map((p) => {
                                        const qty = cart[p._id]?.quantity || 0;
                                        return (
                                            <div key={p._id} className="bg-white rounded-xl border border-gray-200 p-4 flex items-center gap-4 hover:shadow-sm transition-shadow">
                                                <div className="text-3xl w-12 text-center">{p.image}</div>
                                                <div className="flex-1 min-w-0">
                                                    <h3 className="font-medium text-gray-900 text-sm">{p.name}</h3>
                                                    <p className="text-xs text-gray-400">{p.description}</p>
                                                    <p className="text-amber-600 font-semibold text-sm mt-1">{p.price.toLocaleString('vi-VN')}đ</p>
                                                </div>
                                                <div className="flex items-center gap-2">
                                                    {qty > 0 && (
                                                        <>
                                                            <button onClick={() => removeFromCart(p._id)} className="w-7 h-7 rounded-full bg-gray-100 hover:bg-gray-200 flex items-center justify-center transition-colors">
                                                                <MinusIcon className="w-4 h-4 text-gray-600" />
                                                            </button>
                                                            <span className="w-6 text-center text-sm font-semibold">{qty}</span>
                                                        </>
                                                    )}
                                                    <button onClick={() => addToCart(p)} className="w-7 h-7 rounded-full bg-amber-100 hover:bg-amber-200 flex items-center justify-center transition-colors">
                                                        <PlusIcon className="w-4 h-4 text-amber-700" />
                                                    </button>
                                                </div>
                                            </div>
                                        );
                                    })}
                                </div>
                            </div>
                        );
                    })}
                </div>

                {/* Cart sidebar */}
                <div className="lg:col-span-1">
                    <div className="bg-white rounded-xl border border-gray-200 p-5 sticky top-24">
                        <div className="flex items-center gap-2 mb-4">
                            <ShoppingCartIcon className="w-5 h-5 text-amber-500" />
                            <h3 className="font-semibold text-gray-900">Giỏ hàng</h3>
                            {cartItems.length > 0 && (
                                <span className="ml-auto text-xs bg-amber-100 text-amber-700 px-2 py-0.5 rounded-full font-medium">
                                    {cartItems.reduce((s, i) => s + i.quantity, 0)} sản phẩm
                                </span>
                            )}
                        </div>

                        {cartItems.length === 0 ? (
                            <p className="text-gray-400 text-sm text-center py-6">Chưa chọn sản phẩm nào</p>
                        ) : (
                            <div className="space-y-2 mb-4">
                                {cartItems.map((item) => (
                                    <div key={item.product} className="flex justify-between text-sm">
                                        <span className="text-gray-700">{item.name} x{item.quantity}</span>
                                        <span className="text-gray-900 font-medium">{(item.price * item.quantity).toLocaleString('vi-VN')}đ</span>
                                    </div>
                                ))}
                                <div className="border-t pt-2 flex justify-between font-semibold">
                                    <span>Tổng</span>
                                    <span className="text-amber-600">{total.toLocaleString('vi-VN')}đ</span>
                                </div>
                            </div>
                        )}

                        {/* Destination */}
                        <div className="mb-3">
                            <label className="flex items-center gap-1.5 text-sm font-medium text-gray-700 mb-1">
                                <MapPinIcon className="w-4 h-4 text-gray-400" />
                                Vị trí giao hàng
                            </label>
                            <select
                                value={selectedDest}
                                onChange={(e) => setSelectedDest(e.target.value)}
                                className="w-full rounded-lg border-gray-300 text-sm focus:border-amber-500 focus:ring-amber-500"
                            >
                                <option value="">-- Chọn vị trí --</option>
                                {destinations.map((d) => (
                                    <option key={d.pointId} value={d.pointId}>
                                        {d.label} ({d.pointId})
                                    </option>
                                ))}
                            </select>
                        </div>

                        {/* Note */}
                        <div className="mb-4">
                            <input
                                type="text"
                                placeholder="Ghi chú (tuỳ chọn)"
                                value={note}
                                onChange={(e) => setNote(e.target.value)}
                                className="w-full rounded-lg border-gray-300 text-sm focus:border-amber-500 focus:ring-amber-500"
                            />
                        </div>

                        <button
                            onClick={handleOrder}
                            disabled={loading || !cartItems.length}
                            className="w-full flex items-center justify-center gap-2 bg-amber-500 hover:bg-amber-600 disabled:bg-gray-300 text-white font-semibold py-2.5 rounded-lg transition-colors"
                        >
                            <PaperAirplaneIcon className="w-5 h-5" />
                            {loading ? 'Đang gửi...' : 'Đặt hàng'}
                        </button>
                    </div>
                </div>
            </div>
        </div>
    );
}
