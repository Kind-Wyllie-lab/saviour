import { useCallback, useEffect, useRef, useState } from "react";
import socket from "/src/socket";

// The controller's sound library (src/controller/sound_library.py): the
// list (summaries), one file's full analysis on demand, and the
// upload -> analyse -> add/cancel flow.
const CHUNK = 256 * 1024;

export default function useSoundLibrary() {
  const [items, setItems] = useState(null);
  const [detail, setDetail] = useState(null);      // full entry incl. render data
  const [upload, setUpload] = useState(null);      // {received,total} | {analysis,name,replaces} | {error}
  const [error, setError] = useState(null);
  const cancelled = useRef(false);

  useEffect(() => {
    const onList = (d) => setItems(d?.items ?? []);
    const onFile = (d) => setDetail(d);
    const onProgress = (d) => setUpload((u) => (u && !u.analysis ? { ...u, ...d, stage: "uploading" } : u));
    const onAnalysis = (d) => setUpload({ ...d, stage: "analysed" });
    const onError = (d) => {
      const msg = d?.error ?? "Sound library error";
      setUpload((u) => (u && u.stage !== "analysed" ? { error: msg } : u));
      setError(msg);
    };
    socket.on("sound_library", onList);
    socket.on("sound_file", onFile);
    socket.on("sound_upload_progress", onProgress);
    socket.on("sound_upload_analysis", onAnalysis);
    socket.on("sound_library_error", onError);
    socket.emit("get_sound_library");
    return () => {
      socket.off("sound_library", onList);
      socket.off("sound_file", onFile);
      socket.off("sound_upload_progress", onProgress);
      socket.off("sound_upload_analysis", onAnalysis);
      socket.off("sound_library_error", onError);
    };
  }, []);

  const open = useCallback((name) => {
    setDetail((d) => (d?.name === name ? d : null));
    if (name) socket.emit("get_sound_file", { name });
  }, []);

  const start = useCallback((file) => {
    setError(null);
    if (!file) return;
    if (!/\.wav$/i.test(file.name)) {
      setUpload({ error: "Choose a .wav file (convert other formats to WAV first)." });
      return;
    }
    const total = Math.max(1, Math.ceil(file.size / CHUNK));
    cancelled.current = false;
    setUpload({ stage: "uploading", received: 0, total, filename: file.name });
    socket.emit("upload_sound_start", { filename: file.name, total_bytes: file.size, total_chunks: total });
    (async () => {
      for (let i = 0; i < total; i++) {
        if (cancelled.current) return;
        const data = await file.slice(i * CHUNK, (i + 1) * CHUNK).arrayBuffer();
        socket.emit("upload_sound_chunk", { index: i, data });
        await new Promise((r) => setTimeout(r, 0));
      }
    })();
  }, []);

  const add = useCallback((name) => {
    socket.emit("add_sound", { name });
    setUpload(null);
  }, []);

  const discard = useCallback((name) => {
    cancelled.current = true;
    if (name) socket.emit("discard_sound", { name });
    setUpload(null);
  }, []);

  const remove = useCallback((name) => {
    setError(null);
    socket.emit("delete_sound", { name });
  }, []);

  return { items, detail, open, upload, start, add, discard, remove, error, clearError: () => setError(null) };
}
