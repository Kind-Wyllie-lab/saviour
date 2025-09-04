import React, { useEffect, useState } from "react";
import socket from "../../socket";
import "./ExperimentMetadata.css";

function ExperimentMetadata({ setExperimentName }) {
  const [metadata, setMetadata] = useState({
    rat_id: "001",
    strain: "Wistar",
    batch: "B1",
    stage: "habituation",
    trial: "1",
  });

  // Effect: request current metadata on mount
  useEffect(() => {
    socket.emit("get_experiment_metadata");

    const handleResponse = (data) => {
      if (data.status === "success") {
        setMetadata(data.metadata);
        setExperimentName(generateExperimentName(data.metadata));
      }
    };

    socket.on("experiment_metadata_response", handleResponse);
    socket.on("experiment_metadata_updated", handleResponse);

    return () => {
      socket.off("experiment_metadata_response", handleResponse);
      socket.off("experiment_metadata_updated", handleResponse);
    };
  }, []);

  // Handler for input changes
  const handleChange = (field, value) => {
    const updated = { ...metadata, [field]: value };
    setMetadata(updated);

    // Update experiment name in Dashboard
    setExperimentName(generateExperimentName(updated));

    // Send updated metadata to backend
    socket.emit("update_experiment_metadata", updated);
  };

  // Generate experiment name from metadata
  const generateExperimentName = (data = metadata) => {
    const { rat_id, strain, batch, stage, trial } = data;
    return `apa_${rat_id}_${strain}_${batch}_${stage}_t${trial}`;
  };

  return (
    <div className="experiment-metadata-container">
      <h2>Experiment Metadata</h2>

      <div className="metadata-form-row">
        <label htmlFor="rat-id">Rat ID</label>
        <input
          id="rat-id"
          value={metadata.rat_id}
          onChange={(e) => handleChange("rat_id", e.target.value)}
        />
      </div>

      <div className="metadata-form-row">
        <label htmlFor="strain">Strain</label>
        <input
          id="strain"
          value={metadata.strain}
          onChange={(e) => handleChange("strain", e.target.value)}
        />
      </div>

      <div className="metadata-form-row">
        <label htmlFor="batch">Batch</label>
        <input
          id="batch"
          value={metadata.batch}
          onChange={(e) => handleChange("batch", e.target.value)}
        />
      </div>

      <div className="metadata-form-row">
        <label htmlFor="stage">Stage</label>
        <select
          id="stage"
          value={metadata.stage}
          onChange={(e) => handleChange("stage", e.target.value)}
        >
          <option value="habituation">habituation</option>
          <option value="training">training</option>
          <option value="testing">testing</option>
        </select>
      </div>

      <div className="metadata-form-row">
        <label htmlFor="trial">Trial</label>
        <input
          id="trial"
          value={metadata.trial}
          onChange={(e) => handleChange("trial", e.target.value)}
        />
      </div>

      <div className="experiment-name-preview">{generateExperimentName()}</div>
    </div>
  );
}

export default ExperimentMetadata;
