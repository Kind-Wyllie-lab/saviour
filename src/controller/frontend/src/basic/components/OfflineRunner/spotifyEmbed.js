// Spotify's embedded player, driven through its iFrame API
// (https://developer.spotify.com/documentation/embeds). Nothing is fetched
// from Spotify until someone starts the game, and nothing is bundled: the
// audio comes from Spotify's own player, which also needs this browser to
// have internet access. Without a Spotify login it plays a 30 s preview.

export const DEFAULT_GAME_MUSIC = "https://open.spotify.com/track/1vcxF91pWs9uNwDROuiCPB";

const API_SRC = "https://open.spotify.com/embed/iframe-api/v1";
let apiPromise = null;

/** Resolve with Spotify's IFrameAPI object (loaded once per page). */
export function loadSpotifyApi() {
  if (apiPromise) return apiPromise;
  apiPromise = new Promise((resolve, reject) => {
    window.onSpotifyIframeApiReady = (api) => resolve(api);
    const s = document.createElement("script");
    s.src = API_SRC;
    s.async = true;
    s.onerror = () => {
      apiPromise = null; // let a later attempt retry, e.g. once back online
      s.remove();
      reject(new Error("Couldn't reach Spotify"));
    };
    document.body.appendChild(s);
  });
  return apiPromise;
}

/** "spotify:track:<id>" (or album / playlist / episode) from an
 *  open.spotify.com link, an embed <iframe> snippet or a spotify: URI;
 *  null if there isn't one. */
export function spotifyUri(text) {
  const t = (text || "").trim();
  const uri = t.match(/^spotify:(track|album|playlist|episode):([A-Za-z0-9]{10,40})$/);
  if (uri) return t;
  const m = t.match(
    /open\.spotify\.com\/(?:intl-[a-z-]+\/)?(?:embed\/)?(track|album|playlist|episode)\/([A-Za-z0-9]{10,40})/);
  return m ? `spotify:${m[1]}:${m[2]}` : null;
}
