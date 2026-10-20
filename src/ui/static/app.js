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
    hoveredPieceId: null,

    // Per-piece 2D workspace offset in CAD mm: { [pieceId]: { x: number, y: number } }
    pieceOffsets: {},

    // Set of hidden piece IDs
    hiddenPieceIds: new Set(),

    // Camera Transform (World coordinates in mm -> Screen coordinates in px)
    camera: {
      x: 100, // screen px offset
      y: 400, // screen px offset (Y-up)
      zoom: 1.0, // px per mm
    },

    // Pan / Drag interaction state
    isPanning: false,
    panStart: { x: 0, y: 0 },
    hasPanned: false,
    spacePressed: false,

    // Piece dragging state
    draggingPieceId: null,
    dragStartScreen: { x: 0, y: 0 },
    dragStartWorld: { x: 0, y: 0 },
    dragInitialOffset: { x: 0, y: 0 },
    hasDraggedPiece: false,

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

  // ROI Zone Editor Elements
  const roiEditor = document.getElementById('roi-editor');
  const roiStage = document.getElementById('roi-stage');
  const roiCanvas = document.getElementById('roi-canvas');
  const roiCtx = roiCanvas.getContext('2d');
  const roiZoneList = document.getElementById('roi-zone-list');
  const roiSummary = document.getElementById('roi-summary');

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

  function getPieceOffset(pieceId) {
    return state.pieceOffsets[pieceId] || { x: 0, y: 0 };
  }

  function isPieceVisible(pieceId) {
    return !state.hiddenPieceIds.has(pieceId);
  }

  function pieceLocalToScreen(pieceId, lx, ly) {
    const off = getPieceOffset(pieceId);
    return worldToScreen(lx + off.x, ly + off.y);
  }

  // =========================================================================
  // Automatic Non-Overlapping Grid Layout
  // =========================================================================

  function autoArrangePieces(fitView = true) {
    if (!state.project || !state.project.pieces || state.project.pieces.length === 0) return;

    const pieces = state.project.pieces.filter(p => isPieceVisible(p.id));
    const listToArrange = pieces.length > 0 ? pieces : state.project.pieces;

    const cols = Math.min(3, Math.max(1, Math.ceil(Math.sqrt(listToArrange.length))));
    const gapX = 55; // mm horizontal gap between pieces
    const gapY = 60; // mm vertical gap between rows

    let curX = 0;
    let curY = 0;
    let rowMaxH = 0;
    let colIdx = 0;

    for (const p of listToArrange) {
      const b = getContourBounds(p.cut_contour || p.outer_contour) || {
        minX: 0,
        minY: 0,
        maxX: 150,
        maxY: 150,
      };
      const w = b.maxX - b.minX;
      const h = b.maxY - b.minY;

      // Place piece so its bottom-left bounding box sits at (curX, curY)
      state.pieceOffsets[p.id] = {
        x: Math.round((curX - b.minX) * 10) / 10,
        y: Math.round((curY - b.minY) * 10) / 10,
      };

      curX += w + gapX;
      rowMaxH = Math.max(rowMaxH, h);
      colIdx++;

      if (colIdx >= cols) {
        colIdx = 0;
        curX = 0;
        curY += rowMaxH + gapY;
        rowMaxH = 0;
      }
    }

    if (fitView) {
      fitViewToProject();
    } else {
      renderCanvas();
    }
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

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
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

  function renderContour(pieceId, contour, isClosed, strokeColor, fillColor, lineDash = [], lineWidth = 1.5) {
    if (!contour || !contour.segments || contour.segments.length === 0) return;

    ctx.save();
    ctx.strokeStyle = strokeColor;
    ctx.lineWidth = lineWidth;
    ctx.setLineDash(lineDash);
    if (fillColor) ctx.fillStyle = fillColor;

    ctx.beginPath();
    const segs = contour.segments;
    const startPt = pieceLocalToScreen(pieceId, segs[0].start.x, segs[0].start.y);
    ctx.moveTo(startPt.x, startPt.y);

    for (let i = 0; i < segs.length; i++) {
      const seg = segs[i];
      const endPt = pieceLocalToScreen(pieceId, seg.end.x, seg.end.y);
      if (seg.type === 'cubic_bezier' && seg.cp1 && seg.cp2) {
        const cp1 = pieceLocalToScreen(pieceId, seg.cp1.x, seg.cp1.y);
        const cp2 = pieceLocalToScreen(pieceId, seg.cp2.x, seg.cp2.y);
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

  function renderHandles(pieceId, contour) {
    if (!contour || !contour.segments) return;

    ctx.save();
    for (let i = 0; i < contour.segments.length; i++) {
      const seg = contour.segments[i];
      const p1 = pieceLocalToScreen(pieceId, seg.start.x, seg.start.y);
      const p2 = pieceLocalToScreen(pieceId, seg.end.x, seg.end.y);

      // Start / End anchor nodes (cyan square)
      ctx.fillStyle = '#06b6d4';
      ctx.fillRect(p1.x - 3, p1.y - 3, 6, 6);

      if (seg.type === 'cubic_bezier' && seg.cp1 && seg.cp2) {
        const cp1 = pieceLocalToScreen(pieceId, seg.cp1.x, seg.cp1.y);
        const cp2 = pieceLocalToScreen(pieceId, seg.cp2.x, seg.cp2.y);

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

  function renderNotches(pieceId, notches) {
    if (!notches || notches.length === 0) return;

    ctx.save();
    ctx.strokeStyle = '#84cc16';
    ctx.lineWidth = 2;
    ctx.setLineDash([]);

    for (const notch of notches) {
      const p1 = pieceLocalToScreen(pieceId, notch.position.x, notch.position.y);
      const dx = notch.direction ? notch.direction.x : 0;
      const dy = notch.direction ? notch.direction.y : 1;
      const lengthMm = notch.length_mm || 5.0;

      const p2 = pieceLocalToScreen(
        pieceId,
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

  function renderInternalLines(pieceId, internalLines) {
    if (!internalLines || internalLines.length === 0) return;

    for (const line of internalLines) {
      const isFold = line.line_type === 'fold';
      const color = isFold ? '#a855f7' : '#ef4444';
      const dash = isFold ? [6, 3] : [];
      renderContour(pieceId, line.contour, false, color, null, dash, 1.5);
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
    const p1 = pieceLocalToScreen(piece.id, seg.start.x, seg.start.y);
    const p2 = pieceLocalToScreen(piece.id, seg.end.x, seg.end.y);
    ctx.moveTo(p1.x, p1.y);

    if (seg.type === 'cubic_bezier' && seg.cp1 && seg.cp2) {
      const cp1 = pieceLocalToScreen(piece.id, seg.cp1.x, seg.cp1.y);
      const cp2 = pieceLocalToScreen(piece.id, seg.cp2.x, seg.cp2.y);
      ctx.bezierCurveTo(cp1.x, cp1.y, cp2.x, cp2.y, p2.x, p2.y);
    } else {
      ctx.lineTo(p2.x, p2.y);
    }
    ctx.stroke();
    ctx.restore();
  }

  function renderPiece(piece) {
    if (!isPieceVisible(piece.id)) return;

    const isSelected = piece.id === state.selectedPieceId;
    const isHovered = piece.id === state.hoveredPieceId;
    const hasCut = piece.cut_contour && state.showSeamPreview;

    // 1. Fabric Fill & Boundary
    let fillColor = 'rgba(30, 41, 59, 0.45)';
    if (isSelected) {
      fillColor = 'rgba(2, 132, 199, 0.20)';
    } else if (isHovered) {
      fillColor = 'rgba(56, 189, 248, 0.12)';
    }

    // 2. Cut boundary (solid amber) if present
    if (hasCut) {
      const cutStroke = isSelected ? '#fbbf24' : isHovered ? '#f59e0b' : '#d97706';
      renderContour(piece.id, piece.cut_contour, true, cutStroke, fillColor, [], isSelected ? 2.5 : 1.5);

      // Inner Sewing contour (dashed cyan)
      renderContour(piece.id, piece.outer_contour, true, '#06b6d4', null, [5, 3], 1.3);
    } else {
      // Net-only contour
      const strokeColor = isSelected ? '#38bdf8' : isHovered ? '#0ea5e9' : '#0284c7';
      renderContour(piece.id, piece.outer_contour, true, strokeColor, fillColor, [], isSelected ? 2.5 : 1.6);
    }

    // 3. Internal lines & darts (Layer 4 & 5)
    renderInternalLines(piece.id, piece.internal_lines);

    // 4. Alignment Notches (Layer 2)
    renderNotches(piece.id, piece.notches);

    // 5. Bézier handles if enabled
    if (state.showHandles || isSelected) {
      renderHandles(piece.id, piece.outer_contour);
    }

    // 6. Selected segment highlight
    if (isSelected && state.selectedSegIdx !== null) {
      renderSelectedSegment(piece, state.selectedSegIdx);
    }

    // 7. Piece Name & Dimension Label
    const bounds = getContourBounds(piece.outer_contour);
    if (bounds) {
      const center = pieceLocalToScreen(
        piece.id,
        (bounds.minX + bounds.maxX) / 2,
        (bounds.minY + bounds.maxY) / 2
      );
      const wMm = (bounds.maxX - bounds.minX).toFixed(1);
      const hMm = (bounds.maxY - bounds.minY).toFixed(1);

      ctx.save();
      // Subtle pill backdrop for text legibility
      const titleText = `${piece.name} (x${piece.quantity})`;
      const dimText = `${wMm} x ${hMm} mm`;
      ctx.font = 'bold 11px Inter, sans-serif';
      const textW = Math.max(ctx.measureText(titleText).width, 90) + 14;

      ctx.fillStyle = isSelected ? 'rgba(15, 23, 42, 0.85)' : 'rgba(15, 23, 42, 0.68)';
      ctx.beginPath();
      ctx.roundRect(center.x - textW / 2, center.y - 18, textW, 32, 6);
      ctx.fill();

      if (isSelected) {
        ctx.strokeStyle = 'rgba(56, 189, 248, 0.5)';
        ctx.lineWidth = 1;
        ctx.stroke();
      }

      ctx.fillStyle = isSelected ? '#ffffff' : '#cbd5e1';
      ctx.textAlign = 'center';
      ctx.fillText(titleText, center.x, center.y - 4);

      ctx.font = '10px "JetBrains Mono", monospace';
      ctx.fillStyle = isSelected ? '#38bdf8' : '#94a3b8';
      ctx.fillText(dimText, center.x, center.y + 9);
      ctx.restore();
    }
  }

  function renderCanvas() {
    renderGrid();

    if (!state.project || !state.project.pieces) return;

    // Render non-selected pieces first, then selected piece on top so it's never obscured
    for (const piece of state.project.pieces) {
      if (piece.id !== state.selectedPieceId) {
        renderPiece(piece);
      }
    }
    const selected = state.project.pieces.find(p => p.id === state.selectedPieceId);
    if (selected) {
      renderPiece(selected);
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

  function getPieceWorldBounds(piece) {
    const b = getContourBounds(piece.cut_contour || piece.outer_contour);
    if (!b) return null;
    const off = getPieceOffset(piece.id);
    return {
      minX: b.minX + off.x,
      minY: b.minY + off.y,
      maxX: b.maxX + off.x,
      maxY: b.maxY + off.y,
    };
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
      // Sample midpoints of cubic bezier for accurate hit testing on curved shapes
      if (seg.type === 'cubic_bezier' && seg.cp1 && seg.cp2) {
        for (const t of [0.25, 0.5, 0.75]) {
          const mt = 1 - t;
          const bx = mt * mt * mt * seg.start.x + 3 * mt * mt * t * seg.cp1.x + 3 * mt * t * t * seg.cp2.x + t * t * t * seg.end.x;
          const by = mt * mt * mt * seg.start.y + 3 * mt * mt * t * seg.cp1.y + 3 * mt * t * t * seg.cp2.y + t * t * t * seg.end.y;
          pts.push({ x: bx, y: by });
        }
      }
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

    // Test selected piece first if visible (since it renders on top)
    const orderedPieces = [...state.project.pieces];
    if (state.selectedPieceId) {
      const selIdx = orderedPieces.findIndex(p => p.id === state.selectedPieceId);
      if (selIdx !== -1) {
        const [selPiece] = orderedPieces.splice(selIdx, 1);
        orderedPieces.push(selPiece);
      }
    }

    for (let i = orderedPieces.length - 1; i >= 0; i--) {
      const p = orderedPieces[i];
      if (!isPieceVisible(p.id)) continue;

      const off = getPieceOffset(p.id);
      const lx = wx - off.x;
      const ly = wy - off.y;

      const targetContour = p.cut_contour || p.outer_contour;
      const poly = getContourPolygonVertices(targetContour);
      if (poly.length >= 3 && pointInPolygon(lx, ly, poly)) {
        return p;
      }
    }
    return null;
  }

  function findClosestSegmentIndex(piece, wx, wy) {
    if (!piece || !piece.outer_contour) return null;
    const off = getPieceOffset(piece.id);
    const lx = wx - off.x;
    const ly = wy - off.y;

    const segs = piece.outer_contour.segments;
    let bestDist = Infinity;
    let bestIdx = null;

    for (let i = 0; i < segs.length; i++) {
      const s = segs[i];
      const d = distToSegment(lx, ly, s.start.x, s.start.y, s.end.x, s.end.y);
      if (d < bestDist) {
        bestDist = d;
        bestIdx = i;
      }
    }
    return bestDist <= 30.0 ? bestIdx : 0;
  }

  function fitViewToProject() {
    if (!state.project || !state.project.pieces || state.project.pieces.length === 0) return;

    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const p of state.project.pieces) {
      if (!isPieceVisible(p.id)) continue;
      const wb = getPieceWorldBounds(p);
      if (wb) {
        minX = Math.min(minX, wb.minX);
        minY = Math.min(minY, wb.minY);
        maxX = Math.max(maxX, wb.maxX);
        maxY = Math.max(maxY, wb.maxY);
      }
    }

    if (!isFinite(minX)) {
      renderCanvas();
      return;
    }

    const parent = canvas.parentElement;
    const viewW = parent.clientWidth || 800;
    const viewH = parent.clientHeight || 600;
    const padding = 65;

    const projW = maxX - minX;
    const projH = maxY - minY;

    if (projW <= 0 || projH <= 0) return;

    const zoomX = (viewW - padding * 2) / projW;
    const zoomY = (viewH - padding * 2) / projH;
    state.camera.zoom = Math.max(0.15, Math.min(zoomX, zoomY, 2.5));

    const projCenterX = (minX + maxX) / 2;
    const projCenterY = (minY + maxY) / 2;

    state.camera.x = viewW / 2 - projCenterX * state.camera.zoom;
    state.camera.y = viewH / 2 + projCenterY * state.camera.zoom;

    renderCanvas();
  }

  function focusOnPiece(pieceId) {
    if (!state.project || !pieceId) return;
    const piece = state.project.pieces.find(p => p.id === pieceId);
    if (!piece) return;

    // Ensure piece is visible
    state.hiddenPieceIds.delete(pieceId);
    renderPiecesList();

    const wb = getPieceWorldBounds(piece);
    if (!wb) return;

    const parent = canvas.parentElement;
    const viewW = parent.clientWidth || 800;
    const viewH = parent.clientHeight || 600;
    const padding = 90;

    const pw = Math.max(20, wb.maxX - wb.minX);
    const ph = Math.max(20, wb.maxY - wb.minY);

    const zoomX = (viewW - padding * 2) / pw;
    const zoomY = (viewH - padding * 2) / ph;
    state.camera.zoom = Math.max(0.25, Math.min(zoomX, zoomY, 3.0));

    const cx = (wb.minX + wb.maxX) / 2;
    const cy = (wb.minY + wb.maxY) / 2;

    state.camera.x = viewW / 2 - cx * state.camera.zoom;
    state.camera.y = viewH / 2 + cy * state.camera.zoom;

    renderCanvas();
  }

  // =========================================================================
  // Visibility & Isolation Controls
  // =========================================================================

  function togglePieceVisibility(pieceId) {
    if (state.hiddenPieceIds.has(pieceId)) {
      state.hiddenPieceIds.delete(pieceId);
    } else {
      state.hiddenPieceIds.add(pieceId);
    }
    renderPiecesList();
    renderCanvas();
  }

  function showAllPieces() {
    state.hiddenPieceIds.clear();
    renderPiecesList();
    fitViewToProject();
  }

  function isolatePiece(pieceId) {
    if (!state.project || !state.project.pieces) return;
    const targetId = pieceId || state.selectedPieceId;
    if (!targetId) return;

    state.hiddenPieceIds.clear();
    for (const p of state.project.pieces) {
      if (p.id !== targetId) {
        state.hiddenPieceIds.add(p.id);
      }
    }
    selectPiece(targetId);
    focusOnPiece(targetId);
  }

  // =========================================================================
  // Canvas Mouse & Interaction Events (Drag Piece / Pan Table / Select)
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
    // Middle click, Right click, or Space + Left click => Pan camera
    if (e.button === 1 || (e.button === 0 && state.spacePressed) || e.button === 2) {
      state.isPanning = true;
      state.hasPanned = false;
      state.panStart = { x: e.clientX, y: e.clientY };
      canvas.style.cursor = 'grabbing';
      e.preventDefault();
      return;
    }

    // Left click: either grab a piece to move it, or pan background
    if (e.button === 0) {
      const rect = canvas.getBoundingClientRect();
      const sx = e.clientX - rect.left;
      const sy = e.clientY - rect.top;
      const w = screenToWorld(sx, sy);

      const hit = findHitPiece(w.x, w.y);
      if (hit) {
        selectPiece(hit.id);
        state.draggingPieceId = hit.id;
        state.dragStartScreen = { x: e.clientX, y: e.clientY };
        state.dragStartWorld = { x: w.x, y: w.y };
        const curOff = getPieceOffset(hit.id);
        state.dragInitialOffset = { x: curOff.x, y: curOff.y };
        state.hasDraggedPiece = false;
        canvas.style.cursor = 'grabbing';
      } else {
        // Dragging on empty background pans the table smoothly
        state.isPanning = true;
        state.hasPanned = false;
        state.panStart = { x: e.clientX, y: e.clientY };
        canvas.style.cursor = 'grabbing';
      }
      renderCanvas();
    }
  });

  window.addEventListener('mousemove', (e) => {
    const rect = canvas.getBoundingClientRect();
    const sx = e.clientX - rect.left;
    const sy = e.clientY - rect.top;
    const w = screenToWorld(sx, sy);

    // 1. Moving / dragging a pattern piece on the table
    if (state.draggingPieceId) {
      const distPx = Math.hypot(
        e.clientX - state.dragStartScreen.x,
        e.clientY - state.dragStartScreen.y
      );
      if (distPx > 3) {
        state.hasDraggedPiece = true;
      }

      const dxMm = w.x - state.dragStartWorld.x;
      const dyMm = w.y - state.dragStartWorld.y;

      state.pieceOffsets[state.draggingPieceId] = {
        x: Math.round((state.dragInitialOffset.x + dxMm) * 10) / 10,
        y: Math.round((state.dragInitialOffset.y + dyMm) * 10) / 10,
      };

      lblCursorCoords.textContent = `X: ${w.x.toFixed(1)} mm, Y: ${w.y.toFixed(1)} mm (Przesuwanie elementu)`;
      renderCanvas();
      return;
    }

    // 2. Panning the camera / table
    if (state.isPanning) {
      const dx = e.clientX - state.panStart.x;
      const dy = e.clientY - state.panStart.y;
      if (Math.hypot(dx, dy) > 2) {
        state.hasPanned = true;
      }
      state.camera.x += dx;
      state.camera.y += dy;
      state.panStart = { x: e.clientX, y: e.clientY };
      renderCanvas();
      return;
    }

    // 3. Hover feedback & Coordinate indicator
    if (sx >= 0 && sx <= rect.width && sy >= 0 && sy <= rect.height) {
      lblCursorCoords.textContent = `X: ${w.x.toFixed(1)} mm, Y: ${w.y.toFixed(1)} mm`;
      const hit = findHitPiece(w.x, w.y);
      const hitId = hit ? hit.id : null;
      if (hitId !== state.hoveredPieceId) {
        state.hoveredPieceId = hitId;
        canvas.style.cursor = state.spacePressed ? 'grab' : hitId ? 'move' : 'crosshair';
        renderCanvas();
      }
    }
  });

  window.addEventListener('mouseup', (e) => {
    if (state.draggingPieceId) {
      // If user just clicked the piece without dragging it, select the closest segment
      if (!state.hasDraggedPiece && state.project) {
        const rect = canvas.getBoundingClientRect();
        const sx = e.clientX - rect.left;
        const sy = e.clientY - rect.top;
        const w = screenToWorld(sx, sy);
        const piece = state.project.pieces.find(p => p.id === state.draggingPieceId);
        if (piece) {
          const segIdx = findClosestSegmentIndex(piece, w.x, w.y);
          selectSegment(segIdx);
        }
      }
      state.draggingPieceId = null;
      canvas.style.cursor = state.hoveredPieceId ? 'move' : 'crosshair';
      renderCanvas();
    }

    if (state.isPanning) {
      state.isPanning = false;
      canvas.style.cursor = state.spacePressed ? 'grab' : state.hoveredPieceId ? 'move' : 'crosshair';
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
      canvas.style.cursor = state.hoveredPieceId ? 'move' : 'crosshair';
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

  document.getElementById('btn-focus-selected').addEventListener('click', () => {
    if (state.selectedPieceId) {
      focusOnPiece(state.selectedPieceId);
    } else {
      fitViewToProject();
    }
  });

  document.getElementById('btn-reset-view').addEventListener('click', () => {
    state.camera = { x: 100, y: 400, zoom: 1.0 };
    renderCanvas();
  });

  document.getElementById('btn-auto-arrange').addEventListener('click', () => {
    autoArrangePieces(true);
  });

  document.getElementById('btn-show-all-pieces').addEventListener('click', showAllPieces);
  document.getElementById('btn-isolate-selected').addEventListener('click', () => isolatePiece(state.selectedPieceId));
  document.getElementById('btn-sidebar-auto-arrange').addEventListener('click', () => autoArrangePieces(true));

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
      setProject(proj, true);
    } catch (err) {
      alert(`Błąd wczytywania projektu: ${err.message}`);
    } finally {
      hideLoading();
    }
  }

  function setProject(proj, rearrange = false) {
    state.project = proj;
    projectTitleEl.textContent = proj.project_name || 'Projekt CAD';

    // Check if pieces need initial grid arrangement so they don't stack at (0,0)
    const pieces = proj.pieces || [];
    const hasMissingOffsets = pieces.some(p => !(p.id in state.pieceOffsets));

    if (rearrange || hasMissingOffsets) {
      autoArrangePieces(false);
    }

    // Populate piece list sidebar
    renderPiecesList();

    // Select first piece if available and none selected
    if (pieces.length > 0) {
      if (!state.selectedPieceId || !pieces.find(p => p.id === state.selectedPieceId)) {
        selectPiece(pieces[0].id);
      } else {
        updateInspector();
      }
    } else {
      selectPiece(null);
    }

    if (rearrange) {
      fitViewToProject();
    } else {
      renderCanvas();
    }
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
    if (!state.project || !state.project.pieces) {
      piecesCountBadge.textContent = '0';
      return;
    }

    const totalCount = state.project.pieces.length;
    const visibleCount = state.project.pieces.filter(p => isPieceVisible(p.id)).length;
    piecesCountBadge.textContent = visibleCount === totalCount ? `${totalCount}` : `${visibleCount}/${totalCount}`;

    for (const p of state.project.pieces) {
      const b = getContourBounds(p.outer_contour);
      const wMm = b ? (b.maxX - b.minX).toFixed(1) : '-';
      const hMm = b ? (b.maxY - b.minY).toFixed(1) : '-';
      const visible = isPieceVisible(p.id);

      const card = document.createElement('div');
      card.className = `piece-card p-2.5 rounded-lg border border-slate-800 bg-slate-900/60 cursor-pointer transition ${
        p.id === state.selectedPieceId ? 'selected' : ''
      } ${!visible ? 'opacity-45' : ''}`;
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
        <div class="flex items-center justify-between gap-1">
          <span class="font-medium text-xs text-white truncate flex-1">${p.name}</span>
          <div class="flex items-center space-x-1 shrink-0">
            <button type="button" class="btn-piece-isolate p-1 rounded hover:bg-slate-700 text-slate-400 hover:text-sky-400 transition" title="Pokaż tylko ten wykrój (Izoluj)">
              <i data-lucide="crosshair" class="w-3.5 h-3.5"></i>
            </button>
            <button type="button" class="btn-piece-vis p-1 rounded hover:bg-slate-700 ${visible ? 'text-emerald-400' : 'text-slate-500'} transition" title="${visible ? 'Ukryj ten wykrój' : 'Pokaż ten wykrój'}">
              <i data-lucide="${visible ? 'eye' : 'eye-off'}" class="w-3.5 h-3.5"></i>
            </button>
            <span class="text-[10px] font-mono font-bold text-sky-400 bg-slate-800 px-1.5 py-0.5 rounded">x${p.quantity}</span>
          </div>
        </div>
        <div class="mt-1 flex items-center justify-between text-[11px] font-mono text-slate-400">
          <span>${wMm} x ${hMm} mm</span>
          <span class="text-amber-400 font-semibold">+${p.seam_allowance_mm || 0}mm</span>
        </div>
        ${badgesHtml ? `<div class="mt-1.5 flex flex-wrap gap-1">${badgesHtml}</div>` : ''}
      `;

      // Visibility toggle button
      const btnVis = card.querySelector('.btn-piece-vis');
      btnVis.addEventListener('click', (e) => {
        e.stopPropagation();
        togglePieceVisibility(p.id);
      });

      // Isolate button
      const btnIso = card.querySelector('.btn-piece-isolate');
      btnIso.addEventListener('click', (e) => {
        e.stopPropagation();
        isolatePiece(p.id);
      });

      // Clicking card selects piece (and makes it visible if it was hidden)
      card.addEventListener('click', () => {
        if (!isPieceVisible(p.id)) {
          state.hiddenPieceIds.delete(p.id);
          renderPiecesList();
        }
        selectPiece(p.id);
      });

      piecesListEl.appendChild(card);
    }

    lucide.createIcons();
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
      setProject(res.project, false);
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
      const idx = state.project.pieces.findIndex(p => p.id === state.selectedPieceId);
      if (idx !== -1) {
        state.project.pieces[idx] = res.piece;
        setProject(state.project, false);
      }
    } catch (err) {
      alert(`Błąd: ${err.message}`);
    } finally {
      hideLoading();
    }
  });

  btnUnfoldPiece.addEventListener('click', async () => {
    if (!state.selectedPieceId) return;
    const oldId = state.selectedPieceId;
    try {
      showLoading('Rozkładanie elementu na linii zgięcia...');
      const res = await apiCall(`/api/pieces/${oldId}/unfold`, 'POST');
      const idx = state.project.pieces.findIndex(p => p.id === oldId);
      if (idx !== -1) {
        if (res.piece.id !== oldId && state.pieceOffsets[oldId]) {
          state.pieceOffsets[res.piece.id] = state.pieceOffsets[oldId];
        }
        state.project.pieces[idx] = res.piece;
        state.selectedPieceId = res.piece.id;
        setProject(state.project, false);
      }
    } catch (err) {
      alert(`Błąd: ${err.message}`);
    } finally {
      hideLoading();
    }
  });

  btnMirrorPiece.addEventListener('click', async () => {
    if (!state.selectedPieceId) return;
    const oldId = state.selectedPieceId;
    try {
      showLoading('Odbicie lustrzane elementu...');
      const res = await apiCall(`/api/pieces/${oldId}/mirror`, 'POST');
      const idx = state.project.pieces.findIndex(p => p.id === oldId);
      if (idx !== -1) {
        if (res.piece.id !== oldId && state.pieceOffsets[oldId]) {
          state.pieceOffsets[res.piece.id] = state.pieceOffsets[oldId];
        }
        state.project.pieces[idx] = res.piece;
        state.selectedPieceId = res.piece.id;
        setProject(state.project, false);
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
          setProject(state.project, false);
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
        setProject(state.project, false);
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
        setProject(state.project, false);
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
      state.hiddenPieceIds.clear();
      state.pieceOffsets = {};
      const res = await apiCall(`/api/presets/${presetId}/load`, 'POST');
      setProject(res.project, true);
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
        resizeCanvas();
      } else if (tab === 'nesting') {
        document.getElementById('view-nesting').classList.remove('hidden');
        loadNestingPreview();
      } else if (tab === 'diagnostics') {
        document.getElementById('view-diagnostics').classList.remove('hidden');
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
      if (page.is_tiled && page.piece_name) {
        itemsSummary += `<p class="text-xs text-amber-400 font-medium">Kafelek elementu: ${page.piece_name}</p>`;
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
    if (roi.image) {
      layoutRoiCanvas();
      drawRoi();
    }
  });

  btnCloseDigitizerModal.addEventListener('click', () => digitizerModal.classList.add('hidden'));
  btnCancelDigitizer.addEventListener('click', () => digitizerModal.classList.add('hidden'));

  dropArea.addEventListener('click', () => digitizerFileInput.click());

  digitizerFileInput.addEventListener('change', (e) => {
    if (e.target.files && e.target.files[0]) {
      handleDigitizerFile(e.target.files[0]);
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
      handleDigitizerFile(e.dataTransfer.files[0]);
    }
  });

  function handleDigitizerFile(file) {
    selectedFileName.textContent = file.name;
    selectedFileName.classList.remove('hidden');
    loadRoiImage(file);
  }

  // =========================================================================
  // ROI Zone Editor (include / exclude lasso zones drawn on the uploaded image)
  // Zones are stored as polygons in normalized image coordinates (0..1).
  // =========================================================================

  const ROI_COLORS = {
    include: { fill: 'rgba(52, 211, 153, 0.28)', stroke: '#34d399' },
    exclude: { fill: 'rgba(248, 113, 113, 0.32)', stroke: '#f87171' },
  };
  const ROI_CLOSE_RADIUS_PX = 10;
  const ROI_FREEHAND_MIN_STEP_PX = 3;
  const ROI_ACTIVE_MODE_CLASSES = {
    include: ['bg-emerald-600/30', 'text-emerald-300'],
    exclude: ['bg-red-600/30', 'text-red-300'],
  };
  const ROI_ACTIVE_TOOL_CLASSES = ['bg-indigo-600/40', 'text-indigo-200'];
  const ROI_INACTIVE_CLASSES = ['bg-slate-800', 'text-slate-400'];

  const roi = {
    zones: [], // [{ mode: 'include' | 'exclude', points: [[nx, ny], ...] }]
    tool: 'polygon',
    mode: 'include',
    current: null, // in-progress polygon (normalized points)
    hover: null,
    freehandActive: false,
    rectStart: null,
    image: null,
    objectUrl: null,
    cssW: 0,
    cssH: 0,
  };

  function loadRoiImage(file) {
    if (roi.objectUrl) URL.revokeObjectURL(roi.objectUrl);
    roi.objectUrl = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      roi.image = img;
      roi.zones = [];
      roi.current = null;
      roi.hover = null;
      roiEditor.classList.remove('hidden');
      layoutRoiCanvas();
      renderRoiZoneList();
      drawRoi();
    };
    img.onerror = () => {
      roi.image = null;
      roiEditor.classList.add('hidden');
    };
    img.src = roi.objectUrl;
  }

  function layoutRoiCanvas() {
    if (!roi.image) return;
    const maxW = Math.max(240, roiStage.clientWidth || 640);
    const maxH = Math.max(200, Math.round(window.innerHeight * 0.5));
    const scale = Math.min(maxW / roi.image.naturalWidth, maxH / roi.image.naturalHeight, 2);
    roi.cssW = Math.max(1, Math.round(roi.image.naturalWidth * scale));
    roi.cssH = Math.max(1, Math.round(roi.image.naturalHeight * scale));
    const dpr = window.devicePixelRatio || 1;
    roiCanvas.width = Math.round(roi.cssW * dpr);
    roiCanvas.height = Math.round(roi.cssH * dpr);
    roiCanvas.style.width = `${roi.cssW}px`;
    roiCanvas.style.height = `${roi.cssH}px`;
    roiCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function roiPointFromEvent(e) {
    const rect = roiCanvas.getBoundingClientRect();
    const nx = (e.clientX - rect.left) / rect.width;
    const ny = (e.clientY - rect.top) / rect.height;
    return [Math.min(1, Math.max(0, nx)), Math.min(1, Math.max(0, ny))];
  }

  function roiToPx(pt) {
    return [pt[0] * roi.cssW, pt[1] * roi.cssH];
  }

  function roiDistPx(a, b) {
    const pa = roiToPx(a);
    const pb = roiToPx(b);
    return Math.hypot(pa[0] - pb[0], pa[1] - pb[1]);
  }

  function tracePolygon(points, close) {
    roiCtx.beginPath();
    points.forEach((p, i) => {
      const [x, y] = roiToPx(p);
      if (i === 0) roiCtx.moveTo(x, y);
      else roiCtx.lineTo(x, y);
    });
    if (close) roiCtx.closePath();
  }

  function drawRoi() {
    if (!roi.image) return;
    roiCtx.clearRect(0, 0, roi.cssW, roi.cssH);
    roiCtx.drawImage(roi.image, 0, 0, roi.cssW, roi.cssH);

    roi.zones.forEach((zone) => {
      const c = ROI_COLORS[zone.mode];
      tracePolygon(zone.points, true);
      roiCtx.fillStyle = c.fill;
      roiCtx.fill();
      roiCtx.lineWidth = 2;
      roiCtx.strokeStyle = c.stroke;
      roiCtx.stroke();
    });

    if (roi.current && roi.current.length > 0) {
      const c = ROI_COLORS[roi.mode];
      const pts = roi.current;
      const live = roi.tool === 'polygon' && roi.hover ? pts.concat([roi.hover]) : pts;

      if (roi.tool === 'rect' || roi.tool === 'freehand') {
        tracePolygon(pts, true);
        roiCtx.fillStyle = c.fill;
        roiCtx.fill();
      }
      tracePolygon(live, false);
      roiCtx.lineWidth = 2;
      roiCtx.strokeStyle = c.stroke;
      roiCtx.setLineDash(roi.tool === 'polygon' ? [6, 4] : []);
      roiCtx.stroke();
      roiCtx.setLineDash([]);

      if (roi.tool === 'polygon') {
        pts.forEach((p, i) => {
          const [x, y] = roiToPx(p);
          const nearStart = i === 0 && pts.length >= 3 && roi.hover && roiDistPx(roi.hover, p) <= ROI_CLOSE_RADIUS_PX;
          roiCtx.beginPath();
          roiCtx.arc(x, y, nearStart ? 7 : 4, 0, Math.PI * 2);
          roiCtx.fillStyle = nearStart ? '#ffffff' : c.stroke;
          roiCtx.fill();
          roiCtx.lineWidth = 1.5;
          roiCtx.strokeStyle = '#0f172a';
          roiCtx.stroke();
        });
      }
    }
  }

  function finishRoiZone() {
    if (roi.current && roi.current.length >= 3) {
      roi.zones.push({ mode: roi.mode, points: roi.current.map((p) => [p[0], p[1]]) });
      renderRoiZoneList();
    }
    roi.current = null;
    roi.freehandActive = false;
    roi.rectStart = null;
    drawRoi();
  }

  function cancelRoiCurrent() {
    roi.current = null;
    roi.freehandActive = false;
    roi.rectStart = null;
    drawRoi();
  }

  function rectCorners(a, b) {
    return [[a[0], a[1]], [b[0], a[1]], [b[0], b[1]], [a[0], b[1]]];
  }

  roiCanvas.addEventListener('pointerdown', (e) => {
    if (e.button !== 0 || !roi.image) return;
    e.preventDefault();
    const p = roiPointFromEvent(e);

    if (roi.tool === 'polygon') {
      if (!roi.current) {
        roi.current = [p];
      } else if (roi.current.length >= 3 && roiDistPx(p, roi.current[0]) <= ROI_CLOSE_RADIUS_PX) {
        finishRoiZone();
        return;
      } else if (roiDistPx(p, roi.current[roi.current.length - 1]) > 2) {
        roi.current.push(p);
      }
    } else if (roi.tool === 'freehand') {
      roi.current = [p];
      roi.freehandActive = true;
      roiCanvas.setPointerCapture(e.pointerId);
    } else if (roi.tool === 'rect') {
      roi.rectStart = p;
      roi.current = rectCorners(p, p);
      roiCanvas.setPointerCapture(e.pointerId);
    }
    drawRoi();
  });

  roiCanvas.addEventListener('pointermove', (e) => {
    if (!roi.image) return;
    const p = roiPointFromEvent(e);
    roi.hover = p;
    if (roi.tool === 'freehand' && roi.freehandActive && roi.current) {
      if (roiDistPx(p, roi.current[roi.current.length - 1]) >= ROI_FREEHAND_MIN_STEP_PX) {
        roi.current.push(p);
      }
    } else if (roi.tool === 'rect' && roi.rectStart) {
      roi.current = rectCorners(roi.rectStart, p);
    }
    drawRoi();
  });

  roiCanvas.addEventListener('pointerleave', () => {
    roi.hover = null;
    drawRoi();
  });

  roiCanvas.addEventListener('pointerup', () => {
    if (roi.tool === 'freehand' && roi.freehandActive) {
      roi.freehandActive = false;
      if (roi.current && roi.current.length >= 3) finishRoiZone();
      else cancelRoiCurrent();
    } else if (roi.tool === 'rect' && roi.rectStart) {
      const c = roi.current;
      const bigEnough = c && roiDistPx(c[0], c[1]) > 4 && roiDistPx(c[1], c[2]) > 4;
      if (bigEnough) finishRoiZone();
      else cancelRoiCurrent();
    }
  });

  roiCanvas.addEventListener('dblclick', (e) => {
    if (roi.tool === 'polygon' && roi.current && roi.current.length >= 3) {
      e.preventDefault();
      finishRoiZone();
    }
  });

  window.addEventListener('keydown', (e) => {
    if (digitizerModal.classList.contains('hidden') || !roi.current) return;
    if (e.key === 'Enter') {
      e.preventDefault();
      finishRoiZone();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      cancelRoiCurrent();
    } else if (e.key === 'Backspace' && roi.tool === 'polygon') {
      e.preventDefault();
      roi.current.pop();
      if (roi.current.length === 0) roi.current = null;
      drawRoi();
    }
  });

  function setRoiMode(mode) {
    roi.mode = mode;
    document.querySelectorAll('.roi-mode-btn').forEach((btn) => {
      const active = btn.dataset.roiMode === mode;
      Object.values(ROI_ACTIVE_MODE_CLASSES).flat().forEach((c) => btn.classList.remove(c));
      ROI_INACTIVE_CLASSES.forEach((c) => btn.classList.remove(c));
      (active ? ROI_ACTIVE_MODE_CLASSES[btn.dataset.roiMode] : ROI_INACTIVE_CLASSES).forEach((c) => btn.classList.add(c));
    });
    drawRoi();
  }

  function setRoiTool(tool) {
    cancelRoiCurrent();
    roi.tool = tool;
    document.querySelectorAll('.roi-tool-btn').forEach((btn) => {
      const active = btn.dataset.roiTool === tool;
      ROI_ACTIVE_TOOL_CLASSES.concat(ROI_INACTIVE_CLASSES).forEach((c) => btn.classList.remove(c));
      (active ? ROI_ACTIVE_TOOL_CLASSES : ROI_INACTIVE_CLASSES).forEach((c) => btn.classList.add(c));
    });
  }

  document.querySelectorAll('.roi-mode-btn').forEach((btn) => {
    btn.addEventListener('click', () => setRoiMode(btn.dataset.roiMode));
  });
  document.querySelectorAll('.roi-tool-btn').forEach((btn) => {
    btn.addEventListener('click', () => setRoiTool(btn.dataset.roiTool));
  });

  document.getElementById('btn-roi-undo').addEventListener('click', () => {
    if (roi.current) {
      cancelRoiCurrent();
    } else {
      roi.zones.pop();
      renderRoiZoneList();
      drawRoi();
    }
  });

  document.getElementById('btn-roi-clear').addEventListener('click', () => {
    roi.zones = [];
    roi.current = null;
    renderRoiZoneList();
    drawRoi();
  });

  function renderRoiZoneList() {
    const inc = roi.zones.filter((z) => z.mode === 'include').length;
    const exc = roi.zones.length - inc;
    roiSummary.textContent = roi.zones.length ? `${inc} × szukaj · ${exc} × ignoruj` : 'cały obraz';
    roiZoneList.innerHTML = '';
    roi.zones.forEach((zone, idx) => {
      const li = document.createElement('li');
      li.className = 'flex items-center justify-between bg-slate-800/60 border border-slate-700 rounded px-2 py-1';

      const label = document.createElement('span');
      label.className = zone.mode === 'include' ? 'text-emerald-300' : 'text-red-300';
      label.textContent = `${zone.mode === 'include' ? '🟢 Szukaj tutaj' : '🔴 Ignoruj'} — strefa ${idx + 1} (${zone.points.length} pkt)`;

      const actions = document.createElement('span');
      actions.className = 'flex items-center space-x-2';

      const toggle = document.createElement('button');
      toggle.type = 'button';
      toggle.className = 'text-slate-400 hover:text-white';
      toggle.textContent = 'Przełącz';
      toggle.addEventListener('click', () => {
        zone.mode = zone.mode === 'include' ? 'exclude' : 'include';
        renderRoiZoneList();
        drawRoi();
      });

      const remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'text-slate-400 hover:text-red-400';
      remove.textContent = 'Usuń';
      remove.addEventListener('click', () => {
        roi.zones.splice(idx, 1);
        renderRoiZoneList();
        drawRoi();
      });

      actions.append(toggle, remove);
      li.append(label, actions);
      roiZoneList.appendChild(li);
    });
  }

  window.addEventListener('resize', () => {
    if (roi.image && !digitizerModal.classList.contains('hidden')) {
      layoutRoiCanvas();
      drawRoi();
    }
  });

  digitizerForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const file = digitizerFileInput.files[0];
    if (!file) {
      alert('Wybierz plik graficzny wykroju (PNG lub JPG)!');
      return;
    }
    if (roi.current) finishRoiZone();

    const seamMode = document.getElementById('digitizer-seam-mode').value;
    const seamMm = parseFloat(document.getElementById('digitizer-seam-mm').value);
    const autoUnfold = document.getElementById('digitizer-auto-unfold').checked;
    const bezierError = parseFloat(document.getElementById('digitizer-bezier-error').value);
    const segMode = document.getElementById('digitizer-segmentation-mode').value;

    const formData = new FormData();
    formData.append('file', file);
    if (roi.zones.length > 0) {
      formData.append('zones', JSON.stringify(roi.zones.map((z) => ({ mode: z.mode, points: z.points }))));
    }

    digitizerModal.classList.add('hidden');
    showLoading('Potok percepcji: odzyskiwanie skali, segmentacja i wektoryzacja Béziera...');

    try {
      const url = `/api/digitize?seam_mode=${seamMode}&seam_allowance_mm=${seamMm}&auto_unfold=${autoUnfold}&max_bezier_error_mm=${bezierError}&segmentation_mode=${segMode}`;
      const res = await apiCall(url, 'POST', formData);
      state.hiddenPieceIds.clear();
      state.pieceOffsets = {};
      setProject(res.project, true);
      alert(`Sukces! Zdygitalizowano ${res.pieces_count} elementów wykroju.`);
    } catch (err) {
      digitizerModal.classList.remove('hidden');
      layoutRoiCanvas();
      drawRoi();
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
