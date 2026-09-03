import { useEffect } from "react";
import socket from "/src/socket";
import LivestreamCard from "/src/basic/components/LivestreamCard/LivestreamCard";
import MJPEGStreamCard from "/src/basic/components/MJPEGStreamCard/MJPEGStreamCard";
import { useConfigForm } from "../useConfigForm";
import { useHashTab } from "../useHashTab";
import { filterPrivateKeys, isPlainObject, HIDDEN_CONFIG_SECTIONS } from "../configUtils";
import ConfigFields from "../ConfigFields";
import ExportConfigSection from "../ExportConfigSection";
import ConfigCardShell from "../ConfigCardShell";

// Sections that never get a per-section tab: the ones with their own dedicated
// tab (module/export/recording), plus transport/infra plumbing.
const NON_SECTION_TABS = new Set(["module", "export", "recording", ...HIDDEN_CONFIG_SECTIONS]);

// Acronyms that shouldn't be title-cased to "Rfid" / "Ttl".
const SECTION_LABELS = { rfid: "RFID", ttl: "TTL" };
const sectionLabel = (key) =>
  SECTION_LABELS[key] ??
  key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());

function GenericConfigCard({ id, module, clipboard, onCopy }) {
  const { formData, setFormData, handleChange, markSaved, isDirty } = useConfigForm(module.config);

  // One tab per remaining top-level config section (e.g. RFID + Monitoring for
  // an rfid module) instead of a single catch-all "Settings" tab that stacks
  // them all together.
  const cleaned = filterPrivateKeys(formData) ?? {};
  const extraSections = Object.keys(cleaned).filter(
    (k) => !NON_SECTION_TABS.has(k) && isPlainObject(cleaned[k])
  );

  const tabs = [
    { key: "basic", label: "Basic" },
    ...extraSections.map((k) => ({ key: k, label: sectionLabel(k) })),
    { key: "export", label: "Export" },
  ];

  const tabCopySection = {
    basic:  { key: "module", label: "Basic" },
    export: { key: "export", label: "Export" },
    ...Object.fromEntries(
      extraSections.map((k) => [k, { key: k, label: sectionLabel(k) }])
    ),
  };

  const [activeTab, setActiveTab] = useHashTab("basic", tabs.map((t) => t.key));

  useEffect(() => {
    socket.emit("get_module_config", { module_id: module.id });
  }, [module.id]);

  const handlePaste = () => {
    if (!clipboard) return;
    setFormData(prev => {
      const cloned = structuredClone(prev);
      for (const [key, value] of Object.entries(clipboard.data)) {
        cloned[key] = structuredClone(value);
      }
      return cloned;
    });
  };

  return (
    <ConfigCardShell
      id={id}
      module={module}
      formData={formData}
      clipboard={clipboard}
      onCopy={onCopy}
      onPaste={handlePaste}
      tabs={tabs}
      activeTab={activeTab}
      onTabChange={setActiveTab}
      tabSectionMap={tabCopySection}
      markSaved={markSaved}
      isDirty={isDirty}
      sidebar={
        module.type?.includes("camera")
          ? <LivestreamCard module={module} />
          : module.type?.includes("rfid")
            ? <MJPEGStreamCard
                ip={module.ip}
                port={module.config?.monitoring?._port ?? 8083}
                label="RFID pings"
              />
            : null
      }
    >
      {/* BASIC */}
      {activeTab === "basic" && (
        <>
          <div className="form-field">
            <label>Name:</label>
            <input type="text"
              value={formData?.module?.name ?? ""}
              onChange={e => handleChange(["module", "name"], e)} />
          </div>
          <div className="form-field">
            <label>Group:</label>
            <input type="text"
              value={formData?.module?.group ?? ""}
              onChange={e => handleChange(["module", "group"], e)} />
          </div>
          <div className="config-section-divider" />
          <div className="form-field">
            <label>Segment length (mins):</label>
            <input type="number" min="1" step="1"
              value={formData?.recording?.segment_length_mins ?? 60}
              onChange={e => handleChange(["recording", "segment_length_mins"], e)} />
          </div>
        </>
      )}

      {/* One tab per config section — fields rendered directly (no outer
          collapsible fieldset), paths prefixed back to the section. */}
      {extraSections.includes(activeTab) && (
        <form>
          <ConfigFields
            data={cleaned[activeTab]}
            handleChange={(path, e) => handleChange([activeTab, ...path], e)}
          />
        </form>
      )}

      {/* EXPORT */}
      {activeTab === "export" && (
        <ExportConfigSection
          exportConfig={formData?.export}
          handleChange={handleChange}
          moduleId={module.id}
        />
      )}
    </ConfigCardShell>
  );
}

export default GenericConfigCard;
