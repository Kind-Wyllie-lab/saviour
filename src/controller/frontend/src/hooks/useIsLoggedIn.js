import { useState, useEffect } from "react";
import { isLoggedIn, onAuthChange } from "/src/auth";

/**
 * Reactive login state — re-renders on login/logout so guest-only controls
 * (mutating/destructive actions the backend gates via _require_auth) can be
 * disabled up front instead of failing after the fact.
 */
export default function useIsLoggedIn() {
  const [loggedIn, setLoggedIn] = useState(() => isLoggedIn());
  useEffect(() => onAuthChange(() => setLoggedIn(isLoggedIn())), []);
  return loggedIn;
}
