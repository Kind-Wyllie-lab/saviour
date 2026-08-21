import React from "react";
import { formatBytes } from "../sessionFormat";

// Builds a nested tree from the flat {name, path, size_bytes} list
// get_session_file_info returns -- path is a "/"-joined relative path
// (e.g. "20260821/camera_a/rec_0001.ts"), matching the layout
// Export._format_export_path() actually writes on the share:
// session/date/module_name/filename. Kept private to this file (not
// exported) so react-refresh/only-export-components doesn't flag mixing
// component and non-component exports.
function buildFileTree(files) {
  const root = { dirs: new Map(), files: [] };
  for (const file of files) {
    const parts = file.path.split("/");
    let node = root;
    for (let i = 0; i < parts.length - 1; i++) {
      const seg = parts[i];
      if (!node.dirs.has(seg)) node.dirs.set(seg, { dirs: new Map(), files: [] });
      node = node.dirs.get(seg);
    }
    node.files.push(file);
  }
  return root;
}

function countFiles(node) {
  let count = node.files.length;
  for (const sub of node.dirs.values()) count += countFiles(sub);
  return count;
}

// defaultOpen only applies to the top level (typically the date folder(s)
// -- usually just one) -- deeper levels (typically per-module) default
// collapsed so a large habitat deployment's file list stays scannable
// rather than dumping every module's files open at once.
function FileTreeNode({ node, sessionName, defaultOpen }) {
  return (
    <div className="session-file-tree__level">
      {[...node.dirs.entries()].map(([name, sub]) => {
        const n = countFiles(sub);
        return (
          <details key={name} className="session-file-tree__folder" open={defaultOpen}>
            <summary className="session-file-tree__summary">
              {name}{" "}
              <span className="session-file-tree__count">
                ({n} file{n !== 1 ? "s" : ""})
              </span>
            </summary>
            <FileTreeNode node={sub} sessionName={sessionName} defaultOpen={false} />
          </details>
        );
      })}
      {node.files.map((file) => {
        const encodedPath = file.path.split("/").map(encodeURIComponent).join("/");
        const url = `/api/sessions/${sessionName}/download/${encodedPath}`;
        return (
          <div key={file.path} className="session-file-row">
            <span className="session-file-name" title={file.path}>{file.name}</span>
            <span className="session-file-size">{formatBytes(file.size_bytes)}</span>
            <a className="session-file-dl" href={url} download={file.name}>Download</a>
          </div>
        );
      })}
    </div>
  );
}

export default function FileTree({ files, sessionName }) {
  const tree = buildFileTree(files);
  return (
    <div className="session-file-tree">
      <FileTreeNode node={tree} sessionName={sessionName} defaultOpen />
    </div>
  );
}
