/**
 * Image-to-Pattern CAD Studio — Interactive Web CAD Engine & Application
 */

(function () {
  'use strict';

  // =========================================================================
  // State
  // =========================================================================

  const state = {
    project: null,
    selectedPieceId: null,
    selectedSegIdx: null,
    
    // Camera Transform (World coordinates in mm -> Screen coordinates in px)
    camera: {
      x: 100, // screen px offset
      y: 400, // screen px offset (Y-up)
      zoom: 1.2, // px per mm
    },
    
    // Drag / Pan interaction state
    isPanning: false,
    panStart: { x: 0, y: 0 },
    spacePressed: false,
    
    // View Options
    showHandles: false,
    showSeamPreview: true,
    activeTab: 'cad',
    
    // Nesting
    nestingMode: 'hybrid',
    nestingData: null,
  };

  // DOM Elements
  const canvas = document.getElementById('cad-canvas');
  const ctx = canvas.getContext('2d');
  const projectTitleEl = document.getElementById('project-title');
  const piecesCountBadge = document.getElementById('pieces-count-badge');
  const piecesListEl = document.getElementById('pieces-list');
  const presetSelector = document.getElementById('preset-selector');
  const globalSeamSlider = document.getElementById('global-seam-slider');
  const globalSeamVal = document.getElementById('global-seam-val');
  const btnApplyGlobalSeam = document.getElementById('btn-apply-global-seam');
  const lblCursorCoords = document.getElementById('lbl-cursor-coords');
  const lblZoomLevel = document.getElementById('lbl-zoom-level');
  const exportDropdownBtn = document.getElementById('btn-export-dropdown');
  const exportMenu = document.getElementById('export-menu');
  const loadingOverlay = document.getElementById('loading-overlay');
  const loadingMsg = document.getElementById('loading-msg');

  // Inspector Elements
  const inspPieceName = document.getElementById('insp-piece-name');
  const inspPieceId = document.getElementById('insp-piece-id');
  const inspNetDims = document.getElementById('insp-net-dims');
  const inspCutDims = document.getElementById('insp-cut-dims');
  const inspPieceSeamSlider = document.getElementById('insp-piece-seam-slider');
  const inspPieceSeamVal = document.getElementById('insp-piece-seam-val');
  const btnSavePieceSeam = document.getElementById('btn-save-piece-seam');
  const btnUnfoldPiece = document.getElementById('btn-unfold-piece');
  const btnMirrorPiece = document.getElementById('btn-mirror-piece');
  const inpScaleW = document.getElementById('inp-scale-w');
  const inpScaleH = document.getElementById('inp-scale-h');
  const btnApplyScale = document.getElementById('btn-apply-anisotropic-scale');
  const inspSegIdx = document.getElementById('insp-seg-idx');
  const inspEdgeRole = document.getElementById('insp-edge-role');
  const inspEdgeSeamOverride = document.getElementById('insp-edge-seam-override');
  const btnSaveEdgeRole = document.getElementById('btn-save-edge-role');

  // Digitizer Modal Elements
  const btnOpenDigitizer = document.getElementById('btn-open-digitizer');
  const digitizerModal = document.getElementById('digitizer-modal');
  const btnCloseDigitizerModal = document.getElementById('btn-close-digitizer-modal');
  const btnCancelDigitizer = document.getElementById('btn-cancel-digitizer');
  const digitizerForm = document.getElementById('digitizer-form');
  const dropArea = document.getElementById('drop-area');
  const digitizerFileInput = document.getElementById('digitizer-file-input');
  const selectedFileName = document.getElementById('selected-file-name');

  // =========================================================================
  // Coordinate Space Transforms (CAD mm -> Canvas px)
  // CAD coordinates: Origin (0,0) at bottom-left, +X right, +Y UP.
  // Canvas screen: Origin (0,0) at top-left, +X right, +Y DOWN.
  // =========================================================================

  function worldToScreen(wx, wy) {
    return {
      x: state.camera.x + wx * state.camera.zoom,
      y: state.camera.y - wy * state.camera.zoom, // invert Y
    };
  }

  function screenToWorld(sx, sy) {
    return {
      x: (sx - state.camera.x) / state.camera.zoom,
      y: (state.camera.y - sy) / state.camera.zoom, // invert Y
    };
  }

  // =========================================================================
  // Canvas Sizing & Rendering
  // =========================================================================

  function resizeCanvas() {
    const parent = canvas.parentElement;
    const rect = parent.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    canvas.style.width = `${rect.width}px`;
    canvas.style.height = `${rect.height}px`;
    
    ctx.scale(dpr, dpr);
    renderCanvas();
  }

  function renderGrid() {
    const parent = canvas.parentElement;
    const w = parent.clientWidth;
    const h = parent.clientHeight;

    // Draw dark background
    ctx.fillStyle = '#0b1120';
    ctx.fillRect(0, 0, w, h);

    const zoom = state.camera.zoom;
    // Determine grid step in mm based on zoom
    let stepMm = 50;
    if (zoom > 3.0) stepMm = 10;
    else if (zoom < 0.5) stepMm = 100;
    else if (zoom < 0.2) stepMm = 200;

    const topLeft = screenToWorld(0, 0);
    const bottomRight = screenToWorld(w, h);

    const startX = Math.floor(topLeft.x / stepMm) * stepMm;
    const endX = Math.ceil(bottomRight.x / stepMm) * stepMm;
    const startY = Math.floor(bottomRight.y / stepMm) * stepMm;
    const endY = Math.ceil(topLeft.y / stepMm) * stepMm;

    // Render minor / major grid lines
    ctx.lineWidth = 1;
    ctx.strokeStyle = '#1e293b';
    ctx.beginPath();
    for (let x = startX; x <= endX; x += stepMm) {
      const s = worldToScreen(x, 0);
      ctx.moveTo(s.x, 0);
      ctx.lineTo(s.x, h);
    }
    for (let y = startY; y <= endY; y += stepMm) {
      const s = worldToScreen(0, y);
      ctx.moveTo(0, s.y);
      ctx.lineTo(w, s.y);
    }
    ctx.stroke();

    // Render Origin axes (0,0)
    const origin = worldToScreen(0, 0);
    ctx.lineWidth = 1.5;
    
    // X axis (Red)
    ctx.strokeStyle = 'rgba(239, 68, 68, 0.5)';
    ctx.beginPath();
    ctx.moveTo(origin.x, origin.y);
    ctx.lineTo(origin.x + 80, origin.y);
    ctx.stroke();

    // Y axis (Green)
    ctx.strokeStyle = 'rgba(34, 197, 94, 0.5)';
    ctx.beginPath();
    ctx.moveTo(origin.x, origin.y);
    ctx.lineTo(origin.x, origin.y - 80);
    ctx.stroke();

    // Origin label
    ctx.fillStyle = '#64748b';
    ctx.font = '10px "JetBrains Mono", monospace';
    ctx.fillText('(0,0) CAD', origin.x + 5, origin.y + 12);
  }

  function renderContour(contour, isClosed, strokeColor, fillColor, lineDash = [], lineWidth = 1.5) {
    if (!contour || !contour.segments || contour.segments.length === 0) return;

    ctx.save();
    ctx.strokeStyle = strokeColor;
    ctx.lineWidth = lineWidth;
    ctx.setLineDash(lineDash);
    if (fillColor) ctx.fillStyle = fillColor;

    ctx.beginPath();
    const segs = contour.segments;
    const startPt = worldToScreen(segs[0].start.x, segs[0].start.y);
    ctx.moveTo(startPt.x, startPt.y);

    for (let i = 0; i < segs.length; i++) {
      const seg = segs[i];
      const endPt = worldToScreen(seg.end.x, seg.end.y);
      if (seg.type === 'cubic_bezier' && seg.cp1 && seg.cp2) {
        const cp1 = worldToScreen(seg.cp1.x, seg.cp1.y);
        const cp2 = worldToScreen(seg.cp2.x, seg.cp2.y);
        ctx.bezierCurveTo(cp1.x, cp1.y, cp2.x, cp2.y, endPt.x, endPt.y);
      } else {
        ctx.lineTo(endPt.x, endPt.y);
      }
    }

    if (isClosed) {
      ctx.closePath();
      if (fillColor) ctx.fill();
    }
    ctx.stroke();
    ctx.restore();
  }

  function renderHandles(contour) {
    if (!contour || !contour.segments) return;

    ctx.save();
    for (let i = 0; i < contour.segments.length; i++) {
      const seg = contour.segments[i];
      const p1 = worldToScreen(seg.start.x, seg.start.y);
      const p2 = worldToScreen(seg.end.x, seg.end.y);

      // Start / End anchor nodes (cyan square)
      ctx.fillStyle = '#06b6d4';
      ctx.fillRect(p1.x - 3, p1.y - 3, 6, 6);

      if (seg.type === 'cubic_bezier' && seg.cp1 && seg.cp2) {
        const cp1 = worldToScreen(seg.cp1.x, seg.cp1.y);
        const cp2 = worldToScreen(seg.cp2.x, seg.cp2.y);

        // Control lines
        ctx.strokeStyle = 'rgba(236, 72, 153, 0.4)';
        ctx.lineWidth = 1;
        ctx.setLineDash([2, 2]);
        ctx.beginPath();
        ctx.moveTo(p1.x, p1.y);
        ctx.lineTo(cp1.x, cp1.y);
        ctx.moveTo(p2.x, p2.y);
        ctx.lineTo(cp2.x, cp2.y);
        ctx.stroke();

        // Control handles (pink circle)
        ctx.setLineDash([]);
        ctx.fillStyle = '#ec4899';
        ctx.beginPath();
        ctx.arc(cp1.x, cp1.y, 3, 0, Math.PI * 2);
        ctx.fill();

        ctx.fillStyle = '#f59e0b';
        ctx.beginPath();
        ctx.arc(cp2.x, cp2.y, 3, 0, Math.PI * 2);
        ctx.fill();
      }
    }
    ctx.restore();
  }

  function renderNotches(notches) {
    if (!notches || notches.length === 0) return;

    ctx.save();
    ctx.strokeStyle = '#84cc16';
    ctx.lineWidth = 2;
    ctx.setLineDash([]);

    for (const notch of notches) {
      const p1 = worldToScreen(notch.position.x, notch.position.y);
      // Direction vector
      const dx = notch.direction ? notch.direction.x : 0;
      const dy = notch.direction ? notch.direction.y : 1;
      const lengthMm = notch.length_mm || 5.0;

      const p2 = worldToScreen(
        notch.position.x + dx * lengthMm,
        notch.position.y + dy * lengthMm
      );

      ctx.beginPath();
      ctx.moveTo(p1.x, p1.y);
      ctx.lineTo(p2.x, p2.y);
      ctx.stroke();

      // Notch base dot
      ctx.fillStyle = '#84cc16';
      ctx.beginPath();
      ctx.arc(p1.x, p1.y, 2, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.restore();
  }

  function renderInternalLines(internalLines) {
    if (!internalLines || internalLines.length === 0) return;

    for (const line of internalLines) {
      const isFold = line.line_type === 'fold';
      const color = isFold ? '#a855f7' : '#ef4444';
      const dash = isFold ? [6, 3] : [];
      renderContour(line.contour, false, color, null, dash, 1.5);
    }
  }

  function renderSelectedSegment(piece, segIdx) {
    if (!piece || segIdx === null || !piece.outer_contour) return;
    const segs = piece.outer_contour.segments;
    if (segIdx < 0 || segIdx >= segs.length) return;

    const seg = segs[segIdx];
    ctx.save();
    ctx.strokeStyle = '#eab308'; // bright yellow highlight
    ctx.lineWidth = 3.5;
    ctx.setLineDash([]);

    ctx.beginPath();
    const p1 = worldToScreen(seg.start.x, seg.start.y);
    const p2 = worldToScreen(seg.end.x, seg.end.y);
    ctx.moveTo(p1.x, p1.y);

    if (seg.type === 'cubic_bezier' && seg.cp1 && seg.cp2) {
      const cp1 = worldToScreen(seg.cp1.x, seg.cp1.y);
      const cp2 = worldToScreen(seg.cp2.x, seg.cp2.y);
      ctx.bezierCurveTo(cp1.x, cp1.y, cp2.x, cp2.y, p2.x, p2.y);
    } else {
      ctx.lineTo(p2.x, p2.y);
    }
    ctx.stroke();
    ctx.restore();
  }

  function renderPiece(piece) {
    const isSelected = piece.id === state.selectedPieceId;
    const hasCut = piece.cut_contour && state.showSeamPreview;

    // 1. Fabric Fill & Boundary
    const fillColor = isSelected
      ? 'rgba(2, 132, 199, 0.16)'
      : 'rgba(30, 41, 59, 0.4)';

    // 2. Cut boundary (solid amber) if present
    if (hasCut) {
      const cutStroke = isSelected ? '#fbbf24' : '#d97706';
      renderContour(piece.cut_contour, true, cutStroke, fillColor, [], isSelected ? 2.5 : 1.5);
      
      // Inner Sewing contour (dashed cyan)
      renderContour(piece.outer_contour, true, '#06b6d4', null, [5, 3], 1.2);
    } else {
      // Net-only contour
      const strokeColor = isSelected ? '#38bdf8' : '#0284c7';
      renderContour(piece.outer_contour, true, strokeColor, fillColor, [], isSelected ? 2.5 : 1.5);
    }

    // 3. Internal lines & darts (Layer 4 & 5)
    renderInternalLines(piece.internal_lines);

    // 4. Alignment Notches (Layer 2)
    renderNotches(piece.notches);

    // 5. Bézier handles if enabled
    if (state.showHandles || isSelected) {
      renderHandles(piece.outer_contour);
    }

    // 6. Selected segment highlight
    if (isSelected && state.selectedSegIdx !== null) {
      renderSelectedSegment(piece, state.selectedSegIdx);
    }

    // 7. Piece Name & Dimension Label
    const bounds = getContourBounds(piece.outer_contour);
    if (bounds) {
      const center = worldToScreen((bounds.minX + bounds.maxX) / 2, (bounds.minY + bounds.maxY) / 2);
      const wMm = (bounds.maxX - bounds.minX).toFixed(1);
      const hMm = (bounds.maxY - bounds.minY).toFixed(1);

      ctx.save();
      ctx.fillStyle = isSelected ? '#ffffff' : '#94a3b8';
      ctx.font = 'bold 11px Inter, sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText(`${piece.name} (x${piece.quantity})`, center.x, center.y - 6);

      ctx.font = '10px "JetBrains Mono", monospace';
      ctx.fillStyle = isSelected ? '#38bdf8' : '#64748b';
      ctx.fillText(`${wMm} x ${hMm} mm`, center.x, center.y + 8);
      ctx.restore();
    }
  }

  function renderCanvas() {
    renderGrid();

    if (!state.project || !state.project.pieces) return;

    // Render pieces in order
    for (const piece of state.project.pieces) {
      renderPiece(piece);
    }

    // Update zoom label
    lblZoomLevel.textContent = `${Math.round(state.camera.zoom * 100)}%`;
  }

  // =========================================================================
  // Geometry & Hit Testing Helpers
  // =========================================================================

  function getContourBounds(contour) {
    if (!contour || !contour.segments || contour.segments.length === 0) return null;
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const seg of contour.segments) {
      minX = Math.min(minX, seg.start.x, seg.end.x);
      minY = Math.min(minY, seg.start.y, seg.end.y);
      maxX = Math.max(maxX, seg.start.x, seg.end.x);
      maxY = Math.max(maxY, seg.start.y, seg.end.y);
      if (seg.cp1 && seg.cp2) {
        minX = Math.min(minX, seg.cp1.x, seg.cp2.x);
        minY = Math.min(minY, seg.cp1.y, seg.cp2.y);
        maxX = Math.max(maxX, seg.cp1.x, seg.cp2.x);
        maxY = Math.max(maxY, seg.cp1.y, seg.cp2.y);
      }
    }
    return { minX, minY, maxX, maxY };
  }

  function pointInPolygon(px, py, vertices) {
    let inside = false;
    for (let i = 0, j = vertices.length - 1; i < vertices.length; j = i++) {
      const xi = vertices[i].x, yi = vertices[i].y;
      const xj = vertices[j].x, yj = vertices[j].y;
      const intersect = ((yi > py) !== (yj > py)) &&
        (px < (xj - xi) * (py - yi) / (yj - yi) + xi);
      if (intersect) inside = !inside;
    }
    return inside;
  }

  function getContourPolygonVertices(contour) {
    if (!contour || !contour.segments) return [];
    const pts = [];
    for (const seg of contour.segments) {
      pts.push(seg.start);
    }
    return pts;
  }

  function distToSegment(px, py, x1, y1, x2, y2) {
    const l2 = (x2 - x1) * (x2 - x1) + (y2 - y1) * (y2 - y1);
    if (l2 === 0) return Math.hypot(px - x1, py - y1);
    let t = ((px - x1) * (x2 - x1) + (py - y1) * (y2 - y1)) / l2;
    t = Math.max(0, Math.min(1, t));
    return Math.hypot(px - (x1 + t * (x2 - x1)), py - (y1 + t * (y2 - y1)));
  }

  function findHitPiece(wx, wy) {
    if (!state.project || !state.project.pieces) return null;

    // Check in reverse order (topmost first)
    for (let i = state.project.pieces.length - 1; i >= 0; i--) {
      const p = state.project.pieces[i];
      const targetContour = p.cut_contour || p.outer_contour;
      const poly = getContourPolygonVertices(targetContour);
      if (poly.length >= 3 && pointInPolygon(wx, wy, poly)) {
        return p;
      }
    }
    return null;
  }

  function findClosestSegmentIndex(piece, wx, wy) {
    if (!piece || !piece.outer_contour) return null;
    const segs = piece.outer_contour.segments;
    let bestDist = Infinity;
    let bestIdx = null;

    for (let i = 0; i < segs.length; i++) {
      const s = segs[i];
      const d = distToSegment(wx, wy, s.start.x, s.start.y, s.end.x, s.end.y);
      if (d < bestDist) {
        bestDist = d;
        bestIdx = i;
      }
    }
    return bestDist <= 25.0 ? bestIdx : null; // threshold in mm
  }

  function fitViewToProject() {
    if (!state.project || !state.project.pieces || state.project.pieces.length === 0) return;

    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const p of state.project.pieces) {
      const b = getContourBounds(p.cut_contour || p.outer_contour);
      if (b) {
        minX = Math.min(minX, b.minX);
        minY = Math.min(minY, b.minY);
        maxX = Math.max(maxX, b.maxX);
        maxY = Math.max(maxY, b.maxY);
      }
    }

    if (!isFinite(minX)) return;

    const parent = canvas.parentElement;
    const viewW = parent.clientWidth;
    const viewH = parent.clientHeight;
    const padding = 60;

    const projW = maxX - minX;
    const projH = maxY - minY;

    if (projW <= 0 || projH <= 0) return;

    const zoomX = (viewW - padding * 2) / projW;
    const zoomY = (viewH - padding * 2) / projH;
    state.camera.zoom = Math.max(0.2, Math.min(zoomX, zoomY, 2.5));

    // Center project
    const projCenterX = (minX + maxX) / 2;
    const projCenterY = (minY + maxY) / 2;

    state.camera.x = viewW / 2 - projCenterX * state.camera.zoom;
    state.camera.y = viewH / 2 + projCenterY * state.camera.zoom;

    renderCanvas();
  }

  // =========================================================================
  // Canvas Mouse & Interaction Events
  // =========================================================================

  canvas.addEventListener('wheel', (e) => {
    e.preventDefault();
    const rect = canvas.getBoundingClientRect();
    const mouseX = e.clientX - rect.left;
    const mouseY = e.clientY - rect.top;

    const worldBefore = screenToWorld(mouseX, mouseY);
    const zoomFactor = e.deltaY < 0 ? 1.15 : 0.87;
    const newZoom = Math.max(0.05, Math.min(10.0, state.camera.zoom * zoomFactor));

    state.camera.zoom = newZoom;
    state.camera.x = mouseX - worldBefore.x * state.camera.zoom;
    state.camera.y = mouseY + worldBefore.y * state.camera.zoom;

    renderCanvas();
  });

  canvas.addEventListener('mousedown', (e) => {
    // Pan with middle button, or left button with Space pressed, or right click
    if (e.button === 1 || (e.button === 0 && state.spacePressed) || e.button === 2) {
      state.isPanning = true;
      state.panStart = { x: e.clientX, y: e.clientY };
      canvas.style.cursor = 'grabbing';
      e.preventDefault();
      return;
    }

    // Left click: selection
    if (e.button === 0) {
      const rect = canvas.getBoundingClientRect();
      const sx = e.clientX - rect.left;
      const sy = e.clientY - rect.top;
      const w = screenToWorld(sx, sy);

      const hit = findHitPiece(w.x, w.y);
      if (hit) {
        selectPiece(hit.id);
        const segIdx = findClosestSegmentIndex(hit, w.x, w.y);
        selectSegment(segIdx);
      } else {
        selectPiece(null);
        selectSegment(null);
      }
      renderCanvas();
    }
  });

  window.addEventListener('mousemove', (e) => {
    const rect = canvas.getBoundingClientRect();
    const sx = e.clientX - rect.left;
    const sy = e.clientY - rect.top;

    if (state.isPanning) {
      const dx = e.clientX - state.panStart.x;
      const dy = e.clientY - state.panStart.y;
      state.camera.x += dx;
      state.camera.y += dy;
      state.panStart = { x: e.clientX, y: e.clientY };
      renderCanvas();
      return;
    }

    // Coordinate hover indicator
    if (sx >= 0 && sx <= rect.width && sy >= 0 && sy <= rect.height) {
      const w = screenToWorld(sx, sy);
      lblCursorCoords.textContent = `X: ${w.x.toFixed(1)} mm, Y: ${w.y.toFixed(1)} mm`;
    }
  });

  window.addEventListener('mouseup', () => {
    if (state.isPanning) {
      state.isPanning = false;
      canvas.style.cursor = state.spacePressed ? 'grab' : 'crosshair';
    }
  });

  canvas.addEventListener('contextmenu', (e) => e.preventDefault());

  window.addEventListener('keydown', (e) => {
    if (e.code === 'Space' && !state.spacePressed && e.target === document.body) {
      state.spacePressed = true;
      canvas.style.cursor = 'grab';
    }
  });

  window.addEventListener('keyup', (e) => {
    if (e.code === 'Space') {
      state.spacePressed = false;
      canvas.style.cursor = 'crosshair';
    }
  });

  // Floating canvas controls
  document.getElementById('btn-zoom-in').addEventListener('click', () => {
    const parent = canvas.parentElement;
    const cx = parent.clientWidth / 2;
    const cy = parent.clientHeight / 2;
    const w = screenToWorld(cx, cy);
    state.camera.zoom = Math.min(10.0, state.camera.zoom * 1.25);
    state.camera.x = cx - w.x * state.camera.zoom;
    state.camera.y = cy + w.y * state.camera.zoom;
    renderCanvas();
  });

  document.getElementById('btn-zoom-out').addEventListener('click', () => {
    const parent = canvas.parentElement;
    const cx = parent.clientWidth / 2;
    const cy = parent.clientHeight / 2;
    const w = screenToWorld(cx, cy);
    state.camera.zoom = Math.max(0.05, state.camera.zoom * 0.8);
    state.camera.x = cx - w.x * state.camera.zoom;
    state.camera.y = cy + w.y * state.camera.zoom;
    renderCanvas();
  });

  document.getElementById('btn-fit-screen').addEventListener('click', fitViewToProject);

  document.getElementById('btn-reset-view').addEventListener('click', () => {
    state.camera = { x: 100, y: 400, zoom: 1.0 };
    renderCanvas();
  });

  document.getElementById('toggle-handles').addEventListener('change', (e) => {
    state.showHandles = e.target.checked;
    renderCanvas();
  });

  document.getElementById('toggle-seam-preview').addEventListener('change', (e) => {
    state.showSeamPreview = e.target.checked;
    renderCanvas();
  });

  // =========================================================================
  // API Calls & Project Actions
  // =========================================================================

  async function apiCall(endpoint, method = 'GET', body = null) {
    const opts = { method, headers: {} };
    if (body instanceof FormData) {
      opts.body = body;
    } else if (body) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    const res = await fetch(endpoint, opts);
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || 'Wystąpił błąd podczas komunikacji z serwerem');
    }
    return res.json();
  }

  async function fetchProject() {
    try {
      showLoading('Wczytywanie projektu CAD...');
      const proj = await apiCall('/api/project');
      setProject(proj);
      fitViewToProject();
    } catch (err) {
      alert(`Błąd wczytywania projektu: ${err.message}`);
    } finally {
      hideLoading();
    }
  }

  function setProject(proj) {
    state.project = proj;
    projectTitleEl.textContent = proj.project_name || 'Projekt CAD';
    piecesCountBadge.textContent = proj.pieces ? proj.pieces.length : 0;

    // Populate piece list sidebar
    renderPiecesList();

    // Select first piece if available and none selected
    if (proj.pieces && proj.pieces.length > 0) {
      if (!state.selectedPieceId || !proj.pieces.find(p => p.id === state.selectedPieceId)) {
        selectPiece(proj.pieces[0].id);
      } else {
        updateInspector();
      }
    } else {
      selectPiece(null);
    }

    renderCanvas();
  }

  function selectPiece(pieceId) {
    state.selectedPieceId = pieceId;
    state.selectedSegIdx = 0; // default to first segment

    // Update piece list active class
    const cards = piecesListEl.querySelectorAll('.piece-card');
    cards.forEach(card => {
      if (card.dataset.id === pieceId) {
        card.classList.add('selected');
        card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      } else {
        card.classList.remove('selected');
      }
    });

    updateInspector();
    renderCanvas();
  }

  function selectSegment(segIdx) {
    state.selectedSegIdx = segIdx;
    updateEdgeInspector();
    renderCanvas();
  }

  function renderPiecesList() {
    piecesListEl.innerHTML = '';
    if (!state.project || !state.project.pieces) return;

    for (const p of state.project.pieces) {
      const b = getContourBounds(p.outer_contour);
      const wMm = b ? (b.maxX - b.minX).toFixed(1) : '-';
      const hMm = b ? (b.maxY - b.minY).toFixed(1) : '-';

      const card = document.createElement('div');
      card.className = `piece-card p-2.5 rounded-lg border border-slate-800 bg-slate-900/60 cursor-pointer ${p.id === state.selectedPieceId ? 'selected' : ''}`;
      card.dataset.id = p.id;

      let badgesHtml = '';
      if (p.has_fold) {
        badgesHtml += `<span class="px-1.5 py-0.2 text-[9px] font-mono rounded bg-purple-950 text-purple-300 border border-purple-800/60">FOLD</span> `;
      }
      if (p.internal_lines && p.internal_lines.length > 0) {
        badgesHtml += `<span class="px-1.5 py-0.2 text-[9px] font-mono rounded bg-rose-950 text-rose-300 border border-rose-800/60">INT(${p.internal_lines.length})</span> `;
      }
      if (p.notches && p.notches.length > 0) {
        badgesHtml += `<span class="px-1.5 py-0.2 text-[9px] font-mono rounded bg-lime-950 text-lime-300 border border-lime-800/60">NOTCH(${p.notches.length})</span> `;
      }

      card.innerHTML = `
        <div class="flex items-center justify-between">
          <span class="font-medium text-xs text-white truncate max-w-[140px]">${p.name}</span>
          <span class="text-[10px] font-mono font-bold text-sky-400 bg-slate-800 px-1.5 py-0.5 rounded">x${p.quantity}</span>
        </div>
        <div class="mt-1 flex items-center justify-between text-[11px] font-mono text-slate-400">
          <span>${wMm} x ${hMm} mm</span>
          <span class="text-amber-400 font-semibold">+${p.seam_allowance_mm || 0}mm</span>
        </div>
        ${badgesHtml ? `<div class="mt-1.5 flex flex-wrap gap-1">${badgesHtml}</div>` : ''}
      `;

      card.addEventListener('click', () => selectPiece(p.id));
      piecesListEl.appendChild(card);
    }
  }

  function updateInspector() {
    if (!state.project || !state.selectedPieceId) {
      inspPieceName.textContent = 'Zaznacz element na liście lub canvasie';
      inspPieceId.textContent = 'Brak';
      inspNetDims.textContent = '-';
      inspCutDims.textContent = '-';
      return;
    }

    const piece = state.project.pieces.find(p => p.id === state.selectedPieceId);
    if (!piece) return;

    inspPieceName.textContent = piece.name;
    inspPieceId.textContent = piece.id;

    const netB = getContourBounds(piece.outer_contour);
    if (netB) {
      inspNetDims.textContent = `${(netB.maxX - netB.minX).toFixed(1)} x ${(netB.maxY - netB.minY).toFixed(1)} mm`;
    }

    const cutB = getContourBounds(piece.cut_contour || piece.outer_contour);
    if (cutB) {
      inspCutDims.textContent = `${(cutB.maxX - cutB.minX).toFixed(1)} x ${(cutB.maxY - cutB.minY).toFixed(1)} mm`;
    }

    inspPieceSeamSlider.value = piece.seam_allowance_mm || 0;
    inspPieceSeamVal.textContent = `${(piece.seam_allowance_mm || 0).toFixed(1)} mm`;

    inpScaleW.value = piece.target_width_cm || (netB ? ((netB.maxX - netB.minX) / 10).toFixed(1) : 10);
    inpScaleH.value = piece.target_height_cm || (netB ? ((netB.maxY - netB.minY) / 10).toFixed(1) : 10);

    // Quantity buttons
    document.querySelectorAll('.btn-qty').forEach(btn => {
      btn.classList.toggle('active', parseInt(btn.dataset.qty) === piece.quantity);
    });

    updateEdgeInspector();
  }

  function updateEdgeInspector() {
    if (!state.project || !state.selectedPieceId) return;
    const piece = state.project.pieces.find(p => p.id === state.selectedPieceId);
    if (!piece || !piece.outer_contour) return;

    const segs = piece.outer_contour.segments;
    if (state.selectedSegIdx === null || state.selectedSegIdx < 0 || state.selectedSegIdx >= segs.length) {
      inspSegIdx.textContent = 'Nie wybrano krawędzi';
      return;
    }

    const seg = segs[state.selectedSegIdx];
    inspSegIdx.textContent = `Segment #${state.selectedSegIdx + 1} (${seg.type})`;
    inspEdgeRole.value = seg.edge_role || 'seam';
    inspEdgeSeamOverride.value = seg.seam_allowance_mm !== null && seg.seam_allowance_mm !== undefined
      ? seg.seam_allowance_mm
      : '';
  }

  // =========================================================================
  // User Actions (Seam, Unfold, Mirror, Scaling)
  // =========================================================================

  globalSeamSlider.addEventListener('input', (e) => {
    globalSeamVal.textContent = `${parseFloat(e.target.value).toFixed(1)} mm`;
  });

  btnApplyGlobalSeam.addEventListener('click', async () => {
    const val = parseFloat(globalSeamSlider.value);
    try {
      showLoading(`Stosowanie naddatku ${val.toFixed(1)} mm do wszystkich części...`);
      const res = await apiCall('/api/project/global-seam', 'POST', { seam_allowance_mm: val });
      setProject(res.project);
    } catch (err) {
      alert(`Błąd: ${err.message}`);
    } finally {
      hideLoading();
    }
  });

  inspPieceSeamSlider.addEventListener('input', (e) => {
    inspPieceSeamVal.textContent = `${parseFloat(e.target.value).toFixed(1)} mm`;
  });

  btnSavePieceSeam.addEventListener('click', async () => {
    if (!state.selectedPieceId) return;
    const val = parseFloat(inspPieceSeamSlider.value);
    try {
      showLoading('Aktualizacja naddatku elementu...');
      const res = await apiCall(`/api/pieces/${state.selectedPieceId}/seam-allowance`, 'POST', { seam_allowance_mm: val });
      // Update piece locally
      const idx = state.project.pieces.findIndex(p => p.id === state.selectedPieceId);
      if (idx !== -1) {
        state.project.pieces[idx] = res.piece;
        setProject(state.project);
      }
    } catch (err) {
      alert(`Błąd: ${err.message}`);
    } finally {
      hideLoading();
    }
  });

  btnUnfoldPiece.addEventListener('click', async () => {
    if (!state.selectedPieceId) return;
    try {
      showLoading('Rozkładanie elementu na linii zgięcia...');
      const res = await apiCall(`/api/pieces/${state.selectedPieceId}/unfold`, 'POST');
      const idx = state.project.pieces.findIndex(p => p.id === state.selectedPieceId);
      if (idx !== -1) {
        state.project.pieces[idx] = res.piece;
        setProject(state.project);
      }
    } catch (err) {
      alert(`Błąd: ${err.message}`);
    } finally {
      hideLoading();
    }
  });

  btnMirrorPiece.addEventListener('click', async () => {
    if (!state.selectedPieceId) return;
    try {
      showLoading('Odbicie lustrzane elementu...');
      const res = await apiCall(`/api/pieces/${state.selectedPieceId}/mirror`, 'POST');
      const idx = state.project.pieces.findIndex(p => p.id === state.selectedPieceId);
      if (idx !== -1) {
        state.project.pieces[idx] = res.piece;
        setProject(state.project);
      }
    } catch (err) {
      alert(`Błąd: ${err.message}`);
    } finally {
      hideLoading();
    }
  });

  document.querySelectorAll('.btn-qty').forEach(btn => {
    btn.addEventListener('click', async () => {
      if (!state.selectedPieceId) return;
      const qty = parseInt(btn.dataset.qty);
      try {
        const res = await apiCall(`/api/pieces/${state.selectedPieceId}/quantity`, 'POST', { quantity: qty });
        const idx = state.project.pieces.findIndex(p => p.id === state.selectedPieceId);
        if (idx !== -1) {
          state.project.pieces[idx] = res.piece;
          setProject(state.project);
        }
      } catch (err) {
        alert(`Błąd: ${err.message}`);
      }
    });
  });

  btnApplyScale.addEventListener('click', async () => {
    if (!state.selectedPieceId) return;
    const wCm = parseFloat(inpScaleW.value);
    const hCm = parseFloat(inpScaleH.value);
    if (!wCm || !hCm || wCm <= 0 || hCm <= 0) {
      alert('Podaj poprawne wymiary w centymetrach!');
      return;
    }
    try {
      showLoading('Skalowanie anizotropowe elementu...');
      const res = await apiCall(`/api/pieces/${state.selectedPieceId}/scale-anisotropic`, 'POST', {
        target_width_cm: wCm,
        target_height_cm: hCm,
      });
      const idx = state.project.pieces.findIndex(p => p.id === state.selectedPieceId);
      if (idx !== -1) {
        state.project.pieces[idx] = res.piece;
        setProject(state.project);
      }
    } catch (err) {
      alert(`Błąd: ${err.message}`);
    } finally {
      hideLoading();
    }
  });

  btnSaveEdgeRole.addEventListener('click', async () => {
    if (!state.selectedPieceId || state.selectedSegIdx === null) return;
    const role = inspEdgeRole.value;
    const overrideVal = inspEdgeSeamOverride.value ? parseFloat(inspEdgeSeamOverride.value) : null;
    try {
      showLoading('Aktualizacja roli krawędzi...');
      const res = await apiCall(`/api/pieces/${state.selectedPieceId}/segments/${state.selectedSegIdx}/role`, 'POST', {
        edge_role: role,
        seam_allowance_mm: overrideVal,
      });
      const idx = state.project.pieces.findIndex(p => p.id === state.selectedPieceId);
      if (idx !== -1) {
        state.project.pieces[idx] = res.piece;
        setProject(state.project);
      }
    } catch (err) {
      alert(`Błąd: ${err.message}`);
    } finally {
      hideLoading();
    }
  });

  presetSelector.addEventListener('change', async (e) => {
    const presetId = e.target.value;
    try {
      showLoading('Ładowanie wzorca...');
      const res = await apiCall(`/api/presets/${presetId}/load`, 'POST');
      setProject(res.project);
      fitViewToProject();
    } catch (err) {
      alert(`Błąd ładowania wzorca: ${err.message}`);
    } finally {
      hideLoading();
    }
  });

  // =========================================================================
  // Tabs & Views
  // =========================================================================

  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      const tab = btn.dataset.tab;
      state.activeTab = tab;

      document.querySelectorAll('.view-panel').forEach(p => p.classList.add('hidden'));
      if (tab === 'cad') {
        document.getElementById('view-cad').classList.remove('hidden');
        renderCanvas();
      } else if (tab === 'nesting') {
        document.getElementById('view-nesting').classList.remove('hidden');
        loadNestingPreview();
      } else if (tab === 'diagnostics') {
        document.getElementById('view-diagnostics').classList.remove('hidden');
        // Refresh diagnostic image
        document.getElementById('diagnostic-sheet-img').src = `/api/diagnostics/contact-sheet?t=${Date.now()}`;
      }
    });
  });

  // =========================================================================
  // Nesting A4 View
  // =========================================================================

  async function loadNestingPreview() {
    try {
      showLoading('Obliczanie 2D Nestingu i kafelkowania A4...');
      const res = await apiCall('/api/nesting/calculate', 'POST', {
        mode: state.nestingMode,
        page_w_mm: 210.0,
        page_h_mm: 297.0,
        margin_mm: 10.0,
        overlap_mm: 15.0,
      });
      state.nestingData = res;
      renderNestingPages(res);
    } catch (err) {
      alert(`Błąd nestingu: ${err.message}`);
    } finally {
      hideLoading();
    }
  }

  function renderNestingPages(data) {
    const container = document.getElementById('nesting-pages-container');
    container.innerHTML = '';

    const btnDownload = document.getElementById('btn-download-nesting-pdf');
    btnDownload.href = `/api/export/pdf-a4?mode=${state.nestingMode}`;

    for (const page of data.pages) {
      const card = document.createElement('div');
      card.className = 'nesting-page-card bg-slate-900 border border-slate-800 rounded-xl p-3 flex flex-col shadow-lg';

      let itemsSummary = '';
      if (page.placements && page.placements.length > 0) {
        itemsSummary += `<p class="text-xs text-sky-400 font-medium">Całe elementy: ${page.placements.map(pl => pl.piece_name).join(', ')}</p>`;
      }
      if (page.tiles && page.tiles.length > 0) {
        itemsSummary += `<p class="text-xs text-amber-400 font-medium">Kafelki: ${page.tiles.map(t => `${t.piece_name} (${t.tile_idx + 1}/${t.total_tiles})`).join(', ')}</p>`;
      }

      card.innerHTML = `
        <div class="flex items-center justify-between pb-2 border-b border-slate-800 text-xs">
          <span class="font-bold text-white flex items-center gap-1.5">
            <i data-lucide="file" class="w-3.5 h-3.5 text-emerald-400"></i>
            Strona A4 #${page.page_number}
          </span>
          <span class="text-[10px] font-mono bg-slate-800 text-slate-300 px-2 py-0.5 rounded">Rząd ${page.row + 1}, Kol. ${page.col + 1}</span>
        </div>
        
        <!-- Mock A4 Sheet Preview -->
        <div class="my-3 aspect-[210/297] bg-slate-950 border border-slate-700/80 rounded relative p-2 flex flex-col justify-between overflow-hidden">
          <div class="border border-dashed border-slate-800 absolute inset-2 rounded pointer-events-none"></div>
          <div class="text-[9px] font-mono text-slate-500 flex justify-between z-10">
            <span>210 x 297 mm</span>
            <span>Margines 10mm</span>
          </div>
          
          <div class="text-center z-10 my-auto p-2">
            <i data-lucide="maximize" class="w-8 h-8 text-slate-700 mx-auto mb-1"></i>
            <p class="text-xs font-semibold text-slate-300">Arkusz ${page.page_number} / ${data.total_pages}</p>
            ${page.has_calibration_square ? '<span class="inline-block mt-1 px-1.5 py-0.5 text-[9px] font-mono bg-amber-500/20 text-amber-300 rounded border border-amber-500/40">Kwadrat 5x5 cm</span>' : ''}
          </div>

          <div class="text-[9px] font-mono text-slate-500 flex justify-between z-10">
            <span>Overlap 15mm</span>
            <span>R${page.row + 1}-C${page.col + 1}</span>
          </div>
        </div>

        <div class="space-y-1 text-xs">
          ${itemsSummary || '<p class="text-slate-500 text-xs italic">Brak elementów na tej stronie</p>'}
        </div>
      `;

      container.appendChild(card);
    }
    lucide.createIcons();
  }

  document.getElementById('nesting-mode-hybrid').addEventListener('click', () => {
    state.nestingMode = 'hybrid';
    document.getElementById('nesting-mode-hybrid').className = 'px-3 py-1 text-xs rounded-md font-medium text-white bg-slate-700';
    document.getElementById('nesting-mode-poster').className = 'px-3 py-1 text-xs rounded-md font-medium text-slate-400 hover:text-white';
    loadNestingPreview();
  });

  document.getElementById('nesting-mode-poster').addEventListener('click', () => {
    state.nestingMode = 'poster';
    document.getElementById('nesting-mode-poster').className = 'px-3 py-1 text-xs rounded-md font-medium text-white bg-slate-700';
    document.getElementById('nesting-mode-hybrid').className = 'px-3 py-1 text-xs rounded-md font-medium text-slate-400 hover:text-white';
    loadNestingPreview();
  });

  document.getElementById('btn-recalculate-nesting').addEventListener('click', loadNestingPreview);

  // =========================================================================
  // Digitizer Upload Modal
  // =========================================================================

  btnOpenDigitizer.addEventListener('click', () => {
    digitizerModal.classList.remove('hidden');
  });

  btnCloseDigitizerModal.addEventListener('click', () => digitizerModal.classList.add('hidden'));
  btnCancelDigitizer.addEventListener('click', () => digitizerModal.classList.add('hidden'));

  dropArea.addEventListener('click', () => digitizerFileInput.click());

  digitizerFileInput.addEventListener('change', (e) => {
    if (e.target.files && e.target.files[0]) {
      selectedFileName.textContent = e.target.files[0].name;
      selectedFileName.classList.remove('hidden');
    }
  });

  ['dragenter', 'dragover'].forEach(eventName => {
    dropArea.addEventListener(eventName, (e) => {
      e.preventDefault();
      dropArea.classList.add('border-indigo-500', 'bg-indigo-950/20');
    });
  });

  ['dragleave', 'drop'].forEach(eventName => {
    dropArea.addEventListener(eventName, (e) => {
      e.preventDefault();
      dropArea.classList.remove('border-indigo-500', 'bg-indigo-950/20');
    });
  });

  dropArea.addEventListener('drop', (e) => {
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      digitizerFileInput.files = e.dataTransfer.files;
      selectedFileName.textContent = e.dataTransfer.files[0].name;
      selectedFileName.classList.remove('hidden');
    }
  });

  digitizerForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const file = digitizerFileInput.files[0];
    if (!file) {
      alert('Wybierz plik graficzny wykroju (PNG lub JPG)!');
      return;
    }

    const seamMode = document.getElementById('digitizer-seam-mode').value;
    const seamMm = parseFloat(document.getElementById('digitizer-seam-mm').value);
    const autoUnfold = document.getElementById('digitizer-auto-unfold').checked;
    const bezierError = parseFloat(document.getElementById('digitizer-bezier-error').value);

    const formData = new FormData();
    formData.append('file', file);

    digitizerModal.classList.add('hidden');
    showLoading('Potok percepcji: odzyskiwanie skali, segmentacja i wektoryzacja Béziera...');

    try {
      const url = `/api/digitize?seam_mode=${seamMode}&seam_allowance_mm=${seamMm}&auto_unfold=${autoUnfold}&max_bezier_error_mm=${bezierError}`;
      const res = await apiCall(url, 'POST', formData);
      setProject(res.project);
      fitViewToProject();
      alert(`Sukces! Zdygitalizowano ${res.pieces_count} elementów wykroju.`);
    } catch (err) {
      alert(`Błąd dygitalizacji: ${err.message}`);
    } finally {
      hideLoading();
    }
  });

  // =========================================================================
  // Export Dropdown
  // =========================================================================

  exportDropdownBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    exportMenu.classList.toggle('hidden');
  });

  window.addEventListener('click', () => {
    if (!exportMenu.classList.contains('hidden')) {
      exportMenu.classList.add('hidden');
    }
  });

  // =========================================================================
  // Loading Overlay Helpers
  // =========================================================================

  function showLoading(msg = 'Przetwarzanie...') {
    loadingMsg.textContent = msg;
    loadingOverlay.classList.remove('hidden');
  }

  function hideLoading() {
    loadingOverlay.classList.add('hidden');
  }

  // =========================================================================
  // Initialization
  // =========================================================================

  window.addEventListener('resize', resizeCanvas);

  document.addEventListener('DOMContentLoaded', () => {
    lucide.createIcons();
    resizeCanvas();
    fetchProject();
  });

})();
