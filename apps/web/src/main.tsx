import { createRoot } from 'react-dom/client';
import { StrictMode } from 'react';
import './styles.css';
import { App, createDefaultStore } from './App';

createRoot(document.getElementById('root') as HTMLElement).render(
  <StrictMode>
    <App store={createDefaultStore()} />
  </StrictMode>,
);
