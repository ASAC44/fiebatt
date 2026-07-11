// polyfill crypto.randomUUID for non-secure contexts (HTTP)
if (typeof crypto !== 'undefined' && !crypto.randomUUID) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (crypto as any).randomUUID = () =>
    '10000000-1000-4000-8000-100000000000'.replace(/[018]/g, (c: string) =>
      (+c ^ (crypto.getRandomValues(new Uint8Array(1))[0] & (15 >> (+c / 4)))).toString(16)
    );
}

import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import App from './App.tsx';
import { EditorRoute } from './routes/editor.tsx';
import { ProjectsRoute } from './routes/projects.tsx';
import { AuthProvider } from './lib/useAuth';
import 'lenis/dist/lenis.css';
import './index.css';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<App />} />
          <Route path="/editor" element={<EditorRoute />} />
          <Route path="/editor/:projectId" element={<EditorRoute />} />
          <Route path="/projects" element={<ProjectsRoute />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  </StrictMode>,
);
