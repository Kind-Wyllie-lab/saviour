import App from 'virtual:active-app';
import useControllerTheme from './hooks/useControllerTheme';

// Applies controller-configured theme overrides (e.g. accent color, set via
// the Frontend tab in Controller Settings) regardless of which variant is
// active — mounted once from main.jsx rather than duplicated into all five
// App.jsx. Kept in its own file (rather than defined inline in main.jsx) so
// the entry point only bootstraps and doesn't break Vite's fast-refresh
// boundary, which expects a component file to only export components.
export default function ThemedApp() {
  useControllerTheme();
  return <App />;
}
