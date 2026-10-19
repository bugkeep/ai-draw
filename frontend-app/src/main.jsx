import React, { lazy, Suspense } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import SettingsPage from './SettingsPage.jsx'
import './styles.css'

const App = lazy(() => import('./App.jsx'))

createRoot(document.getElementById('root')).render(
  <BrowserRouter><Suspense fallback={<div className="settings-loading">正在打开画布…</div>}>
    <Routes><Route path="/" element={<App />} /><Route path="/settings" element={<SettingsPage />} />
      <Route path="*" element={<Navigate to="/" replace />} /></Routes>
  </Suspense></BrowserRouter>,
)
