import { useSyncExternalStore } from "react";
import socket from "../socket";

// The controller's grid state (variants/apa/shock_automation.py), broadcast
// as apa_shock_state. One store for the whole page, so the commands panel
// (mounted twice when fullscreen) and the livestream badges always agree.
// The spacebar listeners read it synchronously through getShockState().
let current = null;
const subscribers = new Set();
let listening = false;

function onState(data) {
    current = data;
    subscribers.forEach((fn) => fn());
}

function request() { socket.emit("get_apa_shock_state"); }

function subscribe(fn) {
    if (!listening) {
        listening = true;
        socket.on("apa_shock_state", onState);
        socket.on("connect", request);
        request();
    }
    subscribers.add(fn);
    return () => subscribers.delete(fn);
}

export const getShockState = () => current;
export const subscribeShockState = subscribe;

export default function useShockState() {
    return useSyncExternalStore(subscribe, getShockState);
}

export const MODE_LABELS = { manual: "Manual", auto: "Auto", dry_run: "Dry run" };
