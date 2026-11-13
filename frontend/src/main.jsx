import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import Landing from './Landing'
import Dashboard from './Dashboard'
import './index.css'
import './editor-theme.css'
import './editor-info.css'
import './start.css'
import './dashboard.css'
import './plans.css'

// The landing page lives at "/", the dashboard at "/dashboard", the editor everywhere else (e.g. "/edit").
const path = window.location.pathname.replace(/\/+$/, '')

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    {path === '' ? <Landing /> : path === '/dashboard' ? <Dashboard /> : <App />}
  </React.StrictMode>
)
