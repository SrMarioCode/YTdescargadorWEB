const urlForm = document.getElementById("url-form");
const urlInput = document.getElementById("youtube-url");
const btnAnalyze = document.getElementById("btn-analyze");
const btnQuickDownload = document.getElementById("btn-quick-download");
const btnDownloadSelected = document.getElementById("btn-download-selected");
const alertBox = document.getElementById("alert-box");
const previewEl = document.getElementById("video-preview");
const previewThumb = document.getElementById("preview-thumb");
const previewDuration = document.getElementById("preview-duration");
const previewTitle = document.getElementById("preview-title");
const previewChannel = document.getElementById("preview-channel");
const qualitySelect = document.getElementById("quality-select");
const progressPanel = document.getElementById("progress-panel");
const progressLabel = document.getElementById("progress-label");
const progressMeta = document.getElementById("progress-meta");
const progressBar = document.getElementById("progress-bar");

let pollTimer = null;

function showAlert(message, type = "error") {
  alertBox.textContent = message;
  alertBox.className = `alert ${type === "success" ? "success" : ""}`;
}

function clearAlert() {
  alertBox.className = "alert hidden";
  alertBox.textContent = "";
}

function setBusy(busy) {
  btnAnalyze.disabled = busy;
  btnQuickDownload.disabled = busy;
  btnDownloadSelected.disabled = busy;
}

function updateProgress(label, percent, extra = "") {
  progressPanel.classList.remove("hidden");
  progressLabel.textContent = label;
  progressMeta.textContent = extra ? `${percent}% · ${extra}` : `${percent}%`;
  progressBar.style.width = `${Math.max(0, Math.min(100, percent))}%`;
}

function hideProgress() {
  progressPanel.classList.add("hidden");
}

async function analyzeVideo(url) {
  clearAlert();
  hideProgress();
  setBusy(true);
  btnAnalyze.textContent = "Analizando...";

  try {
    const res = await fetch("/api/info", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || "No se pudo analizar el video.");
    }

    previewThumb.src = data.thumbnail || "";
    previewDuration.textContent = data.duration || "";
    previewTitle.textContent = data.title || "Video de YouTube";
    previewChannel.textContent = data.channel || "";

    qualitySelect.innerHTML = "";
    for (const q of data.qualities || []) {
      const opt = document.createElement("option");
      opt.value = q.id;
      opt.textContent = q.label;
      qualitySelect.appendChild(opt);
    }

    previewEl.classList.remove("hidden");
  } catch (err) {
    previewEl.classList.add("hidden");
    showAlert(err.message || "Error inesperado al analizar el enlace.");
  } finally {
    setBusy(false);
    btnAnalyze.textContent = "Analizar";
  }
}

async function startDownload(url, quality = "best_mp4") {
  clearAlert();
  if (!url.trim()) {
    showAlert("Por favor, pega un enlace de YouTube primero.");
    return;
  }

  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }

  setBusy(true);
  updateProgress("Iniciando descarga...", 5);

  try {
    const res = await fetch("/api/download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: url.trim(), quality }),
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || "No se pudo iniciar la descarga.");
    }

    const jobId = data.job_id;
    pollTimer = setInterval(async () => {
      try {
        const statusRes = await fetch(`/api/status/${jobId}`);
        const st = await statusRes.json();
        if (!statusRes.ok) {
          throw new Error(st.detail || "Error consultando estado.");
        }

        if (st.status === "queued") {
          updateProgress("En cola...", 8);
        } else if (st.status === "downloading") {
          const extras = [st.speed, st.eta ? `ETA ${st.eta}` : ""].filter(Boolean).join(" · ");
          updateProgress("Descargando desde YouTube...", st.progress || 10, extras);
        } else if (st.status === "processing") {
          updateProgress("Procesando y combinando audio/video...", 99);
        } else if (st.status === "ready") {
          clearInterval(pollTimer);
          pollTimer = null;
          updateProgress("¡Listo! Enviando archivo al navegador...", 100);
          showAlert(`Descarga lista: ${st.filename}`, "success");
          window.location.href = `/api/file/${jobId}`;
          setBusy(false);
        } else if (st.status === "error") {
          clearInterval(pollTimer);
          pollTimer = null;
          hideProgress();
          showAlert(st.error || "Ocurrió un error durante la descarga.");
          setBusy(false);
        }
      } catch (pollErr) {
        clearInterval(pollTimer);
        pollTimer = null;
        hideProgress();
        showAlert(pollErr.message || "Se perdió la conexión con el servidor.");
        setBusy(false);
      }
    }, 900);
  } catch (err) {
    hideProgress();
    showAlert(err.message || "Error al iniciar la descarga.");
    setBusy(false);
  }
}

urlForm.addEventListener("submit", (e) => {
  e.preventDefault();
  analyzeVideo(urlInput.value);
});

btnQuickDownload.addEventListener("click", () => {
  startDownload(urlInput.value, "best_mp4");
});

btnDownloadSelected.addEventListener("click", () => {
  const selectedQuality = qualitySelect.value || "best_mp4";
  startDownload(urlInput.value, selectedQuality);
});
