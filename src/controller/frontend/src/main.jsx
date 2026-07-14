import React from 'react';
import ReactDOM from 'react-dom/client';
import './index.css';
import App from './loom/App';
import AuthGate from './basic/components/AuthGate/AuthGate';
import ChangePasswordModal from './basic/components/ChangePasswordModal/ChangePasswordModal';
import { BrowserRouter } from "react-router-dom";

const root = ReactDOM.createRoot(document.getElementById('root'));
root.render(
  <React.StrictMode>
    <BrowserRouter>
      <AuthGate />
      <ChangePasswordModal />
      <App />
    </BrowserRouter>
  </React.StrictMode>
);
