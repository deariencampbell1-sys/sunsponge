// RHOBEAR Captur'd — capture studio view.
//
// Captur'd is a desktop tool. You give it two things: the built HTML you want
// shots of (a local file or folder), and a PATHWAY MAP — the interaction tree
// your agent produced — pasted or uploaded as markdown / verifier JSON. The map
// says exactly what to capture, so the run is deterministic and fast. No URLs,
// no crawling, no sitemaps.

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
  const [baseUrl, setBaseUrl] = React.useState("");
  const [mapText, setMapText] = React.useState("");
  const [mapFileName, setMapFileName] = React.useState("");
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
  const mapInputRef = React.useRef(null);

  const api = window.Capturd;

  const mapIsJson = React.useMemo(() => {
    const trimmed = mapText.trim();
    if (!trimmed) return false;
    try { JSON.parse(trimmed); return true; } catch (_) { return false; }
  }, [mapText]);

  const activeStates = viewports.length * schemes.length;
  const discoveredPages = job && Array.isArray(job.urls) ? job.urls.length : 0;
  const completed = job ? Number(job.completed || 0) : 0;
  const total = job ? Number(job.total || 0) : 0;
  const progress = total ? Math.min(100, Math.round((completed / total) * 100)) : 0;
  const results = job && Array.isArray(job.results) ? job.results : [];
  const jobRunning = job && ["queued", "running"].includes(job.status);
  const mapReady = Boolean(mapText.trim());
  const htmlReady = Boolean(baseUrl.trim());
  const canRun = !busy && !jobRunning && activeStates > 0 && mapReady && htmlReady && api;

  const importSummary = mapReady ? (mapIsJson ? "Verifier map" : "Pathway map") : "No map yet";
  const shotSummary = total ? total + " shots" : (discoveredPages ? discoveredPages + " states" : activeStates + "/state");

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

  const onMapFileChange = async (event) => {
    const file = event.target.files && event.target.files[0];
    if (!file) return;
    setError("");
    try {
      const text = await file.text();
      setMapText(text);
      setMapFileName(file.name);
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
    const trimmedMap = mapText.trim();
    const mapField = mapIsJson ? { map_text: trimmedMap } : { pathway_manifest: trimmedMap };
    const payload = {
      ...mapField,
      base_url: baseUrl.trim(),
      viewports,
      schemes,
      format,
      full_page: fullPage,
      export_mode: exportMode,
      export_dir: exportDir.trim() || null,
      concurrency,
      wait_ms: waitMs,
      name: "capturd-captures",
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
      await api.downloadRestedCapture(job.job_id, (job.name || "capturd-captures") + ".zip");
    } catch (err) {
      setError(err && err.message ? err.message : String(err));
    }
  };

  return (
    <div className="capture">
      <div className="capture__top">
        <div className="capture__heading">
          <div className="capture__eyebrow"><IconCamera size={14} /> RESTED-STATE CAPTURE</div>
          <div className="capture__brand">
            <img className="capture__brand-mark" src="/assets/rhobear-logo.png" alt="Rhobear" />
            <span>RHOBEAR Captur'd</span>
          </div>
          <div className="capture__title-row">
            <h1 className="capture__title">Capture Studio</h1>
            <img className="capture__mascot" src="/assets/rhobear-logo.png" alt="" aria-hidden="true" />
          </div>
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
            <h2>Source</h2>
          </div>

          <label className="capture-field">
            <span>Built HTML — file or folder</span>
            <input
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder="C:\\site\\index.html  or  C:\\site"
              spellCheck={false}
            />
          </label>

          <label className="capture-field">
            <span>Pathway map {mapText.trim() ? (mapIsJson ? "· verifier JSON" : "· markdown") : ""}</span>
            <textarea
              className="capture-urlbox"
              value={mapText}
              onChange={(e) => { setMapText(e.target.value); setMapFileName(""); }}
              placeholder={"Paste the pathway map your agent produced — the markdown table of buttons/states (or verifier JSON)."}
              spellCheck={false}
            />
          </label>
          <div className="capture-inline">
            <button className="btn btn--ghost" type="button" onClick={() => mapInputRef.current && mapInputRef.current.click()}>
              <IconUpload size={14} /> Upload map (.md / .json)
            </button>
            {mapFileName && <span className="capture-muted">{mapFileName}</span>}
          </div>
          <input ref={mapInputRef} className="capture__file" type="file" accept=".md,.markdown,.txt,.json" onChange={onMapFileChange} />
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
            <input value={exportDir} onChange={(e) => setExportDir(e.target.value)} placeholder="Where to drop the shots (local path)" />
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
            {job ? job.message : (canRun ? "Ready" : (htmlReady ? "Paste or upload a pathway map" : "Point at your built HTML"))}
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
                <span className="capture-result__url">{item.pathway_id || item.url}</span>
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
