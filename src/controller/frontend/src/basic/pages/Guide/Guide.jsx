import React from "react";
import "./Guide.css";

// Each topic gets a short written walkthrough plus an optional tutorial
// video. Fill in `youtubeId` (the id from a youtube.com/watch?v=<id> URL,
// or a full youtube.com/embed/<id> share link's id) to embed a video —
// topics with no id yet just show the written steps.
const TOPICS = [
  {
    title: "Assigning a device role",
    youtubeId: null,
    steps: [
      "Flash the SD card and boot the Raspberry Pi 5 on the PoE network.",
      "Run `sudo saviour-config` on the device.",
      "Choose Controller (one per system) or Module, then pick the module type (camera, microphone, TTL, RFID, ...).",
      "The device reboots into its assigned role and appears automatically once discovered.",
    ],
  },
  {
    title: "Connecting modules to the controller",
    youtubeId: null,
    steps: [
      "Power on the controller first — it acts as the PTP grandmaster and service discovery hub.",
      "Power on modules; they register over mDNS and appear on the Dashboard within a few seconds.",
      "Check the System page to confirm every module shows a recent heartbeat and a locked PTP offset before recording.",
    ],
  },
  {
    title: "Running a recording session",
    youtubeId: null,
    steps: [
      "Configure each module on the Settings page (resolution, sample rate, etc.) before starting.",
      "On the Recording page, click \"Check Ready\" to confirm PTP sync is within threshold on every module.",
      "Start the session, and stop it (or let a scheduled window end it) once you're done.",
      "Recordings export automatically to the controller's share once each module finishes.",
    ],
  },
  {
    title: "Exporting and retrieving data",
    youtubeId: null,
    steps: [
      "Recordings land on the controller's Samba share, organised by session name and date.",
      "Connect to the share from a lab workstation to copy files off, or point analysis tools at it directly.",
      "Use tools/analyse_framesync.py and tools/make_aligned_video.py to check multi-camera timing and build aligned review videos.",
    ],
  },
  {
    title: "Troubleshooting a module",
    youtubeId: null,
    steps: [
      "A module marked offline usually means its heartbeat timed out — check power, PoE link and network cabling first.",
      "The System page shows per-module CPU, disk, temperature and PTP offset — a drifting PTP offset points to a clock sync problem, not a recording bug.",
      "Reboot or shut down an individual module from its actions menu on the System page if it needs a clean restart.",
    ],
  },
];

function Guide() {
  return (
    <main className="guide-page">
      <div className="guide-header">
        <h2>Guide</h2>
        <p className="guide-intro">
          Instructions and video walkthroughs for setting up and running SAVIOUR.
        </p>
      </div>

      <div className="guide-topics">
        {TOPICS.map((topic) => (
          <section className="card guide-topic" key={topic.title}>
            <h3>{topic.title}</h3>

            {topic.youtubeId ? (
              <div className="guide-video-wrap">
                <iframe
                  className="guide-video"
                  src={`https://www.youtube-nocookie.com/embed/${topic.youtubeId}`}
                  title={topic.title}
                  frameBorder="0"
                  allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
                  allowFullScreen
                />
              </div>
            ) : (
              <p className="guide-video-placeholder">Video walkthrough coming soon.</p>
            )}

            <ol className="guide-steps">
              {topic.steps.map((step, i) => (
                <li key={i}>{step}</li>
              ))}
            </ol>
          </section>
        ))}
      </div>
    </main>
  );
}

export default Guide;
