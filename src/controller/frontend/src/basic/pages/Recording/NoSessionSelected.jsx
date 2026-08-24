// Detail-pane placeholder for the index route (/recording, no session
// selected yet) -- RecordingLayout's <Outlet/> renders this until a rail
// row is clicked.
export default function NoSessionSelected() {
  return (
    <div className="no-session-selected">
      <p>Select a session from the list to see its details.</p>
    </div>
  );
}
