let currentMode = 'in_place';
let currentSpeed = 'whisper';

function setMode(mode) {
  currentMode = mode;
  document.getElementById('modeInPlace').classList.toggle('active', mode === 'in_place');
  document.getElementById('modeSafeCopy').classList.toggle('active', mode === 'safe_copy');
  const srcRow = document.getElementById('safeCopySourceRow');
  if (srcRow) {
    srcRow.style.display = mode === 'safe_copy' ? 'flex' : 'none';
  }
  runPreflight();
}

function setSpeed(speed) {
  currentSpeed = speed;
  document.getElementById('speedWhisper').classList.toggle('active', speed === 'whisper');
  document.getElementById('speedTurbo').classList.toggle('active', speed === 'turbo');
}

async function browseFolder(target = 'archive') {
  const res = await fetch('/api/browse-folder', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ target })
  });
  const data = await res.json();
  if (data.path) {
    const inputId = target === 'source' ? 'sourcePath' : 'archivePath';
    document.getElementById(inputId).value = data.path;
    await runPreflight();
  }
}

function getSelectedSourceDirs() {
  if (currentMode !== 'safe_copy') return [];
  const raw = (document.getElementById('sourcePath')?.value || '').trim();
  return raw ? raw.split(',').map(s => s.trim()).filter(Boolean) : [];
}

async function runPreflight() {
  const archivePath = document.getElementById('archivePath').value.trim();
  const res = await fetch('/api/preflight', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      archive_path: archivePath || null,
      mode: currentMode,
      source_dirs: getSelectedSourceDirs()
    })
  });
  const data = await res.json();
  renderDiagnostics(data);
  if (data.archive) {
    renderArchiveMetrics(data.archive);
  }
}

function renderDiagnostics(d) {
  const disk = d.disk || {};
  const diskBadge = document.getElementById('diagDiskBadge');
  diskBadge.className = disk.ok ? 'badge-ok' : 'badge-err';
  diskBadge.innerText = disk.free_gb !== null ? (disk.ok ? 'PASSED' : 'LOW DISK') : 'Pending';
  document.getElementById('diagDiskSub').innerText = disk.message || '';
  if (disk.free_gb !== null) {
    document.getElementById('teleDisk').innerText = `${disk.free_gb} GiB`;
  }

  const ol = d.ollama || {};
  const olBadge = document.getElementById('diagOllamaBadge');
  if (ol.running && ol.target_model_ready) {
    olBadge.className = 'badge-ok';
    olBadge.innerText = 'READY';
    document.getElementById('diagOllamaSub').innerText = `${ol.target_model} active at localhost:11434`;
  } else if (ol.running) {
    olBadge.className = 'badge-warn';
    olBadge.innerText = 'MODEL MISSING';
    document.getElementById('diagOllamaSub').innerText = `Ollama running; pull ${ol.target_model}`;
  } else {
    olBadge.className = 'badge-err';
    olBadge.innerText = ol.binary_installed ? 'STOPPED' : 'NOT INSTALLED';
    document.getElementById('diagOllamaSub').innerText = ol.binary_installed ? 'Click Start Ollama to launch daemon.' : 'Install Ollama from ollama.com';
  }

  const bio = d.biometrics || {};
  const bioBadge = document.getElementById('diagBioBadge');
  const btnInstallBio = document.getElementById('btnInstallBio');
  const providers = (d.onnx_providers || []).join(', ') || 'None';
  if (bio.package_installed && bio.weights_cached) {
    bioBadge.className = 'badge-ok';
    bioBadge.innerText = 'READY';
    if (btnInstallBio) btnInstallBio.style.display = 'none';
  } else if (bio.package_installed) {
    bioBadge.className = 'badge-warn';
    bioBadge.innerText = 'ON FIRST RUN';
    if (btnInstallBio) btnInstallBio.style.display = 'none';
  } else {
    bioBadge.className = 'badge-warn';
    bioBadge.innerText = 'OPTIONAL';
    if (btnInstallBio) btnInstallBio.style.display = 'inline-flex';
  }
  document.getElementById('diagBioSub').innerText = `Providers: ${providers}`;

  const tools = d.tools || {};
  const tBadge = document.getElementById('diagToolsBadge');
  const exifReady = tools.exiftool?.installed;
  const heicReady = tools.sips?.heic_ready;
  tBadge.className = (exifReady && heicReady) ? 'badge-ok' : 'badge-warn';
  tBadge.innerText = (exifReady && heicReady) ? 'READY' : 'FALLBACK ACTIVE';
  document.getElementById('diagToolsSub').innerText =
    `ExifTool: ${exifReady ? 'Yes' : 'Pillow Fallback'} | HEIC: ${heicReady ? 'Ready' : 'Standard Formats'}`;
}

function renderArchiveMetrics(a) {
  document.getElementById('mTotal').innerText = (a.total_items || 0).toLocaleString();
  document.getElementById('mPhotos').innerText = (a.photos || 0).toLocaleString();
  document.getElementById('mGeocoded').innerText = (a.geocoded || 0).toLocaleString();
  document.getElementById('mFaces').innerText = (a.faces_detected || 0).toLocaleString();
  document.getElementById('mCaptioned').innerText = (a.ai_captioned || 0).toLocaleString();
  document.getElementById('mHw').innerText = (a.homework_flagged || 0).toLocaleString();
  if (a.free_disk_gb !== undefined) {
    document.getElementById('teleDisk').innerText = `${a.free_disk_gb} GiB`;
  }
  const museumBtn = document.getElementById('btnOpenMuseum');
  museumBtn.style.opacity = a.has_viewer ? '1' : '0.5';
}

function renderPipelineState(s) {
  const running = Boolean(s.running);
  document.getElementById('btnStartAll').disabled = running;
  document.getElementById('btnResumeVision').disabled = running;
  document.getElementById('btnStop').disabled = !running;

  document.getElementById('teleStage').innerText = s.stage || 'idle';
  document.getElementById('teleRam').innerText = s.llama_rss_mb ? `${Math.round(s.llama_rss_mb)} MB` : 'Idle';

  document.getElementById('progressMsg').innerText = s.message || 'Ready';
  document.getElementById('progressStage').innerText = `Stage: ${s.stage || 'idle'}`;
  const cur = s.current || 0;
  const tot = s.total || 0;
  document.getElementById('progressCounts').innerText = `${cur.toLocaleString()} / ${tot.toLocaleString()}`;
  const pct = tot > 0 ? Math.min(100, Math.round((cur / tot) * 100)) : (s.stage === 'complete' ? 100 : 0);
  document.getElementById('progressFill').style.width = `${pct}%`;
  document.getElementById('progressRate').innerText = s.rate_img_s ? `${s.rate_img_s} img/s` : '';

  if (s.pull && s.pull.running) {
    const pct = s.pull.total ? ` (${Math.round((s.pull.completed / s.pull.total) * 100)}%)` : '';
    document.getElementById('diagOllamaSub').innerText = `Pulling model: ${s.pull.status}${pct}`;
  }
  if (s.bio_install && s.bio_install.running) {
    document.getElementById('diagBioSub').innerText = s.bio_install.status || 'Installing Face AI...';
  }

  if (s.archive) renderArchiveMetrics(s.archive);
}

async function startOllama() {
  const res = await fetch('/api/setup/start-ollama', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
  await res.json();
  await runPreflight();
}

async function pullMoondream() {
  await fetch('/api/setup/pull-model', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ model: 'moondream:latest' }) });
  document.getElementById('diagOllamaSub').innerText = 'Downloading moondream:latest in background...';
}

async function installBiometrics() {
  await fetch('/api/setup/install-biometrics', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
  document.getElementById('diagBioSub').innerText = 'Installing InsightFace & ONNX Runtime in background...';
}

async function startPipeline(stages = null) {
  const archivePath = document.getElementById('archivePath').value.trim();
  if (!archivePath) { alert('Please select or enter an archive folder first.'); return; }
  const sourceDirs = getSelectedSourceDirs();
  if (currentMode === 'safe_copy' && sourceDirs.length === 0) {
    alert('Safe Copy Mode requires selecting a Source Photos Folder to copy from.');
    return;
  }
  const res = await fetch('/api/pipeline/start', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      archive_path: archivePath,
      source_dirs: sourceDirs,
      mode: currentMode,
      speed_mode: currentSpeed,
      enable_biometrics: document.getElementById('chkBiometrics').checked,
      enable_vision: document.getElementById('chkVision').checked,
      stages: stages
    })
  });
  const data = await res.json();
  if (!res.ok) {
    alert(data.error || 'Could not start pipeline.');
    return;
  }
  if (data.status) renderPipelineState(data.status);
}

function startVisionOnly() {
  startPipeline(['stage4_vision', 'stage5']);
}

async function stopPipeline() {
  const res = await fetch('/api/pipeline/stop', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
  const data = await res.json();
  if (data.status) renderPipelineState(data.status);
}

async function syncMuseum() {
  const archivePath = document.getElementById('archivePath').value.trim();
  if (!archivePath) { alert('Select an archive folder first.'); return; }
  const res = await fetch('/api/pipeline/sync', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ archive_path: archivePath })
  });
  const data = await res.json();
  if (data.archive) renderArchiveMetrics(data.archive);
  document.getElementById('progressMsg').innerText = 'Museum FTS5 & catalog_data.js synced!';
}

async function quarantineHomework() {
  const archivePath = document.getElementById('archivePath').value.trim();
  if (!archivePath) { alert('Select an archive folder first.'); return; }
  const res = await fetch('/api/homework/quarantine', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ archive_path: archivePath })
  });
  const data = await res.json();
  if (data.ok) {
    document.getElementById('progressMsg').innerText = `Moved ${data.moved_count} flagged document(s) to content/documents/homework_archive/.`;
    await runPreflight();
  }
}

window.addEventListener('DOMContentLoaded', async () => {
  const statusRes = await fetch('/api/status');
  const status = await statusRes.json();
  if (status.selected_archive) {
    document.getElementById('archivePath').value = status.selected_archive;
  }
  renderPipelineState(status);
  await runPreflight();

  const evtSource = new EventSource('/api/pipeline/events');
  evtSource.onmessage = (event) => {
    try {
      const payload = JSON.parse(event.data);
      renderPipelineState(payload);
    } catch (_) {}
  };

  const closeStream = () => {
    try {
      evtSource.close();
    } catch (_) {}
  };
  window.addEventListener('pagehide', closeStream);
  window.addEventListener('beforeunload', closeStream);
});
