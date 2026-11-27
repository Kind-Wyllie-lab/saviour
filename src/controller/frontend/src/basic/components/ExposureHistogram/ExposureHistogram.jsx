import { useEffect, useState } from "react";
import { exposureUrl } from "/src/basic/utils/streamUrls";
import "./ExposureHistogram.css";

// Live pixel-brightness histogram for a camera (its /exposure.json, once a
// second while shown). Black on the left, white on the right; the clipped
// ends are marked. For a dark animal in a white arena the right shape is a
// tall bar at white (the background) and the animal spread wide over the
// rest, not squeezed into a narrow hump.

const VERDICT_TEXT = {
  ok: null,
  over: "Overexposed: clipped white and nothing in the frame is dark. Lower exposure or gain.",
  under: "Underexposed: crushed black and nothing in the frame is bright. Raise exposure or gain.",
  low_contrast: "Low contrast: the image uses only a narrow band of greys. Raise contrast or exposure, or open the aperture.",
};

export default function ExposureHistogram({ module }) {
  const [data, setData] = useState(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let live = true;
    const poll = async () => {
      const url = exposureUrl(module);
      if (!url) return;
      try {
        const r = await fetch(url, { cache: "no-store" });
        const j = await r.json();
        if (live) { setData(j); setFailed(false); }
      } catch {
        if (live) setFailed(true);
      }
    };
    poll();
    const t = setInterval(poll, 1000);
    return () => { live = false; clearInterval(t); };
  }, [module?.ip]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!module?.ip) return null;
  const hist = data?.available ? data.hist ?? [] : [];
  const max = Math.max(1, ...hist);
  // The background bar can dwarf the rest: square-root scale keeps the
  // subject's spread visible beside a tall white bar.
  const h = (c) => Math.sqrt(c / max);
  const W = 256, H = 64, bw = W / Math.max(1, hist.length);
  const verdict = data?.verdict ?? "ok";

  return (
    <div className="exp-hist">
      <div className="exp-hist-head">
        <span className="exp-hist-title">Brightness histogram</span>
        {data?.available && (
          <span className="exp-hist-figs">
            white {data.over_pct?.toFixed(0)}% · black {data.under_pct?.toFixed(0)}% · range {Math.round(data.p1)}-{Math.round(data.p99)}
          </span>
        )}
      </div>
      {failed || !data?.available ? (
        <p className="exp-hist-empty">{failed ? "Can't reach the camera's histogram." : "Waiting for the camera…"}</p>
      ) : (
        <svg className="exp-hist-plot" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img"
          aria-label="Pixel brightness histogram, black to white">
          {hist.map((c, i) => (
            <rect key={i} x={i * bw} y={H - h(c) * H} width={bw - 0.5} height={h(c) * H}
              className={i === 0 || i === hist.length - 1 ? "exp-hist-bar exp-hist-bar--edge" : "exp-hist-bar"} />
          ))}
        </svg>
      )}
      <div className="exp-hist-axis"><span>black</span><span>white</span></div>
      {VERDICT_TEXT[verdict] && <p className={`exp-hist-verdict exp-hist-verdict--${verdict}`}>{VERDICT_TEXT[verdict]}</p>}
      <p className="exp-hist-hint">
        A white arena can sit at white: that&apos;s fine. What matters is that the animal&apos;s greys spread
        wide across the range rather than bunching into a narrow hump.
      </p>
    </div>
  );
}
