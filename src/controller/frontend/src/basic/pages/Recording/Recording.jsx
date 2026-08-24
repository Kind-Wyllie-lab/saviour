import React from "react";
import { Routes, Route } from "react-router";

import RecordingLayout from "./RecordingLayout";
import NoSessionSelected from "./NoSessionSelected";
import SessionDetailPage from "./SessionDetailPage/SessionDetailPage";

// Mounted at "/recording/*" (see the variant App.jsx files) so these nested
// routes can resolve. RecordingLayout is a layout route -- it renders the
// session-list rail once and an <Outlet/> for whichever child below is
// active, rather than each child owning the whole page.
function Recording() {
    return (
        <Routes>
            <Route element={<RecordingLayout />}>
                <Route index element={<NoSessionSelected />} />
                <Route path="sessions/:sessionName" element={<SessionDetailPage />} />
            </Route>
        </Routes>
    );
}

export default Recording;
