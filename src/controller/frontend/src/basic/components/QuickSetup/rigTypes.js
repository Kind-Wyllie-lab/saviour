// What each rig type (controller variant, VITE_VARIANT) is for, for Quick
// setup's first step.
export const RIG_TYPES = {
  basic: { label: "Basic rig", description: "General recording: cameras, microphones and TTL with no experiment-specific pages." },
  apa: { label: "APA rig", description: "Active place avoidance: an overhead camera tracks the animal on the rotating arena and the shock controller delivers shocks in the zone." },
  loom: { label: "Loom rig", description: "Looming stimulus: a loom camera with a region of interest, the stimulus timed to the animal's position." },
  acoustic_startle: { label: "Acoustic startle rig", description: "A sound module plays startle stimuli while cameras record the response." },
  habitat: { label: "Habitat", description: "The home-cage habitat: one camera per box, a microphone module per column, the RFID gate bus and the lighting." },
};

export const BUILT_VARIANT = import.meta.env.VITE_VARIANT || "basic";
