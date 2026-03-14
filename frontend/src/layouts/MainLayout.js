import React, { useState } from 'react';
import { Link, useLocation, Outlet, Navigate } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import NotificationBell from '../components/NotificationBell';
import {
    TruckIcon,
    ShoppingCartIcon,
    ClipboardDocumentListIcon,
    Cog6ToothIcon,
    MapIcon,
    ChartBarIcon,
    ArrowRightOnRectangleIcon,
    Bars3Icon,
    XMarkIcon,
    UserCircleIcon,
    SignalIcon,
    WrenchScrewdriverIcon,
} from '@heroicons/react/24/outline';
import { TruckIcon as TruckSolid } from '@heroicons/react/24/solid';

const navConfig = {
    customer: [
        { to: '/', icon: ShoppingCartIcon, label: 'Đặt hàng' },
        { to: '/my-orders', icon: ClipboardDocumentListIcon, label: 'Đơn của tôi' },
    ],
    staff: [
        { to: '/staff/orders', icon: ClipboardDocumentListIcon, label: 'Đơn hàng' },
        { to: '/staff/control', icon: Cog6ToothIcon, label: 'Điều khiển & Camera' },
    ],
    admin: [
        { to: '/admin', icon: ChartBarIcon, label: 'Dashboard' },
        { to: '/admin/logs', icon: ClipboardDocumentListIcon, label: 'Delivery Logs' },
        { to: '/admin/map-builder', icon: MapIcon, label: 'Map Builder' },
        { to: '/admin/rfid', icon: SignalIcon, label: 'Quản lý RFID' },
        { to: '/admin/motor-calibration', icon: WrenchScrewdriverIcon, label: 'Đo động cơ' },
        { to: '/map', icon: TruckIcon, label: 'Bản đồ & Xe' },
    ],
};

// Dev links removed — merged into Map Builder page

export default function MainLayout() {
    const { user, logout } = useAuth();
    const location = useLocation();
    const [sidebarOpen, setSidebarOpen] = useState(false);

    if (!user) return <Navigate to="/login" replace />;

    const role = user.role || 'customer';
    const links = navConfig[role] || navConfig.customer;


    const roleLabel = { customer: 'Customer', staff: 'Staff', admin: 'Admin' }[role];
    const roleBadgeColor = { customer: 'bg-green-100 text-green-700', staff: 'bg-blue-100 text-blue-700', admin: 'bg-purple-100 text-purple-700' }[role];

    return (
        <div className="min-h-screen bg-gray-50 flex">
            {/* Mobile overlay */}
            {sidebarOpen && (
                <div className="fixed inset-0 z-40 bg-black/30 lg:hidden" onClick={() => setSidebarOpen(false)} />
            )}

            {/* Sidebar */}
            <aside className={`
                fixed inset-y-0 left-0 z-50 w-64 bg-white border-r border-gray-200 flex flex-col
                transform transition-transform duration-200 ease-in-out
                lg:translate-x-0 lg:static lg:z-auto
                ${sidebarOpen ? 'translate-x-0' : '-translate-x-full'}
            `}>
                {/* Brand */}
                <div className="h-16 flex items-center px-6 border-b border-gray-100">
                    <TruckSolid className="w-8 h-8 text-amber-500 mr-3" />
                    <span className="text-lg font-bold text-gray-900">DeliverBot</span>
                    <button className="ml-auto lg:hidden" onClick={() => setSidebarOpen(false)}>
                        <XMarkIcon className="w-5 h-5 text-gray-400" />
                    </button>
                </div>

                {/* Nav */}
                <nav className="flex-1 px-3 py-4 space-y-1 overflow-y-auto">
                    <p className="px-3 text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">{roleLabel}</p>
                    {links.map((item) => {
                        const active = location.pathname === item.to;
                        return (
                            <Link
                                key={item.to}
                                to={item.to}
                                onClick={() => setSidebarOpen(false)}
                                className={`flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors ${active
                                    ? 'bg-amber-50 text-amber-700'
                                    : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900'
                                    }`}
                            >
                                <item.icon className={`w-5 h-5 ${active ? 'text-amber-500' : 'text-gray-400'}`} />
                                {item.label}
                            </Link>
                        );
                    })}


                </nav>

                {/* User info */}
                <div className="p-4 border-t border-gray-100">
                    <div className="flex items-center gap-3">
                        <UserCircleIcon className="w-9 h-9 text-gray-300" />
                        <div className="flex-1 min-w-0">
                            <p className="text-sm font-medium text-gray-900 truncate">{user.displayName}</p>
                            <span className={`inline-block text-xs px-2 py-0.5 rounded-full font-medium ${roleBadgeColor}`}>
                                {roleLabel}
                            </span>
                        </div>
                        <button onClick={logout} className="p-1.5 text-gray-400 hover:text-red-500 transition-colors" title="Đăng xuất">
                            <ArrowRightOnRectangleIcon className="w-5 h-5" />
                        </button>
                    </div>
                </div>
            </aside>

            {/* Main content */}
            <div className="flex-1 flex flex-col min-w-0">
                {/* Top bar (mobile) */}
                <header className="h-16 bg-white border-b border-gray-200 flex items-center px-4 lg:px-8 sticky top-0 z-30">
                    <button className="lg:hidden mr-4" onClick={() => setSidebarOpen(true)}>
                        <Bars3Icon className="w-6 h-6 text-gray-600" />
                    </button>
                    <div className="flex items-center gap-2 lg:hidden">
                        <TruckSolid className="w-6 h-6 text-amber-500" />
                        <span className="font-bold text-gray-900">DeliverBot</span>
                    </div>
                    <div className="flex-1" />
                    <div className="flex items-center gap-3">
                        <NotificationBell />
                        <span className="hidden lg:inline text-sm text-gray-500">{user.displayName}</span>
                        <span className={`hidden lg:inline text-xs px-2 py-0.5 rounded-full font-medium ${roleBadgeColor}`}>
                            {roleLabel}
                        </span>
                    </div>
                </header>

                {/* Page content */}
                <main className="flex-1 p-4 lg:p-8 overflow-y-auto">
                    <Outlet />
                </main>
            </div>
        </div>
    );
}
