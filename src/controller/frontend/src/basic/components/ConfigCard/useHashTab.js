import { useState, useEffect, useRef } from "react";

/**
 * Tracks a config card's active tab in the URL hash, alongside the selected
 * device id that Settings.jsx already puts there (`#<deviceId>/<tab>`) — so
 * refreshing the page, or sharing the link, returns to the same tab and not
 * just the same device. Settings.jsx also carries the tab suffix across a
 * device switch, so different card types can see a tab that isn't one of
 * theirs — pass `validKeys` and this falls back to `defaultTab` for those
 * (without rewriting the hash, so switching back to a card that *does* have
 * that tab still lands on it).
 */
function readTabFromHash() {
  const raw = window.location.hash.slice(1);
  const slash = raw.indexOf("/");
  return slash === -1 ? null : raw.slice(slash + 1);
}

export function useHashTab(defaultTab, validKeys = null) {
  const resolve = (tab) => {
    if (!tab) return defaultTab;
    if (validKeys && !validKeys.includes(tab)) return defaultTab;
    return tab;
  };

  const [activeTab, setActiveTabState] = useState(() => resolve(readTabFromHash()));
  const activeTabRef = useRef(activeTab);
  activeTabRef.current = activeTab;

  // Browser back/forward can change the hash without going through
  // setActiveTab below — stay in sync the same way Settings.jsx does for
  // the device id.
  useEffect(() => {
    const onHashChange = () => {
      const tab = resolve(readTabFromHash());
      if (tab !== activeTabRef.current) setActiveTabState(tab);
    };
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const setActiveTab = (tab) => {
    setActiveTabState(tab);
    const raw = window.location.hash.slice(1);
    const slash = raw.indexOf("/");
    const id = slash === -1 ? raw : raw.slice(0, slash);
    window.location.hash = `${id}/${tab}`;
  };

  return [activeTab, setActiveTab];
}
