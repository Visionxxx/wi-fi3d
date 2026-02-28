import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

let scene, camera, renderer, controls;
const nodes = new THREE.Group();
const edges = new THREE.Group();
const statusElement = document.getElementById('status');
const detailsContentElement = document.getElementById('details-content');
const zonesContentElement = document.getElementById('zones-content');
const eventsContentElement = document.getElementById('events-content');
const modeSelectElement = document.getElementById('mode-select');
const togglePanelButton = document.getElementById('toggle-panel');
const fullscreenButton = document.getElementById('toggle-fullscreen');
const replayStartButton = document.getElementById('replay-start');
const replayPauseButton = document.getElementById('replay-pause');
const replayResumeButton = document.getElementById('replay-resume');
const replayStopButton = document.getElementById('replay-stop');
const replayResetButton = document.getElementById('replay-reset');
const replayFileSelect = document.getElementById('replay-file');
const replaySeekInput = document.getElementById('replay-seek');
const replayStatusElement = document.getElementById('replay-status');
const nodeMap = new Map();
const raycaster = new THREE.Raycaster();
const pointer = new THREE.Vector2();

let connectedBssid = null;
let connectedSsid = null;
let selectedBssid = null;
let currentMode = modeSelectElement?.value || 'wifi';
let isFetching = false;
let capabilities = null;
let replayStatusTimer = null;
let replayFiles = [];

// --- Colors ---
const COLOR_2_4GHZ = 0x00aaff;
const COLOR_5GHZ = 0x00ff00;
const COLOR_CONNECTED = 0xff00ff;
const COLOR_BLUETOOTH = 0xff9f1c;
const COLOR_CENTER = 0xffffff;

// --- Visualization Constants ---
const MIN_RSSI = -90;
const MAX_RSSI = -30;
const MIN_RADIUS = 2;
const MAX_RADIUS = 40;
const CLIENT_NODE_GRACE_MS = 120000;
const MIN_DISTANCE = 18;
const MAX_DISTANCE = 140;
const NODE_PADDING = 2.0;

// Shared resources
const coreGeometries = {
  default: new THREE.SphereGeometry(0.5, 16, 8),
  beast: new THREE.IcosahedronGeometry(0.65, 0),
};
const shellGeometries = {
  default: new THREE.SphereGeometry(1, 16, 8),
  beast: new THREE.OctahedronGeometry(1.2, 0),
};

const coreMaterials = {
  wifi24: new THREE.MeshStandardMaterial({ color: COLOR_2_4GHZ, emissive: COLOR_2_4GHZ, emissiveIntensity: 0.7 }),
  wifi5: new THREE.MeshStandardMaterial({ color: COLOR_5GHZ, emissive: COLOR_5GHZ, emissiveIntensity: 0.7 }),
  bluetooth: new THREE.MeshStandardMaterial({ color: COLOR_BLUETOOTH, emissive: COLOR_BLUETOOTH, emissiveIntensity: 0.7 }),
  connected: new THREE.MeshStandardMaterial({ color: COLOR_CONNECTED, emissive: COLOR_CONNECTED, emissiveIntensity: 0.8 }),
};

const shellMaterials = {
  wifi24: new THREE.MeshBasicMaterial({ color: COLOR_2_4GHZ, wireframe: true, transparent: true, opacity: 0.5 }),
  wifi5: new THREE.MeshBasicMaterial({ color: COLOR_5GHZ, wireframe: true, transparent: true, opacity: 0.5 }),
  bluetooth: new THREE.MeshBasicMaterial({ color: COLOR_BLUETOOTH, wireframe: true, transparent: true, opacity: 0.5 }),
  connected: new THREE.MeshBasicMaterial({ color: COLOR_CONNECTED, wireframe: true, transparent: true, opacity: 0.55 }),
};

let centerMarker = null;

function geometryMode() {
  return currentMode === 'beast' ? 'beast' : 'default';
}

function clamp01(value) {
  return Math.max(0, Math.min(1, value));
}

function nodeUncertainty(node) {
  if (currentMode !== 'beast') return 0;
  const drift = clamp01(Number(node?.drift_score) || 0);
  const age = Number(node?.last_seen_age_sec) || 0;
  const freshnessPenalty = clamp01(age / 30);
  return clamp01((0.75 * drift) + (0.25 * freshnessPenalty));
}

function buildCoreMaterial(key, uncertainty) {
  if (currentMode !== 'beast') return coreMaterials[key];
  const mat = coreMaterials[key].clone();
  const hot = new THREE.Color(0xff453a);
  mat.color.copy(mat.color.clone().lerp(hot, uncertainty * 0.85));
  mat.emissive.copy(mat.emissive.clone().lerp(hot, uncertainty));
  mat.emissiveIntensity = 0.6 + (uncertainty * 0.55);
  return mat;
}

function buildShellMaterial(key, uncertainty) {
  if (currentMode !== 'beast') return shellMaterials[key];
  const mat = shellMaterials[key].clone();
  const cold = new THREE.Color(0x22c55e);
  const hot = new THREE.Color(0xff453a);
  mat.color.copy(cold.clone().lerp(hot, uncertainty));
  mat.opacity = 0.28 + (uncertainty * 0.45);
  return mat;
}

function disposeDynamicMaterials(nodeGroup) {
  const dynamic = nodeGroup?.userData?.dynamicMaterials || [];
  dynamic.forEach((m) => m?.dispose?.());
}

function mapRssiToRadius(rssi) {
  if (rssi < MIN_RSSI) rssi = MIN_RSSI;
  if (rssi > MAX_RSSI) rssi = MAX_RSSI;
  const percentage = (rssi - MIN_RSSI) / (MAX_RSSI - MIN_RSSI);
  return MIN_RADIUS + percentage * (MAX_RADIUS - MIN_RADIUS);
}

function mapRssiToDistance(rssi) {
  if (rssi < MIN_RSSI) rssi = MIN_RSSI;
  if (rssi > MAX_RSSI) rssi = MAX_RSSI;
  const percentage = (rssi - MIN_RSSI) / (MAX_RSSI - MIN_RSSI);
  return MAX_DISTANCE - percentage * (MAX_DISTANCE - MIN_DISTANCE);
}

function hashString(str) {
  let hash = 2166136261;
  for (let i = 0; i < str.length; i++) {
    hash ^= str.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

function directionFromId(id) {
  const h = hashString(id || 'node');
  const azimuth = (h % 360) * (Math.PI / 180);
  const elevation = (((h >> 9) % 80) - 40) * (Math.PI / 180);
  return { azimuth, elevation };
}

function positionFromPolar(distance, azimuth, elevation) {
  const cosEl = Math.cos(elevation);
  return new THREE.Vector3(
    distance * cosEl * Math.cos(azimuth),
    distance * Math.sin(elevation),
    distance * cosEl * Math.sin(azimuth),
  );
}

function resolveTargetOverlaps(nodeEntries, iterations = 6) {
  for (let pass = 0; pass < iterations; pass++) {
    for (let i = 0; i < nodeEntries.length; i++) {
      for (let j = i + 1; j < nodeEntries.length; j++) {
        const a = nodeEntries[i];
        const b = nodeEntries[j];
        const minDist = (a.radius + b.radius) + NODE_PADDING;
        const delta = new THREE.Vector3().subVectors(a.targetPosition, b.targetPosition);
        let distance = delta.length();

        if (distance >= minDist) continue;
        if (distance < 0.001) {
          delta.set(1, 0, 0);
          distance = 0.001;
        }

        const push = (minDist - distance) * 0.5;
        delta.normalize();

        if (a.isConnected && b.isConnected) {
          continue;
        } else if (a.isConnected) {
          b.targetPosition.add(delta.clone().multiplyScalar(-2 * push));
        } else if (b.isConnected) {
          a.targetPosition.add(delta.clone().multiplyScalar(2 * push));
        } else {
          a.targetPosition.add(delta.clone().multiplyScalar(push));
          b.targetPosition.add(delta.clone().multiplyScalar(-push));
        }
      }
    }
  }
}

function getEdgeCountMap(apEdges) {
  const counts = new Map();
  for (const edge of apEdges) {
    counts.set(edge.source, (counts.get(edge.source) || 0) + 1);
    counts.set(edge.target, (counts.get(edge.target) || 0) + 1);
  }
  return counts;
}

function nodeIsConnected(node) {
  if (!node) return false;
  if (connectedBssid && node.bssid === connectedBssid) return true;
  if (connectedSsid && node.ssid && node.ssid === connectedSsid) return true;
  return false;
}

function materialKeyForNode(node) {
  if (nodeIsConnected(node)) return 'connected';
  if (node.protocol === 'bluetooth') return 'bluetooth';
  return node.band === '5 GHz' ? 'wifi5' : 'wifi24';
}

function formatNodeDetails(node, edgeCount) {
  if (!node) {
    return 'Ingen node valgt.\nKlikk på en kule for å se detaljer.\nRetning i visningen er estimert (ikke fysisk målt azimuth).';
  }

  const protocol = node.protocol === 'bluetooth' ? 'Bluetooth' : 'Wi-Fi';
  const name = node.ssid || '[ukjent]';
  const signal = Number.isFinite(node.rssi) ? `${node.rssi.toFixed(1)} dBm` : 'N/A';
  const channel = node.channel ?? '-';
  const band = node.band || '-';
  const age = Number.isFinite(node.last_seen_age_sec) ? `${node.last_seen_age_sec.toFixed(1)} s` : '-';
  const isConnected = nodeIsConnected(node) ? 'Ja (deg)' : 'Nei';
  const zone = node.zone_id || '-';
  const drift = Number.isFinite(node.drift_score) ? node.drift_score.toFixed(3) : '-';

  return [
    `Nettverk/enhet: ${name}`,
    `ID: ${node.bssid || '-'}`,
    `Type: ${protocol}`,
    `Signal: ${signal}`,
    `Bånd: ${band}`,
    `Kanal: ${channel}`,
    `Koblet til: ${isConnected}`,
    `Sist sett: ${age} siden`,
    `Zone: ${zone}`,
    `Drift score: ${drift}`,
    `Korrelasjoner: ${edgeCount}`,
  ].join('\n');
}

function renderNodeDetails(node, edgeCount = 0) {
  if (!detailsContentElement) return;
  detailsContentElement.textContent = formatNodeDetails(node, edgeCount);
}

function updateNodeDetails(apNodes, apEdges) {
  const edgeCountMap = getEdgeCountMap(apEdges);
  let chosenNode = null;

  if (selectedBssid) {
    chosenNode = apNodes.find((node) => node.bssid === selectedBssid) || null;
  }

  if (!chosenNode && connectedBssid) {
    chosenNode = apNodes.find((node) => node.bssid === connectedBssid) || null;
  }

  if (!chosenNode && connectedSsid) {
    chosenNode = apNodes.find((node) => node.ssid && node.ssid === connectedSsid) || null;
  }

  if (!chosenNode && apNodes.length > 0) {
    chosenNode = [...apNodes].sort((a, b) => (b.rssi ?? -200) - (a.rssi ?? -200))[0];
  }

  if (chosenNode) {
    selectedBssid = chosenNode.bssid;
    renderNodeDetails(chosenNode, edgeCountMap.get(chosenNode.bssid) || 0);
  } else {
    renderNodeDetails(null, 0);
  }
}

function removeNode(bssid) {
  const node = nodeMap.get(bssid);
  if (!node) return;

  disposeDynamicMaterials(node);
  nodes.remove(node);
  nodeMap.delete(bssid);
  if (selectedBssid === bssid) selectedBssid = null;
}

function renderZones(zones = []) {
  if (!zonesContentElement) return;
  if (!zones.length) {
    zonesContentElement.textContent = 'Ingen soner ennå.';
    return;
  }
  zonesContentElement.textContent = zones
    .map((z) => `${z.zone_id} [${z.locked ? 'locked' : 'warming'}]: ${z.members.length} noder`)
    .join('\n');
}

function renderEvents(events = []) {
  if (!eventsContentElement) return;
  if (!events.length) {
    eventsContentElement.textContent = 'Ingen events.';
    return;
  }
  eventsContentElement.textContent = events
    .slice(-8)
    .map((e) => `${e.event_type} ${e.source_id}`)
    .join('\n');
}

function normalizeData(raw) {
  const inputNodes = raw.nodes || [];
  const inputEdges = raw.edges || [];
  const mode = raw.mode || currentMode;

  const nodesNorm = inputNodes.map((n) => {
    if (mode === 'beast') {
      return {
        bssid: n.source_id,
        ssid: n.name || '',
        protocol: n.source_type === 'bluetooth' ? 'bluetooth' : 'wifi',
        band: n.band || '',
        channel: n.channel ?? null,
        rssi: n.smoothed_rssi ?? n.rssi ?? -100,
        last_seen_age_sec: n.last_seen_age_sec ?? 0,
        zone_id: n.zone_id || null,
        drift_score: n.drift_score ?? 0,
      };
    }
    return n;
  });

  const edgesNorm = inputEdges.map((e) => {
    if (mode === 'beast') {
      return {
        source: e.source,
        target: e.target,
        correlation: e.score,
        confidence: e.confidence,
        weight: e.score,
      };
    }
    return {
      source: e.source,
      target: e.target,
      correlation: e.correlation,
      confidence: 0.5,
      weight: Math.abs(e.correlation || 0),
    };
  });

  return {
    mode,
    nodes: nodesNorm,
    edges: edgesNorm,
    zones: raw.zones || [],
    events: raw.events || [],
    metrics: raw.metrics || {},
  };
}

function clearEdges() {
  while (edges.children.length > 0) {
    const edge = edges.children[0];
    if (edge.geometry) edge.geometry.dispose();
    if (edge.material) edge.material.dispose();
    edges.remove(edge);
  }
}

function ensureCenterMarker() {
  if (centerMarker) return;

  const markerGroup = new THREE.Group();
  const markerCore = new THREE.Mesh(
    new THREE.SphereGeometry(1.2, 20, 12),
    new THREE.MeshStandardMaterial({ color: COLOR_CENTER, emissive: COLOR_CENTER, emissiveIntensity: 0.35 }),
  );
  const markerShell = new THREE.Mesh(
    new THREE.SphereGeometry(4.5, 20, 12),
    new THREE.MeshBasicMaterial({ color: COLOR_CENTER, wireframe: true, transparent: true, opacity: 0.18 }),
  );
  markerGroup.add(markerCore);
  markerGroup.add(markerShell);
  markerGroup.position.set(0, 0, 0);
  centerMarker = markerGroup;
  scene.add(centerMarker);
}

function disposeReusableResources() {
  Object.values(coreGeometries).forEach((geometry) => geometry.dispose());
  Object.values(shellGeometries).forEach((geometry) => geometry.dispose());

  Object.values(coreMaterials).forEach((material) => material.dispose());
  Object.values(shellMaterials).forEach((material) => material.dispose());

  if (centerMarker) {
    centerMarker.children.forEach((child) => {
      if (child.geometry) child.geometry.dispose();
      if (child.material) child.material.dispose();
    });
  }
}

function selectedReplayPath() {
  return replayFileSelect?.value || 'backend/replay/sample_walk.ndjson';
}

async function apiReplay(action, params = {}) {
  const qs = new URLSearchParams({ action, ...params });
  const response = await fetch(`/api/replay?${qs.toString()}`);
  if (!response.ok) {
    throw new Error(`Replay API failed: ${response.status}`);
  }
  return response.json();
}

function renderReplayStatus(status) {
  if (!replayStatusElement) return;
  if (!status || status.ok === false) {
    replayStatusElement.textContent = 'Replay: unavailable';
    return;
  }
  const active = status.replay_active ? 'playing' : 'paused/stopped';
  const cursor = Number(status.cursor || 0);
  const size = Number(status.replay_size || 0);
  const pct = size > 0 ? ((cursor / size) * 100).toFixed(1) : '0.0';
  const file = status.path || selectedReplayPath();
  replayStatusElement.textContent = `Replay: ${active} | ${cursor}/${size} (${pct}%) | ${file}`;
  if (replaySeekInput) {
    replaySeekInput.max = String(Math.max(0, size));
    replaySeekInput.value = String(Math.max(0, Math.min(cursor, size)));
  }
}

async function refreshReplayFiles() {
  if (!replayFileSelect) return;
  try {
    const data = await apiReplay('list');
    replayFiles = Array.isArray(data.files) && data.files.length ? data.files : ['backend/replay/sample_walk.ndjson'];
    replayFileSelect.innerHTML = '';
    replayFiles.forEach((path) => {
      const opt = document.createElement('option');
      opt.value = path;
      opt.textContent = path.replace('backend/replay/', '');
      replayFileSelect.appendChild(opt);
    });
    if (replayFiles.includes('backend/replay/sample_walk.ndjson')) {
      replayFileSelect.value = 'backend/replay/sample_walk.ndjson';
    }
  } catch (error) {
    console.error('Could not fetch replay files:', error);
  }
}

async function refreshReplayStatus() {
  try {
    const status = await apiReplay('status');
    renderReplayStatus(status);
  } catch (error) {
    console.error('Could not fetch replay status:', error);
  }
}

function startReplayStatusPolling() {
  if (replayStatusTimer) clearInterval(replayStatusTimer);
  replayStatusTimer = setInterval(refreshReplayStatus, 1000);
}

function togglePanel() {
  document.body.classList.toggle('panel-collapsed');
  if (togglePanelButton) {
    togglePanelButton.textContent = document.body.classList.contains('panel-collapsed') ? 'Show Panel' : 'Hide Panel';
  }
}

async function toggleFullscreen() {
  if (!document.fullscreenElement) {
    await document.documentElement.requestFullscreen();
    if (fullscreenButton) fullscreenButton.textContent = 'Exit Fullscreen';
  } else {
    await document.exitFullscreen();
    if (fullscreenButton) fullscreenButton.textContent = 'Fullscreen';
  }
}

async function init() {
  console.log('Initializing wiremesh signal visualization...');
  await fetchCapabilities();
  await refreshReplayFiles();
  await refreshReplayStatus();
  await fetchConnectionInfo();

  scene = new THREE.Scene();
  camera = new THREE.PerspectiveCamera(75, window.innerWidth / window.innerHeight, 0.1, 1000);
  camera.position.set(0, 75, 160);

  const canvas = document.querySelector('#c');
  renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
  renderer.setSize(window.innerWidth, window.innerHeight);
  renderer.setPixelRatio(window.devicePixelRatio);

  scene.fog = new THREE.Fog(0x000000, 1, 380);
  scene.add(new THREE.AmbientLight(0x505050));

  const directionalLight = new THREE.DirectionalLight(0xffffff, 0.65);
  directionalLight.position.set(40, 80, 40);
  scene.add(directionalLight);

  controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.target.set(0, 0, 0);

  scene.add(nodes);
  scene.add(edges);
  ensureCenterMarker();

  if (modeSelectElement) {
    modeSelectElement.addEventListener('change', onModeChanged);
  }
  if (togglePanelButton) {
    togglePanelButton.addEventListener('click', togglePanel);
  }
  if (fullscreenButton) {
    fullscreenButton.addEventListener('click', toggleFullscreen);
  }
  if (replayStartButton) {
    replayStartButton.addEventListener('click', async () => {
      const status = await apiReplay('start', { path: selectedReplayPath() });
      renderReplayStatus(status);
    });
  }
  if (replayPauseButton) {
    replayPauseButton.addEventListener('click', async () => {
      const status = await apiReplay('stop');
      renderReplayStatus(status);
    });
  }
  if (replayResumeButton) {
    replayResumeButton.addEventListener('click', async () => {
      const status = await apiReplay('resume');
      renderReplayStatus(status);
    });
  }
  if (replayStopButton) {
    replayStopButton.addEventListener('click', async () => {
      await apiReplay('stop');
      const status = await apiReplay('seek', { index: 0 });
      renderReplayStatus(status);
    });
  }
  if (replayResetButton) {
    replayResetButton.addEventListener('click', async () => {
      await apiReplay('reset');
      await refreshReplayStatus();
    });
  }
  if (replaySeekInput) {
    replaySeekInput.addEventListener('change', async () => {
      const idx = Number(replaySeekInput.value || 0);
      const status = await apiReplay('seek', { index: String(idx) });
      renderReplayStatus(status);
    });
  }
  if (replayFileSelect) {
    replayFileSelect.addEventListener('change', async () => {
      const status = await apiReplay('start', { path: selectedReplayPath() });
      renderReplayStatus(status);
    });
  }

  renderer.domElement.addEventListener('pointerdown', onPointerDown);
  window.addEventListener('resize', onWindowResize, false);
  window.addEventListener('beforeunload', disposeReusableResources);
  window.addEventListener('beforeunload', () => {
    if (replayStatusTimer) clearInterval(replayStatusTimer);
  });

  animate();
  startReplayStatusPolling();
  renderNodeDetails(null, 0);
  renderZones([]);
  renderEvents([]);
  fetchData();
  setInterval(fetchData, 5000);
}

async function fetchCapabilities() {
  try {
    const response = await fetch('/api/capabilities');
    if (!response.ok) return;
    capabilities = await response.json();
    const supported = new Set(capabilities?.supported_modes || ['wifi', 'beast']);
    if (modeSelectElement) {
      [...modeSelectElement.options].forEach((opt) => {
        if (!supported.has(opt.value)) {
          opt.remove();
        }
      });
      if (!supported.has(currentMode)) {
        currentMode = supported.has('beast') ? 'beast' : 'wifi';
        modeSelectElement.value = currentMode;
      }
    }
  } catch (error) {
    console.error('Could not fetch capabilities:', error);
  }
}

function onPointerDown(event) {
  if (!renderer || !camera) return;

  const rect = renderer.domElement.getBoundingClientRect();
  pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
  pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;

  raycaster.setFromCamera(pointer, camera);
  const intersections = raycaster.intersectObjects(nodes.children, true);
  if (intersections.length === 0) return;

  let obj = intersections[0].object;
  while (obj && !obj.userData?.bssid) {
    obj = obj.parent;
  }
  if (!obj || !obj.userData?.bssid) return;

  selectedBssid = obj.userData.bssid;
  const edgeCountMap = getEdgeCountMap(
    edges.children
      .map((line) => ({
        source: line.userData?.source?.userData?.bssid,
        target: line.userData?.target?.userData?.bssid,
      }))
      .filter((e) => e.source && e.target),
  );
  renderNodeDetails(obj.userData, edgeCountMap.get(selectedBssid) || 0);
}

function animate() {
  requestAnimationFrame(animate);
  updateLayout();
  controls.update();
  renderer.render(scene, camera);
}

function updateLayout() {
  for (const node of nodeMap.values()) {
    if (node.userData?.targetPosition) {
      node.position.lerp(node.userData.targetPosition, 0.12);
    }
  }

  edges.children.forEach((edge) => {
    const sourceNode = edge.userData.source;
    const targetNode = edge.userData.target;
    if (!sourceNode || !targetNode) return;
    edge.geometry.setFromPoints([sourceNode.position, targetNode.position]);
  });
}

function clearSceneData() {
  for (const bssid of [...nodeMap.keys()]) {
    removeNode(bssid);
  }
  clearEdges();
  selectedBssid = null;
  renderNodeDetails(null, 0);
}

async function onModeChanged() {
  currentMode = modeSelectElement?.value || 'wifi';
  connectedBssid = null;
  connectedSsid = null;
  clearSceneData();
  await fetchConnectionInfo();
  await fetchData();
}

async function fetchConnectionInfo() {
  try {
    const response = await fetch(`/api/connection?mode=${encodeURIComponent(currentMode)}`);
    const data = await response.json();
    connectedBssid = data.connected_bssid || null;
    connectedSsid = data.connected_ssid || null;
  } catch (error) {
    console.error('Could not fetch connection info:', error);
  }
}

async function fetchData() {
  if (!statusElement) return;
  if (isFetching) return;

  isFetching = true;
  statusElement.textContent = `Scanning and analyzing (${currentMode})...`;

  try {
    const response = await fetch(`/api/data?mode=${encodeURIComponent(currentMode)}`);
    if (!response.ok) throw new Error(`HTTP error! Status: ${response.status}`);
    const data = await response.json();
    updateScene(normalizeData(data));
  } catch (error) {
    console.error('Fetch failed:', error);
    statusElement.textContent = 'Error: Could not fetch data. Check console.';
  } finally {
    isFetching = false;
  }
}

function updateScene(data) {
  const apNodes = data.nodes || [];
  const apEdges = data.edges || [];
  const nowMs = Date.now();
  const incomingBssids = new Set(apNodes.map((n) => n.bssid));

  for (const [bssid, nodeObj] of nodeMap.entries()) {
    if (incomingBssids.has(bssid)) continue;
    const lastClientSeen = nodeObj.userData?.clientLastSeenMs || 0;
    if (nowMs - lastClientSeen > CLIENT_NODE_GRACE_MS) {
      removeNode(bssid);
    }
  }

  const desiredTargets = [];

  apNodes.forEach((ap) => {
    const key = materialKeyForNode(ap);
    const radius = mapRssiToRadius(ap.rssi);
    const isConnected = nodeIsConnected(ap);

    let targetPosition;
    if (isConnected) {
      targetPosition = new THREE.Vector3(0, 0, 0);
    } else {
      const { azimuth, elevation } = directionFromId(ap.bssid);
      const distance = mapRssiToDistance(ap.rssi);
      targetPosition = positionFromPolar(distance, azimuth, elevation);
    }

    desiredTargets.push({
      bssid: ap.bssid,
      targetPosition: targetPosition.clone(),
      radius,
      isConnected,
    });
  });

  resolveTargetOverlaps(desiredTargets);
  const targetByBssid = new Map(desiredTargets.map((entry) => [entry.bssid, entry]));

  apNodes.forEach((ap) => {
    const existing = nodeMap.get(ap.bssid);
    if (existing && existing.userData?.renderMode !== currentMode) {
      removeNode(ap.bssid);
    }
  });

  apNodes.forEach((ap) => {
    const key = materialKeyForNode(ap);
    const targetData = targetByBssid.get(ap.bssid);
    const radius = targetData ? targetData.radius : mapRssiToRadius(ap.rssi);
    const targetPosition = targetData ? targetData.targetPosition : new THREE.Vector3(0, 0, 0);
    const uncertainty = nodeUncertainty(ap);
    const renderMode = currentMode;
    const shapeMode = geometryMode();
    const coreMaterial = buildCoreMaterial(key, uncertainty);
    const shellMaterial = buildShellMaterial(key, uncertainty);
    const dynamicMaterials = [];
    if (coreMaterial !== coreMaterials[key]) dynamicMaterials.push(coreMaterial);
    if (shellMaterial !== shellMaterials[key]) dynamicMaterials.push(shellMaterial);

    if (nodeMap.has(ap.bssid)) {
      const apGroup = nodeMap.get(ap.bssid);
      const core = apGroup.children[0];
      const signalMesh = apGroup.children[1];

      disposeDynamicMaterials(apGroup);
      core.material = coreMaterial;
      signalMesh.material = shellMaterial;
      core.geometry = coreGeometries[shapeMode];
      signalMesh.geometry = shellGeometries[shapeMode];
      signalMesh.scale.lerp(new THREE.Vector3(radius, radius, radius), 0.2);

      apGroup.userData = {
        ...ap,
        clientLastSeenMs: nowMs,
        targetPosition,
        currentRadius: radius,
        uncertainty,
        renderMode,
        dynamicMaterials,
      };
    } else {
      const apGroup = new THREE.Group();
      const core = new THREE.Mesh(coreGeometries[shapeMode], coreMaterial);
      const signalMesh = new THREE.Mesh(shellGeometries[shapeMode], shellMaterial);
      signalMesh.scale.set(radius, radius, radius);

      apGroup.add(core);
      apGroup.add(signalMesh);

      const spawnPos = targetPosition.clone().multiplyScalar(0.85);
      apGroup.position.copy(spawnPos);
      apGroup.userData = {
        ...ap,
        clientLastSeenMs: nowMs,
        targetPosition,
        currentRadius: radius,
        uncertainty,
        renderMode,
        dynamicMaterials,
      };

      nodes.add(apGroup);
      nodeMap.set(ap.bssid, apGroup);
    }
  });

  clearEdges();

  apEdges.forEach((edge) => {
    const sourceNode = nodeMap.get(edge.source);
    const targetNode = nodeMap.get(edge.target);
    if (!sourceNode || !targetNode) return;

    const lineGeometry = new THREE.BufferGeometry().setFromPoints([
      sourceNode.position,
      targetNode.position,
    ]);
    const confidence = Math.max(0, Math.min(1, edge.confidence || 0.5));
    const uncertainty = 1 - confidence;
    const opacity = Math.max(0.12, Math.min(0.95, confidence * 0.95));
    const hue = Math.max(0.0, Math.min(0.33, (edge.weight || 0) * 0.33));
    const defaultColor = new THREE.Color().setHSL(hue, 0.9, 0.6);
    const beastColor = new THREE.Color(0x22c55e).lerp(new THREE.Color(0xff453a), uncertainty);
    const edgeMat = new THREE.LineBasicMaterial({
      color: currentMode === 'beast' ? beastColor : defaultColor,
      transparent: true,
      opacity,
    });
    const line = new THREE.Line(lineGeometry, edgeMat);
    line.userData = { source: sourceNode, target: targetNode, correlation: edge.correlation, confidence: edge.confidence };
    edges.add(line);
  });

  updateNodeDetails(apNodes, apEdges);
  renderZones(data.zones || []);
  renderEvents(data.events || []);
  const metrics = data.metrics || {};
  if (currentMode === 'beast') {
    const avgConf = Number(metrics.avg_edge_confidence || 0).toFixed(2);
    const avgDrift = Number(metrics.avg_drift_score || 0).toFixed(2);
    const stale = Number(metrics.stale_node_count || 0);
    const replayActive = Boolean(metrics.replay_active);
    if (!replayActive && nodes.children.length === 0) {
      statusElement.textContent = 'No live nodes. Start Replay (sample/live capture) or free the Wi-Fi interface.';
    } else {
      statusElement.textContent = `Rendered ${nodes.children.length} nodes / ${edges.children.length} edges (beast). conf=${avgConf}, drift=${avgDrift}, stale=${stale}`;
    }
  } else {
    statusElement.textContent = `Rendered ${nodes.children.length} nodes and ${edges.children.length} edges (${currentMode}).`;
  }
}

function onWindowResize() {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
}

init();
