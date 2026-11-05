// Who else has this UI open: web.py broadcasts `clients_update` (one entry
// per browser tab: id, ip, via_proxy, since, admin) on every connect,
// disconnect, login and logout, and sends each tab its own id as
// `client_self`. Feeds the Sidebar's "N connected" indicator and
// confirmIfOtherAdmins(), the warning before a risky action while someone
// else is signed in.
import { useSyncExternalStore } from "react";
import socket from "/src/socket";

let state = { selfId: null, clients: [] };
const listeners = new Set();

function update(patch) {
  state = { ...state, ...patch };
  listeners.forEach((fn) => fn());
}

socket.on("client_self", (d) => update({ selfId: d?.id ?? null }));
socket.on("clients_update", (d) => update({ clients: d?.clients ?? [] }));
// The connect-time broadcast can land before a listener exists; ask again.
socket.on("connect", () => socket.emit("get_clients"));
if (socket.connected) socket.emit("get_clients");

function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function useClients() {
  return useSyncExternalStore(subscribe, () => state);
}

// One row per address: a person with three tabs open is one viewer.
export function groupByAddress({ selfId, clients }) {
  const byIp = new Map();
  for (const c of clients) {
    const g = byIp.get(c.ip) ?? {
      ip: c.ip, viaProxy: false, tabs: 0, admin: false, isSelf: false, since: Infinity,
    };
    g.tabs += 1;
    g.admin ||= c.admin;
    g.viaProxy ||= c.via_proxy;
    g.isSelf ||= c.id === selfId;
    g.since = Math.min(g.since, c.since);
    byIp.set(c.ip, g);
  }
  // This browser first, then admins, then longest connected.
  return [...byIp.values()].sort((a, b) =>
    (b.isSelf - a.isSelf) || (b.admin - a.admin) || (a.since - b.since));
}

// Signed-in viewers other than this machine. Tabs from this browser's own
// address are left out: they are almost always the same person, and two
// people at one PC can see each other anyway.
export function otherAdmins(s = state) {
  return groupByAddress(s).filter((g) => g.admin && !g.isSelf);
}

// window.confirm when another admin is connected; true when there's no one
// else (or the user says go ahead). `action` reads as a question stem, e.g.
// "Stop the recording".
export function confirmIfOtherAdmins(action) {
  const others = otherAdmins();
  if (others.length === 0) return true;
  const who = others.map((g) => g.ip).join(", ");
  const lead = others.length === 1
    ? `Another admin is connected from ${who}.`
    : `${others.length} other admins are connected (${who}).`;
  return window.confirm(
    `${lead} They may be in the middle of something.\n\n${action} anyway?`);
}

// confirmIfOtherAdmins() wording for the controller actions menus
// (ControllerActionsMenu, System page), keyed by their confirm target.
export const CONTROLLER_ACTIONS = {
  restart_service: "Restart the controller service",
  reboot: "Reboot the controller",
  shutdown: "Shut down the controller",
  revert: "Revert the controller update",
};
