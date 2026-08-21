import React from "react";
import { Routes, Route } from "react-router";

import RecordingOverview from "./RecordingOverview";
import SessionDetailPage from "./SessionDetailPage/SessionDetailPage";

// Mounted at "/recording/*" (see the variant App.jsx files) so these nested
// routes can resolve -- the list view at /recording, a full-width detail
// view per session at /recording/sessions/<name>.
function Recording() {
    return (
        <Routes>
            <Route index element={<RecordingOverview />} />
            <Route path="sessions/:sessionName" element={<SessionDetailPage />} />
        </Routes>
    );
}

export default Recording;
