import React, { useState, useEffect } from "react";
import HabitatLivestreamCard from "../HabitatLivestreamCard/HabitatLivestreamCard";
import "./HabitatLivestreamGrid.css";

const COLUMNS = ["A", "B", "C", "D"];
const ROWS = [1, 2, 3, 4];

function HabitatLivestreamGrid({ modules }) {
    const moduleByName = React.useMemo(() => {
        return Object.values(modules).reduce((acc, module) => {
          acc[module.name] = module;
          return acc;
        }, {});
      }, [modules]);



    // Form a grid based on module name - columns A to D, rows 1 to 4
    return (
        <div className="habitat-grid">
          {["A", "B", "C", "D"].map((col) =>
            [1, 2, 3, 4].map((row) => {
              const cell = `${col}${row}`;
              const module = moduleByName[cell];
    
              return (
                <div key={cell} className="habitat-grid-cell">
                  {module ? (
                    <HabitatLivestreamCard module={module} />
                  ) : (
                    <div className="empty-cell">{cell}</div>
                  )}
                </div>
              );
            })
          )}
        </div>
    );
}

export default HabitatLivestreamGrid;