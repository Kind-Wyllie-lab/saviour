import React, { useState } from "react";
import "./Guide.css";

// Each topic gets a short written walkthrough plus an optional tutorial
// video. Fill in `youtubeId` (the id from a youtube.com/watch?v=<id> URL,
// or a full youtube.com/embed/<id> share link's id) to embed a video —
// topics with no id yet just show the written steps.
const BASICS_TOPICS = [
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

const EPHYS_TOPICS = [
  {
    title: "Syncing SAVIOUR to an ephys acquisition system",
    youtubeId: null,
    steps: [
      "Wire a shared TTL line between the ephys acquisition system and a TTL module input pin so both systems see the same sync pulses.",
      "Recording an input pin logs each pulse edge with a PTP-disciplined timestamp, in the same clock domain as every camera/microphone/TTL module on the network.",
      "On the ephys side, log the same pulses against its own acquisition clock — the shared pulses are your alignment reference between the two clocks.",
    ],
  },
  {
    title: "Sending sync pulses out to the ephys rig",
    youtubeId: null,
    steps: [
      "Configure a TTL module output pin (fixed-interval or pseudorandom pulse train) and wire it into a spare digital/sync input on the acquisition system.",
      "Start the pulse train before recording begins so there are reference edges throughout the whole session, not just at the start.",
      "A pseudorandom (non-periodic) pulse train is easier to align unambiguously than a fixed-rate one if a few pulses are missed on either side.",
    ],
  },
  {
    title: "Aligning ephys data post-hoc",
    youtubeId: null,
    steps: [
      "Export the TTL module's per-pulse timestamp CSV alongside the ephys recording.",
      "Match pulse edges between the two logs to fit a clock offset (and drift, if the ephys clock isn't disciplined) between ephys time and SAVIOUR/PTP time.",
      "Apply that mapping to bring spike times, video frames and any other module's timestamps into one common timeline.",
    ],
  },
];

const FAQS = [
  {
    question: "How do I know if PTP sync is good enough to start recording?",
    answer: "Click \"Check Ready\" on the Recording page — it checks ptp4l and phc2sys offset on every module against a threshold (50 µs by default) and reports which modules aren't ready. The System page also shows live per-module PTP offset if you want to watch it settle after a reboot.",
  },
  {
    question: "A camera was just rebooted — can I record straight away?",
    answer: "Give it 5–10 minutes first. phc2sys needs that long to converge its frequency estimate for that crystal; recording immediately after a reboot can leave a larger-than-usual (but still bounded) inter-camera phase offset.",
  },
  {
    question: "What happens to a recording if the NAS/export share goes down?",
    answer: "Files are staged locally on the module and export is retried with backoff once the share comes back — recordings aren't lost, but they won't appear on the share until the export queue catches up.",
  },
  {
    question: "Can I change which rig UI (basic / loom / apa / habitat / acoustic startle) a controller shows?",
    answer: "The frontend variant is selected by which App is imported in src/controller/frontend/src/main.jsx. Switching rigs means editing that import and rebuilding the frontend — it isn't a runtime setting yet.",
  },
  {
    question: "Do all modules need to be the same type?",
    answer: "No — a system is any mix of camera, microphone, TTL, RFID and rig-specific module types (loom camera, APA camera/arduino) all reporting to one controller. Add or remove modules freely; the controller discovers them automatically over mDNS.",
  },
];

const SECTIONS = [
  { id: "basics", label: "Basics" },
  { id: "ephys", label: "Using with Ephys" },
  { id: "faq", label: "FAQ" },
];

function TopicCard({ topic }) {
  return (
    <section className="card guide-topic">
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
  );
}

function FaqAccordion({ faqs }) {
  const [openIndex, setOpenIndex] = useState(null);
  return (
    <div className="card guide-faq">
      {faqs.map((faq, i) => {
        const isOpen = openIndex === i;
        return (
          <div className="guide-faq-item" key={faq.question}>
            <button
              type="button"
              className="guide-faq-question"
              aria-expanded={isOpen}
              onClick={() => setOpenIndex(isOpen ? null : i)}
            >
              <span>{faq.question}</span>
              <span className="guide-faq-caret">{isOpen ? "−" : "+"}</span>
            </button>
            {isOpen && <p className="guide-faq-answer">{faq.answer}</p>}
          </div>
        );
      })}
    </div>
  );
}

function Guide() {
  const [activeSection, setActiveSection] = useState(SECTIONS[0].id);

  return (
    <main className="guide-page">
      <div className="guide-header">
        <h2>Guide</h2>
        <p className="guide-intro">
          Instructions and video walkthroughs for setting up and running SAVIOUR.
        </p>
      </div>

      <nav className="guide-tabs">
        {SECTIONS.map((section) => (
          <button
            key={section.id}
            type="button"
            className={`guide-tab${activeSection === section.id ? " guide-tab--active" : ""}`}
            onClick={() => setActiveSection(section.id)}
          >
            {section.label}
          </button>
        ))}
      </nav>

      <div className="guide-topics">
        {activeSection === "basics" && BASICS_TOPICS.map((topic) => (
          <TopicCard topic={topic} key={topic.title} />
        ))}
        {activeSection === "ephys" && EPHYS_TOPICS.map((topic) => (
          <TopicCard topic={topic} key={topic.title} />
        ))}
        {activeSection === "faq" && <FaqAccordion faqs={FAQS} />}
      </div>
    </main>
  );
}

export default Guide;
