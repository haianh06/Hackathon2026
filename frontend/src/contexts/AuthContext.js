import React, { createContext, useContext, useState, useEffect } from 'react';
import { login as apiLogin, register as apiRegister, getMe } from '../services/api';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
    const [user, setUser] = useState(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        const token = localStorage.getItem('token');
        if (token) {
            getMe()
                .then((res) => setUser(res.data.data))
                .catch(() => {
                    localStorage.removeItem('token');
                    localStorage.removeItem('user');
                })
                .finally(() => setLoading(false));
        } else {
            setLoading(false);
        }
    }, []);

    const login = async (username, password) => {
        const res = await apiLogin(username, password);
        const { user: u, token } = res.data.data;
        localStorage.setItem('token', token);
        localStorage.setItem('user', JSON.stringify(u));
        setUser(u);
        return u;
    };

    const register = async (data) => {
        const res = await apiRegister(data);
        const { user: u, token } = res.data.data;
        localStorage.setItem('token', token);
        localStorage.setItem('user', JSON.stringify(u));
        setUser(u);
        return u;
    };

    const logout = () => {
        localStorage.removeItem('token');
        localStorage.removeItem('user');
        setUser(null);
    };

    return (
        <AuthContext.Provider value={{ user, loading, login, register, logout }}>
            {children}
        </AuthContext.Provider>
    );
}

export function useAuth() {
    return useContext(AuthContext);
}
