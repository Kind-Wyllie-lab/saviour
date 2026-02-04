import React, { useState } from "react";

import socket from "/src/socket";

function PlaySound({ modules }) {
    const [targetModule, setTargetModule] = useState(""); // selected target

    const soundModules = (modules || []).filter((m) => m.type === "sound");

    console.log(soundModules);


    return (
        <>
            <div>

            </div>
        </>
    )
}

export default PlaySound;