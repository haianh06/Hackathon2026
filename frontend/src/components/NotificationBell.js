import React, { useState, useEffect, useCallback, useRef } from 'react';
import { BellIcon, CheckIcon, XMarkIcon } from '@heroicons/react/24/outline';
import { BellAlertIcon } from '@heroicons/react/24/solid';
import { getNotifications, getUnreadNotificationCount, markNotificationRead, markAllNotificationsRead } from '../services/api';
import socket from '../services/socket';

const typeConfig = {
    order_confirmed: { emoji: '✅', color: 'text-blue-600' },
    order_delivering: { emoji: '🚚', color: 'text-amber-600' },
    order_arrived: { emoji: '📍', color: 'text-green-600' },
    order_delivered: { emoji: '🎉', color: 'text-green-700' },
    order_cancelled: { emoji: '❌', color: 'text-red-600' },
    system: { emoji: '🔔', color: 'text-gray-600' },
};

export default function NotificationBell() {
    const [open, setOpen] = useState(false);
    const [notifications, setNotifications] = useState([]);
    const [unreadCount, setUnreadCount] = useState(0);
    const [loading, setLoading] = useState(false);
    const panelRef = useRef(null);

    const loadUnreadCount = useCallback(async () => {
        try {
            const res = await getUnreadNotificationCount();
            setUnreadCount(res.data.data.count);
        } catch (e) { /* ignore */ }
    }, []);

    const loadNotifications = useCallback(async () => {
        setLoading(true);
        try {
            const res = await getNotifications({ limit: 20 });
            setNotifications(res.data.data || []);
        } catch (e) { /* ignore */ }
        finally { setLoading(false); }
    }, []);

    useEffect(() => {
        loadUnreadCount();
        const interval = setInterval(loadUnreadCount, 30000);

        const handleNewNotif = (notif) => {
            setNotifications(prev => [notif, ...prev].slice(0, 20));
            setUnreadCount(prev => prev + 1);
        };
        socket.on('new-notification', handleNewNotif);

        return () => {
            clearInterval(interval);
            socket.off('new-notification', handleNewNotif);
        };
    }, [loadUnreadCount]);

    // Close on outside click
    useEffect(() => {
        const handleClickOutside = (e) => {
            if (panelRef.current && !panelRef.current.contains(e.target)) {
                setOpen(false);
            }
        };
        if (open) {
            document.addEventListener('mousedown', handleClickOutside);
        }
        return () => document.removeEventListener('mousedown', handleClickOutside);
    }, [open]);

    const togglePanel = () => {
        if (!open) {
            loadNotifications();
        }
        setOpen(!open);
    };

    const handleMarkRead = async (id) => {
        try {
            await markNotificationRead(id);
            setNotifications(prev => prev.map(n => n._id === id ? { ...n, read: true } : n));
            setUnreadCount(prev => Math.max(0, prev - 1));
        } catch (e) { /* ignore */ }
    };

    const handleMarkAllRead = async () => {
        try {
            await markAllNotificationsRead();
            setNotifications(prev => prev.map(n => ({ ...n, read: true })));
            setUnreadCount(0);
        } catch (e) { /* ignore */ }
    };

    return (
        <div className="relative" ref={panelRef}>
            <button
                onClick={togglePanel}
                className="relative p-2 rounded-lg hover:bg-gray-100 transition-colors"
            >
                {unreadCount > 0 ? (
                    <BellAlertIcon className="w-5 h-5 text-amber-500 animate-bounce" />
                ) : (
                    <BellIcon className="w-5 h-5 text-gray-500" />
                )}
                {unreadCount > 0 && (
                    <span className="absolute -top-0.5 -right-0.5 bg-red-500 text-white text-[10px] font-bold rounded-full w-4.5 h-4.5 flex items-center justify-center min-w-[18px] px-1">
                        {unreadCount > 9 ? '9+' : unreadCount}
                    </span>
                )}
            </button>

            {open && (
                <div className="absolute right-0 top-full mt-2 w-80 bg-white rounded-xl shadow-2xl border border-gray-200 z-50 overflow-hidden">
                    <div className="flex items-center justify-between px-4 py-3 border-b bg-gray-50">
                        <h3 className="text-sm font-bold text-gray-700">Thông báo</h3>
                        <div className="flex items-center gap-2">
                            {unreadCount > 0 && (
                                <button
                                    onClick={handleMarkAllRead}
                                    className="text-xs text-blue-500 hover:text-blue-700 flex items-center gap-0.5"
                                >
                                    <CheckIcon className="w-3 h-3" /> Đọc tất cả
                                </button>
                            )}
                            <button onClick={() => setOpen(false)} className="text-gray-400 hover:text-gray-600">
                                <XMarkIcon className="w-4 h-4" />
                            </button>
                        </div>
                    </div>
                    <div className="max-h-80 overflow-y-auto divide-y divide-gray-50">
                        {loading ? (
                            <div className="flex justify-center py-6">
                                <div className="w-5 h-5 border-2 border-amber-500 border-t-transparent rounded-full animate-spin" />
                            </div>
                        ) : notifications.length === 0 ? (
                            <div className="text-center py-8 text-sm text-gray-400">
                                Chưa có thông báo
                            </div>
                        ) : (
                            notifications.map((notif) => {
                                const cfg = typeConfig[notif.type] || typeConfig.system;
                                return (
                                    <div
                                        key={notif._id}
                                        className={`px-4 py-3 hover:bg-gray-50 transition cursor-pointer ${!notif.read ? 'bg-blue-50/50' : ''}`}
                                        onClick={() => !notif.read && handleMarkRead(notif._id)}
                                    >
                                        <div className="flex items-start gap-2.5">
                                            <span className="text-lg mt-0.5">{cfg.emoji}</span>
                                            <div className="flex-1 min-w-0">
                                                <p className={`text-sm font-semibold ${cfg.color}`}>{notif.title}</p>
                                                <p className="text-xs text-gray-500 mt-0.5 line-clamp-2">{notif.message}</p>
                                                <p className="text-[10px] text-gray-300 mt-1">
                                                    {new Date(notif.createdAt).toLocaleString('vi-VN')}
                                                </p>
                                            </div>
                                            {!notif.read && (
                                                <span className="w-2 h-2 bg-blue-500 rounded-full mt-1.5 flex-shrink-0" />
                                            )}
                                        </div>
                                    </div>
                                );
                            })
                        )}
                    </div>
                </div>
            )}
        </div>
    );
}
