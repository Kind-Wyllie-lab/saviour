// Status ships with a symbol + word in `label`, never colour alone.
// cls: ok | warn | danger | idle (styles in SyncPanel.css).
export default function StatusChip({ cls, label, title }) {
  return <span className={`sync-chip sync-chip--${cls}`} title={title}>{label}</span>;
}
