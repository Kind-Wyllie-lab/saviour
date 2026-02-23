import React from "react";
import "./TimeSelect.css";

function TimeSelect({ label, hour, setHour, minute, setMinute }) {
    const hours = Array.from({ length: 24 }, (_, i) => i.toString().padStart(2, '0'));
    // const minutes = ['00', '05', '10', '15', '20', '25', '30', '35', '40', '45', '50', '55'];
    // const minutes = ['00', '15', '30', '45'];
    const minutes = Array.from({ length: 60 }, (_, i) => i.toString().padStart(2, '0'));

    return (
        <div className="time-select-form-row">
            <label>{label}</label>
            <select value={hour} onChange={(e) => setHour(e.target.value)}>
                {hours.map((h) => (
                    <option key={h} value={h}>
                        {h}
                    </option>
                ))}
            </select>
            :
            <select value={minute} onChange={(e) => setMinute(e.target.value)}>
                {minutes.map((m) => (
                    <option key={m} value={m} className="time-select-option">
                        {m}
                    </option>
                ))}
            </select>
        </div>
    )
}

export default TimeSelect;