import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import { TruckIcon } from '@heroicons/react/24/solid';

export default function LoginPage() {
    const [isRegister, setIsRegister] = useState(false);
    const [username, setUsername] = useState('');
    const [password, setPassword] = useState('');
    const [displayName, setDisplayName] = useState('');
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(false);
    const { login, register } = useAuth();
    const navigate = useNavigate();

    const handleSubmit = async (e) => {
        e.preventDefault();
        setError('');
        setLoading(true);
        try {
            let u;
            if (isRegister) {
                u = await register({ username, password, displayName, role: 'customer' });
            } else {
                u = await login(username, password);
            }
            // Redirect based on role
            if (u.role === 'admin') navigate('/admin');
            else if (u.role === 'staff') navigate('/staff/orders');
            else navigate('/');
        } catch (err) {
            setError(err.response?.data?.message || 'Đã có lỗi xảy ra');
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="min-h-screen bg-gradient-to-br from-amber-50 via-white to-orange-50 flex items-center justify-center px-4">
            <div className="max-w-md w-full">
                {/* Logo */}
                <div className="text-center mb-8">
                    <div className="inline-flex items-center justify-center w-16 h-16 bg-amber-500 rounded-2xl shadow-lg mb-4">
                        <TruckIcon className="w-9 h-9 text-white" />
                    </div>
                    <h1 className="text-3xl font-bold text-gray-900">DeliverBot</h1>
                    <p className="text-gray-500 mt-1">Hệ thống giao hàng tự hành</p>
                </div>

                {/* Card */}
                <div className="bg-white rounded-2xl shadow-xl border border-gray-100 p-8">
                    <h2 className="text-xl font-semibold text-gray-800 mb-6">
                        {isRegister ? 'Đăng ký tài khoản' : 'Đăng nhập'}
                    </h2>

                    {error && (
                        <div className="mb-4 p-3 bg-red-50 border border-red-200 text-red-700 rounded-lg text-sm">
                            {error}
                        </div>
                    )}

                    <form onSubmit={handleSubmit} className="space-y-4">
                        {isRegister && (
                            <div>
                                <label className="block text-sm font-medium text-gray-700 mb-1">Tên hiển thị</label>
                                <input
                                    type="text"
                                    value={displayName}
                                    onChange={(e) => setDisplayName(e.target.value)}
                                    className="w-full rounded-lg border-gray-300 shadow-sm focus:border-amber-500 focus:ring-amber-500"
                                    placeholder="Nhập tên hiển thị"
                                    required
                                />
                            </div>
                        )}
                        <div>
                            <label className="block text-sm font-medium text-gray-700 mb-1">Tên đăng nhập</label>
                            <input
                                type="text"
                                value={username}
                                onChange={(e) => setUsername(e.target.value)}
                                className="w-full rounded-lg border-gray-300 shadow-sm focus:border-amber-500 focus:ring-amber-500"
                                placeholder="username"
                                required
                            />
                        </div>
                        <div>
                            <label className="block text-sm font-medium text-gray-700 mb-1">Mật khẩu</label>
                            <input
                                type="password"
                                value={password}
                                onChange={(e) => setPassword(e.target.value)}
                                className="w-full rounded-lg border-gray-300 shadow-sm focus:border-amber-500 focus:ring-amber-500"
                                placeholder="••••••••"
                                required
                            />
                        </div>
                        <button
                            type="submit"
                            disabled={loading}
                            className="w-full bg-amber-500 hover:bg-amber-600 text-white font-semibold py-2.5 rounded-lg transition-colors disabled:opacity-50"
                        >
                            {loading ? 'Đang xử lý...' : isRegister ? 'Đăng ký' : 'Đăng nhập'}
                        </button>
                    </form>

                    <div className="mt-6 text-center">
                        <button
                            onClick={() => { setIsRegister(!isRegister); setError(''); }}
                            className="text-sm text-amber-600 hover:text-amber-700 font-medium"
                        >
                            {isRegister ? 'Đã có tài khoản? Đăng nhập' : 'Chưa có tài khoản? Đăng ký'}
                        </button>
                    </div>
                </div>

                {/* Quick login hints */}
                <div className="mt-6 bg-white/60 backdrop-blur-sm rounded-xl p-4 border border-gray-100">
                    <p className="text-xs font-medium text-gray-500 mb-2">Tài khoản demo:</p>
                    <div className="grid grid-cols-3 gap-2 text-xs">
                        <button onClick={() => { setUsername('customer1'); setPassword('1234'); setIsRegister(false); }}
                            className="px-2 py-1.5 bg-green-50 text-green-700 rounded-lg hover:bg-green-100 transition-colors">
                            Customer
                        </button>
                        <button onClick={() => { setUsername('staff1'); setPassword('1234'); setIsRegister(false); }}
                            className="px-2 py-1.5 bg-blue-50 text-blue-700 rounded-lg hover:bg-blue-100 transition-colors">
                            Staff
                        </button>
                        <button onClick={() => { setUsername('admin'); setPassword('admin'); setIsRegister(false); }}
                            className="px-2 py-1.5 bg-purple-50 text-purple-700 rounded-lg hover:bg-purple-100 transition-colors">
                            Admin
                        </button>
                    </div>
                </div>
            </div>
        </div>
    );
}
