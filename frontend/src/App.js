import React from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider, useAuth } from './contexts/AuthContext';
import MainLayout from './layouts/MainLayout';
import LoginPage from './pages/LoginPage';
import CustomerOrderPage from './pages/CustomerOrderPage';
import MyOrdersPage from './pages/MyOrdersPage';
import StaffOrdersPage from './pages/StaffOrdersPage';
import StaffControlPage from './pages/StaffControlPage';
import StaffCameraPage from './pages/StaffCameraPage';
import AdminDashboard from './pages/AdminDashboard';
import AdminLogsPage from './pages/AdminLogsPage';
import MapPage from './pages/MapPage';
import DevPage from './pages/DevPage';

function ProtectedRoute({ children, roles }) {
    const { user, loading } = useAuth();
    if (loading) return <div className="flex items-center justify-center h-screen"><div className="w-8 h-8 border-4 border-amber-500 border-t-transparent rounded-full animate-spin" /></div>;
    if (!user) return <Navigate to="/login" replace />;
    if (roles && !roles.includes(user.role)) return <Navigate to="/" replace />;
    return children;
}

function AppRoutes() {
    const { user, loading } = useAuth();
    if (loading) return <div className="flex items-center justify-center h-screen"><div className="w-8 h-8 border-4 border-amber-500 border-t-transparent rounded-full animate-spin" /></div>;

    return (
        <Routes>
            <Route path="/login" element={user ? <Navigate to="/" replace /> : <LoginPage />} />
            <Route element={<ProtectedRoute><MainLayout /></ProtectedRoute>}>
                {/* Customer */}
                <Route path="/" element={<CustomerOrderPage />} />
                <Route path="/my-orders" element={<MyOrdersPage />} />
                {/* Staff */}
                <Route path="/staff/orders" element={<ProtectedRoute roles={['staff', 'admin']}><StaffOrdersPage /></ProtectedRoute>} />
                <Route path="/staff/control" element={<ProtectedRoute roles={['staff', 'admin']}><StaffControlPage /></ProtectedRoute>} />
                <Route path="/staff/camera" element={<ProtectedRoute roles={['staff', 'admin']}><StaffCameraPage /></ProtectedRoute>} />
                {/* Admin */}
                <Route path="/admin" element={<ProtectedRoute roles={['admin']}><AdminDashboard /></ProtectedRoute>} />
                <Route path="/admin/logs" element={<ProtectedRoute roles={['admin']}><AdminLogsPage /></ProtectedRoute>} />
                {/* Dev */}
                <Route path="/map" element={<ProtectedRoute roles={['staff', 'admin']}><MapPage /></ProtectedRoute>} />
                <Route path="/dev" element={<ProtectedRoute roles={['staff', 'admin']}><DevPage /></ProtectedRoute>} />
            </Route>
            <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
    );
}

export default function App() {
    return (
        <Router>
            <AuthProvider>
                <AppRoutes />
            </AuthProvider>
        </Router>
    );
}
