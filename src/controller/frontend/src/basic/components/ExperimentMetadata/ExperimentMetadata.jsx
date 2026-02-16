import React, { useEffect, useState } from "react";
import socket from "/src/socket";
import "./ExperimentMetadata.css";

function ExperimentMetadata( {experimentName} ) {
  const [metadata, setMetadata] = useState({
    experiment: "Not Set",
    rat_id: "",
    strain: "",
    batch: "",
    stage: "",
    trial: "",
  });

  // Effect: request current metadata on mount
  useEffect(() => {
    socket.emit("get_experiment_metadata");

    const handleResponse = (data) => {
      if (data.status === "success") {
        setMetadata(data.metadata);
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
    // Send updated metadata to backend
    socket.emit("update_experiment_metadata", updated);
  };

  return (
    <>

      <div className="experiment-metadata-container card">
        <h2>Experiment Metadata</h2>
        <div className="metadata-form-row">
          <label htmlFor="experiment">Experiment</label>
          <input
            id="experiment"
            value={metadata.experiment}
            onChange={(e) => handleChange("experiment", e.target.value)}
          />
        </div>

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
            <option value=""></option>
            <option value="habituation">habituation</option>
            <option value="training">training</option>
            <option value="testing">testing</option>
            <option value="probe">probe</option> 
            <option value="conflict">conflict</option>
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

        {/* <div className="filename-preview">Filename preview:</p> */}
        <div>
          <p className="session-name-preview-title">Session Name</p>
          <p className="session-name-preview">{experimentName}_(TIMESTAMP)</p>
          </div>
      </div>
    </>

  );
}

export default ExperimentMetadata;
