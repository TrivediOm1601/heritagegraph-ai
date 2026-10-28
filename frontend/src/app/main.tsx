import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import App from './App';
import '../styles/index.css';

const rootElement = document.getElementById('root');

if (!rootElement) {
  throw new Error('HeritageGraph AI could not start: #root element is missing.');
}

createRoot(rootElement).render(
  <StrictMode>
    <App />
  </StrictMode>,
);