// SunSponge capture studio view.

const RESTED_CAPTURE_VIEWPORTS = [
  { id: "desktop", label: "Desktop", hint: "1440 x 1000" },
  { id: "tablet", label: "Tablet", hint: "834 x 1112" },
  { id: "mobile", label: "Mobile", hint: "390 x 844" },
];

const RESTED_CAPTURE_SCHEMES = [
  { id: "light", label: "Light" },
  { id: "dark", label: "Dark" },
];

function RestedCaptureView() {
  const [importMode, setImportMode] = React.useState("site");
  const [urlText, setUrlText] = React.useState("");
  const [sitemapUrl, setSitemapUrl] = React.useState("");
  const [crawlUrl, setCrawlUrl] = React.useState("");
  const [localPath, setLocalPath] = React.useState("");
  const [crawlDepth, setCrawlDepth] = React.useState(8);
  const [maxPages, setMaxPages] = React.useState(1000);
  const [fileName, setFileName] = React.useState("");
  const [viewports, setViewports] = React.useState(["desktop", "tablet", "mobile"]);
  const [schemes, setSchemes] = React.useState(["light", "dark"]);
  const [format, setFormat] = React.useState("png");
  const [fullPage, setFullPage] = React.useState(true);
  const [exportMode, setExportMode] = React.useState("zip");
  const [exportDir, setExportDir] = React.useState("");
  const [concurrency, setConcurrency] = React.useState(3);
  const [waitMs, setWaitMs] = React.useState(600);
  const [job, setJob] = React.useState(null);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState("");
  const fileInputRef = React.useRef(null);

  const api = window.SunSponge;

  const parsedUrls = React.useMemo(() => {
    return urlText
      .split(/[\r\n,]+/)
      .map((value) => value.trim())
      .filter(Boolean);
  }, [urlText]);

  const activeStates = viewports.length * schemes.length;
  const discoveredPages = job && Array.isArray(job.urls) ? job.urls.length : 0;
  const pageEstimate = importMode === "site" || importMode === "local"
    ? (discoveredPages || 1)
    : (importMode === "sitemap" ? 1 : parsedUrls.length);
  const estimatedShots = Math.max(1, pageEstimate) * activeStates;
  const completed = job ? Number(job.completed || 0) : 0;
  const total = job ? Number(job.total || 0) : estimatedShots;
  const progress = total ? Math.min(100, Math.round((completed / total) * 100)) : 0;
  const results = job && Array.isArray(job.results) ? job.results : [];
  const jobRunning = job && ["queued", "running"].includes(job.status);
  const importReady = importMode === "site"
    ? crawlUrl.trim()
    : (importMode === "local" ? localPath.trim() : (importMode === "sitemap" ? sitemapUrl.trim() : parsedUrls.length > 0));
  const canRun = !busy && !jobRunning && activeStates > 0 && Boolean(importReady) && api;
  const importSummary = importMode === "site"
    ? (discoveredPages ? discoveredPages + " pages" : "Site")
    : (importMode === "local" ? (discoveredPages ? discoveredPages + " files" : "Local") : (importMode === "sitemap" ? "Sitemap" : parsedUrls.length + " URLs"));
  const shotSummary = (importMode === "site" || importMode === "local") && !discoveredPages ? "Discover" : estimatedShots + " shots";

  React.useEffect(() => {
    if (!job || !jobRunning || !api) return undefined;
    let alive = true;
    const id = setInterval(async () => {
      try {
        const next = await api.restedCaptureJob(job.job_id);
        if (alive) setJob(next);
      } catch (err) {
        if (alive) setError(err && err.message ? err.message : String(err));
      }
    }, 900);
    return () => { alive = false; clearInterval(id); };
  }, [job && job.job_id, job && job.status]);

  const toggle = (list, setList, id) => {
    setList((prev) => prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id]);
  };

  const parseImportedFile = (text) => {
    const trimmed = text.trim();
    if (!trimmed) return [];
    try {
      const data = JSON.parse(trimmed);
      if (Array.isArray(data)) return data.map(String);
      if (data && Array.isArray(data.urls)) return data.urls.map(String);
    } catch (_) {}
    return trimmed.split(/[\r\n,]+/).map((value) => value.trim()).filter(Boolean);
  };

  const onFileChange = async (event) => {
    const file = event.target.files && event.target.files[0];
    if (!file) return;
    setError("");
    setFileName(file.name);
    try {
      const text = await file.text();
      const urls = parseImportedFile(text);
      setUrlText(urls.join("\n"));
      setImportMode("file");
    } catch (err) {
      setError(err && err.message ? err.message : String(err));
    } finally {
      event.target.value = "";
    }
  };

  const startCapture = async () => {
    if (!canRun) return;
    setBusy(true);
    setError("");
    setJob(null);
    const payload = {
      urls: importMode === "urls" || importMode === "file" ? parsedUrls : [],
      sitemap_url: importMode === "sitemap" ? sitemapUrl.trim() : "",
      crawl: importMode === "site",
      crawl_url: importMode === "site" ? crawlUrl.trim() : "",
      local: importMode === "local",
      local_path: importMode === "local" ? localPath.trim() : "",
      crawl_depth: crawlDepth,
      crawl_concurrency: 6,
      max_pages: maxPages,
      include_sitemaps: true,
      viewports,
      schemes,
      format,
      full_page: fullPage,
      export_mode: exportMode,
      export_dir: exportDir.trim() || null,
      concurrency,
      wait_ms: waitMs,
      name: "sunsponge-captures",
    };
    try {
      const started = await api.startRestedCapture(payload);
      setJob(started);
    } catch (err) {
      setError(err && err.message ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const downloadZip = async () => {
    if (!job || !job.job_id || !api) return;
    setError("");
    try {
      await api.downloadRestedCapture(job.job_id, (job.name || "sunsponge-captures") + ".zip");
    } catch (err) {
      setError(err && err.message ? err.message : String(err));
    }
  };

  return (
    <div className="capture">
      <div className="capture__top">
        <div>
          <div className="capture__eyebrow"><IconCamera size={14} /> WEBSITE CAPTURE</div>
          <div className="capture__brand">
            <span className="capture__brand-mark" aria-hidden="true" />
            <span>SunSponge</span>
          </div>
          <h1 className="capture__title">Capture Studio</h1>
        </div>
        <div className="capture__summary">
          <span>{importSummary}</span>
          <span>{activeStates} states</span>
          <span>{shotSummary}</span>
        </div>
      </div>

      <div className="capture__grid">
        <section className="capture-panel capture-panel--import">
          <div className="capture-panel__head">
            <h2>Import</h2>
            <div className="capture-seg">
              <button className={importMode === "site" ? "is-active" : ""} type="button" onClick={() => setImportMode("site")}>Site</button>
              <button className={importMode === "local" ? "is-active" : ""} type="button" onClick={() => setImportMode("local")}>Local</button>
              <button className={importMode === "urls" ? "is-active" : ""} type="button" onClick={() => setImportMode("urls")}>URLs</button>
              <button className={importMode === "file" ? "is-active" : ""} type="button" onClick={() => { setImportMode("file"); fileInputRef.current && fileInputRef.current.click(); }}>
                <IconUpload size={13} /> File
              </button>
              <button className={importMode === "sitemap" ? "is-active" : ""} type="button" onClick={() => setImportMode("sitemap")}>Sitemap</button>
            </div>
          </div>

          <input ref={fileInputRef} className="capture__file" type="file" accept=".txt,.csv,.json,.xml" onChange={onFileChange} />

          {importMode === "site" ? (
            <>
              <label className="capture-field">
                <span>Site URL</span>
                <input value={crawlUrl} onChange={(e) => setCrawlUrl(e.target.value)} placeholder="https://example.com" />
              </label>
              <div className="capture-numbers">
                <label><span>Depth</span><input type="number" min="0" max="20" value={crawlDepth} onChange={(e) => setCrawlDepth(Number(e.target.value || 0))} /></label>
                <label><span>Max pages</span><input type="number" min="1" max="1000" value={maxPages} onChange={(e) => setMaxPages(Number(e.target.value || 1))} /></label>
              </div>
            </>
          ) : importMode === "local" ? (
            <label className="capture-field">
              <span>Local HTML path</span>
              <input value={localPath} onChange={(e) => setLocalPath(e.target.value)} placeholder="C:\\site\\index.html or C:\\site" />
            </label>
          ) : importMode === "sitemap" ? (
            <label className="capture-field">
              <span>Sitemap URL</span>
              <input value={sitemapUrl} onChange={(e) => setSitemapUrl(e.target.value)} placeholder="https://example.com/sitemap.xml" />
            </label>
          ) : (
            <>
              <textarea
                className="capture-urlbox"
                value={urlText}
                onChange={(e) => setUrlText(e.target.value)}
                placeholder={"https://example.com\nhttps://example.com/pricing"}
                spellCheck={false}
              />
              <div className="capture-inline">
                <button className="btn btn--ghost" type="button" onClick={() => fileInputRef.current && fileInputRef.current.click()}>
                  <IconUpload size={14} /> Import file
                </button>
                {fileName && <span className="capture-muted">{fileName}</span>}
              </div>
            </>
          )}
        </section>

        <section className="capture-panel">
          <div className="capture-panel__head"><h2>States</h2></div>
          <div className="capture-checks">
            {RESTED_CAPTURE_VIEWPORTS.map((item) => (
              <label key={item.id} className={"capture-check" + (viewports.includes(item.id) ? " is-on" : "")}>
                <input type="checkbox" checked={viewports.includes(item.id)} onChange={() => toggle(viewports, setViewports, item.id)} />
                <span className="capture-check__mark" />
                <span><strong>{item.label}</strong><em>{item.hint}</em></span>
              </label>
            ))}
          </div>
          <div className="capture-seg capture-seg--wide">
            {RESTED_CAPTURE_SCHEMES.map((item) => (
              <button key={item.id} className={schemes.includes(item.id) ? "is-active" : ""} type="button" onClick={() => toggle(schemes, setSchemes, item.id)}>
                {item.label}
              </button>
            ))}
          </div>
          <label className="capture-toggle">
            <input type="checkbox" checked={fullPage} onChange={(e) => setFullPage(e.target.checked)} />
            <span>Full page</span>
          </label>
        </section>

        <section className="capture-panel">
          <div className="capture-panel__head"><h2>Export</h2></div>
          <div className="capture-seg capture-seg--wide">
            <button className={format === "png" ? "is-active" : ""} type="button" onClick={() => setFormat("png")}>PNG</button>
            <button className={format === "jpeg" ? "is-active" : ""} type="button" onClick={() => setFormat("jpeg")}>JPEG</button>
          </div>
          <div className="capture-seg capture-seg--wide">
            <button className={exportMode === "zip" ? "is-active" : ""} type="button" onClick={() => setExportMode("zip")}>ZIP</button>
            <button className={exportMode === "folder" ? "is-active" : ""} type="button" onClick={() => setExportMode("folder")}>Folder</button>
          </div>
          <label className="capture-field">
            <span>Export folder (optional)</span>
            <input value={exportDir} onChange={(e) => setExportDir(e.target.value)} placeholder="Server-side path" />
          </label>
          <div className="capture-numbers">
            <label><span>Workers</span><input type="number" min="1" max="8" value={concurrency} onChange={(e) => setConcurrency(Number(e.target.value || 1))} /></label>
            <label><span>Settle ms</span><input type="number" min="0" step="100" value={waitMs} onChange={(e) => setWaitMs(Number(e.target.value || 0))} /></label>
          </div>
        </section>
      </div>

      <section className="capture-run">
        <div className="capture-run__main">
          <button className="btn btn--primary capture-run__button" type="button" disabled={!canRun} onClick={startCapture}>
            <IconPlay size={15} /> {busy || jobRunning ? "Capturing" : "Capture"}
          </button>
          <div className="capture-progress" aria-label="Capture progress">
            <div className="capture-progress__bar" style={{ width: progress + "%" }} />
          </div>
          <div className="capture-run__meta">
            {job ? job.message : (canRun ? "Ready" : "Waiting")}
          </div>
        </div>
        {job && job.zip_url && (
          <button className="btn btn--ghost" type="button" onClick={downloadZip}>
            <IconDownload size={14} /> Download ZIP
          </button>
        )}
      </section>

      {error && (
        <div className="capture-alert">
          <IconAlertCircle size={15} />
          <span>{error}</span>
        </div>
      )}

      {job && (
        <section className="capture-results">
          <div className="capture-results__head">
            <h2>Results</h2>
            <span>{completed}/{total}</span>
          </div>
          <div className="capture-result-list">
            {results.length === 0 && <div className="capture-empty">Queued</div>}
            {results.slice().reverse().map((item, index) => (
              <div key={index} className={"capture-result capture-result--" + item.status}>
                <span className="capture-result__icon">
                  {item.status === "ok" ? <IconCheckCircle size={15} /> : <IconAlertCircle size={15} />}
                </span>
                <span className="capture-result__url">{item.url}</span>
                <span className="capture-result__state">{item.state_id}</span>
                <span className="capture-result__time">{item.elapsed_ms} ms</span>
                {item.error && <span className="capture-result__error">{item.error}</span>}
              </div>
            ))}
          </div>
          {job.output_dir && <div className="capture-output">{job.output_dir}</div>}
        </section>
      )}
    </div>
  );
}

Object.assign(window, { RestedCaptureView });