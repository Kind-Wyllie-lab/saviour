import React from 'react';
import ReactDOM from 'react-dom/client';
import './index.css';
import ThemedApp from './ThemedApp';
import AuthGate from './basic/components/AuthGate/AuthGate';
import ChangePasswordModal from './basic/components/ChangePasswordModal/ChangePasswordModal';
import OfflineRunner from './basic/components/OfflineRunner/OfflineRunner';
import { BrowserRouter } from "react-router";

const root = ReactDOM.createRoot(document.getElementById('root'));
root.render(
  <React.StrictMode>
    <BrowserRouter>
      <AuthGate />
      <ChangePasswordModal />
      <ThemedApp />
      <OfflineRunner />
    </BrowserRouter>
  </React.StrictMode>
);
