import React, { useState, useEffect, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import "./Monitor.css";

import useModules from "/src/hooks/useModules";
import HabitatLivestreamGrid from "/src/habitat/components/HabitatLivestreamGrid/HabitatLivestreamGrid";

function Monitor() {
    // Press escape to return to homepae
    const navigate = useNavigate();

    const onClose = useCallback(() => {
        navigate("/");
    }, [navigate]);

    useEffect(() => {
        const handleKeyDown = (e) => {
          if (e.key === "Escape") onClose();
        };
        document.addEventListener("keydown", handleKeyDown);
        return () => document.removeEventListener("keydown", handleKeyDown);
      }, [onClose]);

    // Get modules
    const { modules } = useModules();

    return (
        <div className="fullscreen-modal">
            <div className="monitor-left">

            </div>
            <div className="monitor-right">
                <HabitatLivestreamGrid modules={modules} />
            </div>
        </div>
    )
}

export default Monitor;