import React from 'react';
import ReactDOM from 'react-dom/client';
import './index.css';
import APAApp from './apa/APAApp';
import { BrowserRouter } from "react-router-dom";

const root = ReactDOM.createRoot(document.getElementById('root'));
root.render(
  <React.StrictMode>
    <BrowserRouter>
      <APAApp /> {/*Change this to reflect which controller frontend is being used.*/}
    </BrowserRouter>
  </React.StrictMode>
);