import React from 'react';
import ReactDOM from 'react-dom/client';
import './index.css';
import App from './apa/App';
import { BrowserRouter } from "react-router-dom";

const root = ReactDOM.createRoot(document.getElementById('root'));
root.render(
  <React.StrictMode>
    <BrowserRouter>
      <App /> {/*Change this to reflect which controller frontend is being used.*/}
    </BrowserRouter>
  </React.StrictMode>
);
