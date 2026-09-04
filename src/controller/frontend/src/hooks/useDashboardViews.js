import { useCallback, useEffect, useState } from "react";
import socket from "/src/socket";

/**
 * Controller-side "Saved Views" for the Dashboard canvas.
 *
 * A view is `{ id, name, group, widgets: [{id, type, target?}], layout }`,
 * persisted on the controller (one JSON file each) so every browser pointed
 * at it sees the same set of views. Writes are gated by _require_auth on the
 * server — a guest can read and switch views but a save/delete is rejected
 * with a `dashboard_view_error` (surfaced here as `error`).
 *
 * The server broadcasts the full list on every change, so all open
 * dashboards stay in sync without a manual refetch.
 */
export default function useDashboardViews() {
  const [views, setViews] = useState([]);
  const [defaultId, setDefaultId] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [lastSaved, setLastSaved] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    const onViews = (data) => {
      setViews(Array.isArray(data?.views) ? data.views : []);
      setDefaultId(data?.default_id || "");
      setLoaded(true);
    };
    const onSaved = (data) => {
      if (data?.view) setLastSaved({ ...data.view, _at: Date.now() });
    };
    const onError = (data) => setError(data?.error || "Could not save the view");
    socket.on("dashboard_views", onViews);
    socket.on("dashboard_view_saved", onSaved);
    socket.on("dashboard_view_error", onError);
    socket.emit("get_dashboard_views");
    return () => {
      socket.off("dashboard_views", onViews);
      socket.off("dashboard_view_saved", onSaved);
      socket.off("dashboard_view_error", onError);
    };
  }, []);

  const saveView = useCallback((view) => {
    setError(null);
    socket.emit("save_dashboard_view", view);
  }, []);
  const deleteView = useCallback((id) => {
    setError(null);
    socket.emit("delete_dashboard_view", { id });
  }, []);
  const setDefaultView = useCallback((id) => {
    setError(null);
    socket.emit("set_default_dashboard_view", { id });
  }, []);

  return {
    views,
    defaultId,
    loaded,
    lastSaved,
    error,
    clearError: () => setError(null),
    saveView,
    deleteView,
    setDefaultView,
  };
}
