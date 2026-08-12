import React, { useState, useMemo, useCallback, useRef } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeRaw from "rehype-raw";
import rehypeSlug from "rehype-slug";
import "./Guide.css";
import { docs, resolveDocLink, resolveDocImage, extractOutline } from "/src/docs/loadDocs.js";

// Mirrors mkdocs.yml's nav — the RTD site's structure is this page's
// structure too, so there's one source of truth for both.
const TABS = [
  { id: "index", label: "Home" },
  { id: "getting_started", label: "Getting Started" },
  { id: "faqs", label: "FAQs" },
  { id: "open_ephys", label: "Using with Ephys" },
  { id: "how_it_works", label: "How it Works" },
  { id: "hardware", label: "Hardware" },
  { id: "cad", label: "CAD/3D Prints" },
  { id: "about", label: "About" },
];

// mkdocs nests these three under "About" in its nav; combined onto one tab
// here rather than a two-level tab widget, since each file is short.
const ABOUT_DOC_IDS = ["about/license", "about/contributing", "about/acknowledgements"];

function makeMarkdownComponents(docId, onNavigate) {
  return {
    a({ href, children, ...props }) {
      const resolved = resolveDocLink(href, docId);
      if (resolved.type === "external") {
        return (
          <a href={resolved.href} target="_blank" rel="noopener noreferrer" {...props}>
            {children}
          </a>
        );
      }
      if (resolved.type === "anchor") {
        return <a href={resolved.href} {...props}>{children}</a>;
      }
      return (
        <a
          href={`#${resolved.tabId}`}
          onClick={(e) => {
            e.preventDefault();
            onNavigate(resolved.tabId);
          }}
          {...props}
        >
          {children}
        </a>
      );
    },
    img({ src, alt, ...props }) {
      return <img src={resolveDocImage(src, docId)} alt={alt} loading="lazy" {...props} />;
    },
  };
}

function DocPage({ docId, onNavigate }) {
  const content = docs[docId];
  const components = useMemo(() => makeMarkdownComponents(docId, onNavigate), [docId, onNavigate]);

  if (content == null) {
    return <p className="guide-missing">Doc page "{docId}" not found.</p>;
  }

  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      rehypePlugins={[rehypeRaw, rehypeSlug]}
      components={components}
    >
      {content}
    </ReactMarkdown>
  );
}

function Guide() {
  const [activeTab, setActiveTab] = useState(TABS[0].id);
  const pageRef = useRef(null);

  const outline = useMemo(
    () => extractOutline(activeTab === "about" ? ABOUT_DOC_IDS : [activeTab]),
    [activeTab]
  );

  const handleNavigate = useCallback((tabId) => {
    setActiveTab(tabId);
    // .guide-page is its own scroll container (see Guide.css), not the
    // window, now that it's contained rather than growing the outer shell.
    pageRef.current?.scrollTo({ top: 0 });
  }, []);

  const handleJumpTo = useCallback((id) => {
    document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, []);

  return (
    <main className="guide-page" ref={pageRef}>
      <nav className="guide-contents">
        <div className="guide-contents-title">Contents</div>
        <ul className="guide-contents-list">
          {TABS.map((tab) => (
            <li key={tab.id}>
              <button
                type="button"
                className={`guide-contents-link${activeTab === tab.id ? " guide-contents-link--active" : ""}`}
                onClick={() => handleNavigate(tab.id)}
              >
                {tab.label}
              </button>
            </li>
          ))}
        </ul>
      </nav>

      <div className="guide-content">
        <div className="guide-topics">
          {activeTab === "about" ? (
            ABOUT_DOC_IDS.map((docId) => (
              <section className="card guide-topic" key={docId}>
                <DocPage docId={docId} onNavigate={handleNavigate} />
              </section>
            ))
          ) : (
            <section className="card guide-topic">
              <DocPage docId={activeTab} onNavigate={handleNavigate} />
            </section>
          )}
        </div>
      </div>

      {outline.length > 0 && (
        <nav className="guide-outline">
          <div className="guide-outline-title">On this page</div>
          <ul className="guide-outline-list">
            {outline.map((entry) => (
              <li
                key={`${entry.docId}-${entry.id}`}
                className={`guide-outline-item guide-outline-item--level${entry.level}`}
              >
                <a
                  href={`#${entry.id}`}
                  onClick={(e) => {
                    e.preventDefault();
                    handleJumpTo(entry.id);
                  }}
                >
                  {entry.text}
                </a>
              </li>
            ))}
          </ul>
        </nav>
      )}
    </main>
  );
}

export default Guide;
