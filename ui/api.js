(function () {
  const API_BASE = window.location.origin;

  async function fetchJson(path, options) {
    const res = await fetch(API_BASE + path, {
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      ...options,
      body: options && options.body ? JSON.stringify(options.body) : undefined,
    });
    const payload = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(payload.error || payload.detail || (res.status + " " + res.statusText));
    }
    return payload;
  }

  window.SunSponge = {
    async startRestedCapture(body) {
      return fetchJson("/api/rested-captures/jobs", { method: "POST", body });
    },
    async restedCaptureJob(jobId) {
      return fetchJson("/api/rested-captures/jobs/" + encodeURIComponent(jobId), { cache: "no-store" });
    },
    async downloadRestedCapture(jobId, fileName) {
      const url = API_BASE + "/api/rested-captures/jobs/" + encodeURIComponent(jobId) + "/download";
      const res = await fetch(url, { headers: { Accept: "application/zip" } });
      if (!res.ok) {
        const payload = await res.json().catch(() => ({}));
        throw new Error(payload.error || (res.status + " " + res.statusText));
      }
      const blob = await res.blob();
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = fileName || "sunsponge-captures.zip";
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(link.href);
    },
  };
})();