import React from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider, useAuth } from './contexts/AuthContext';
import MainLayout from './layouts/MainLayout';
import LoginPage from './pages/LoginPage';
import CustomerOrderPage from './pages/CustomerOrderPage';
import MyOrdersPage from './pages/MyOrdersPage';
import StaffOrdersPage from './pages/StaffOrdersPage';
import StaffControlPage from './pages/StaffControlPage';
// StaffCameraPage merged into StaffControlPage
import AdminDashboard from './pages/AdminDashboard';
import AdminLogsPage from './pages/AdminLogsPage';
import MapBuilderPage from './pages/MapBuilderPage';
import MapPage from './pages/MapPage';
import RfidPage from './pages/RfidPage';

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
                {/* Admin */}
                <Route path="/admin" element={<ProtectedRoute roles={['admin']}><AdminDashboard /></ProtectedRoute>} />
                <Route path="/admin/logs" element={<ProtectedRoute roles={['admin']}><AdminLogsPage /></ProtectedRoute>} />
                {/* Map Builder: separate page */}
                <Route path="/admin/map-builder" element={<ProtectedRoute roles={['admin']}><MapBuilderPage /></ProtectedRoute>} />
                <Route path="/admin/rfid" element={<ProtectedRoute roles={['admin']}><RfidPage /></ProtectedRoute>} />
                {/* Map + Dev Debug: combined page */}
                <Route path="/map" element={<ProtectedRoute roles={['admin']}><MapPage /></ProtectedRoute>} />
                <Route path="/dev" element={<Navigate to="/map" replace />} />
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
