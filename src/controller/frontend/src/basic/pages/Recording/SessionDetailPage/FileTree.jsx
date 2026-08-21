import React from "react";
import { formatBytes, DOWNLOAD_ALL_MAX_BYTES } from "../sessionFormat";

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

function sumBytes(node) {
  let total = node.files.reduce((acc, f) => acc + (f.size_bytes || 0), 0);
  for (const sub of node.dirs.values()) total += sumBytes(sub);
  return total;
}

// defaultOpen only applies to the top level (typically the date folder(s)
// -- usually just one) -- deeper levels (typically per-module) default
// collapsed so a large habitat deployment's file list stays scannable
// rather than dumping every module's files open at once.
//
// pathPrefix accumulates the folder path relative to the session root as
// we recurse, so each folder's zip link can hit
// /api/sessions/<name>/download/<folder path> -- the same route a single
// file download uses, now also zip-streaming when the resolved path is a
// directory (web.py's download_session_file).
function FileTreeNode({ node, sessionName, defaultOpen, pathPrefix }) {
  return (
    <div className="session-file-tree__level">
      {[...node.dirs.entries()].map(([name, sub]) => {
        const n = countFiles(sub);
        const folderPath = pathPrefix ? `${pathPrefix}/${name}` : name;
        const totalBytes = sumBytes(sub);
        const tooLarge = totalBytes > DOWNLOAD_ALL_MAX_BYTES;
        const encodedFolderPath = folderPath.split("/").map(encodeURIComponent).join("/");
        const folderUrl = `/api/sessions/${sessionName}/download/${encodedFolderPath}`;
        const zipName = `${sessionName}-${folderPath.replace(/\//g, "-")}.zip`;
        return (
          <details key={name} className="session-file-tree__folder" open={defaultOpen}>
            <summary className="session-file-tree__summary">
              <span className="session-file-tree__summary-row">
                <span className="session-file-tree__arrow" aria-hidden="true">▸</span>
                <span className="session-file-tree__label">
                  {name}{" "}
                  <span className="session-file-tree__count">
                    ({n} file{n !== 1 ? "s" : ""}, {formatBytes(totalBytes)})
                  </span>
                </span>
                {tooLarge ? (
                  <span
                    className="session-file-dl session-file-tree__folder-dl session-file-dl--disabled"
                    title={`Too large to zip via browser (${formatBytes(totalBytes)}) - download individual files or use the network share`}
                  >
                    Download zip
                  </span>
                ) : (
                  <a
                    className="session-file-dl session-file-tree__folder-dl"
                    href={folderUrl}
                    download={zipName}
                    onClick={(e) => e.stopPropagation()}
                    title={`Download all ${n} file${n !== 1 ? "s" : ""} in this folder as a zip`}
                  >
                    Download zip
                  </a>
                )}
              </span>
            </summary>
            <FileTreeNode node={sub} sessionName={sessionName} defaultOpen={false} pathPrefix={folderPath} />
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
      <FileTreeNode node={tree} sessionName={sessionName} defaultOpen pathPrefix="" />
    </div>
  );
}
