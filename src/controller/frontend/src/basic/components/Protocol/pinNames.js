// What a scheduled TTL pin is called in the protocol designer: its label
// from the module's config (ttl.pins.<gpio>.label, set on the Pins / Lines
// tab), falling back to the GPIO number.

export function pinLabel(pinConfigs, pin) {
  const cfg = pinConfigs?.[pin] ?? pinConfigs?.[String(pin)];
  const label = typeof cfg?.label === "string" ? cfg.label.trim() : "";
  return label || null;
}

/** "Lights (GPIO 17)" when labelled, else "GPIO 17". */
export function pinName(pinConfigs, pin) {
  const label = pinLabel(pinConfigs, pin);
  return label ? `${label} (GPIO ${pin})` : `GPIO ${pin}`;
}
